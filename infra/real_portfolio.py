#infra/real_portfolio.py
import time
import logging
from config import Config
import datetime
import pytz

class RealPortfolio:
    """
    [RealPortfolio V2.2 - Paper Trading & Virtual Portfolio Integrated]
    - EXECUTION_MODE == 'PAPER_TRADING_ONLY' 시 가상 예수금($10,000) 및 가상 포지션 관리
    - 실제 증권사 계좌 및 자금 영향 0% 보장
    """

    def __init__(self, kis_api):
        self.logger = logging.getLogger("RealPortfolio")
        self.kis = kis_api

        # 🚨 페이퍼 트레이딩 모드 여부
        self.is_paper = getattr(Config, 'IS_PAPER_TRADING', False)

        # ----------------------------------------------------
        # 📊 Dynamic State (변동 데이터)
        # ----------------------------------------------------
        # 페이퍼 모드 시 가상 시작 예수금($10,000) 할당
        self.balance = getattr(Config, 'VIRTUAL_INITIAL_BALANCE', 10000.0) if self.is_paper else 0.0
        self.total_equity = self.balance
        
        # 💵 [Trade-Date Settlement] 체결기준 정산 및 당일 실현손익 추적
        self.daily_realized_pnl = 0.0          # 당일 누적 실현손익 ($)
        self.initial_seed_today = 0.0          # [지시 6] 당일 장 시작 기준 예수금 ($)
        self.unsettled_sell_amount = 0.0       # D+2 미결제 매도대금 합계 ($)
        self.closed_trades_today = []          # 당일 청산 거래 목록
        self.last_sync_removed_positions = {}  # 최근 동기화 시 매도 감지된 포지션 백업

        # [A3] 잔고 미반영 의심 감지 플래그 및 매도 추적 변수
        self.is_balance_unconfirmed = False
        self._sale_in_progress = False
        self._sale_in_progress_time = 0.0
        self._balance_before_sale = 0.0
        self._pending_sale_proceeds = 0.0
        
        # [작업 1 & 2] 세션 시작 총자산, 정산 지연 여부 및 봇 관리 종목 영구 집합
        self.session_start_equity = 0.0        # 17:00 프리마켓 첫 하트비트 시 래치된 총자산
        self.is_delayed_settlement = False     # 3회 재조회 후에도 KIS 미반영 시 True
        self.bot_managed_tickers = set()       # 봇이 진입한 종목 코드 집합 (수동 포지션 오분류 방지)
        
        # Positions Dictionary
        # { 'TICKER': { 'qty': 10, 'entry_price': 100, 'highest_price': 120, ... } }
        self.positions = {} 
        
        # [NEW] 금일 매매 금지(Cool-down) 리스트 (Set 구조)
        self.ban_list = set()

        # ----------------------------------------------------
        # ⚙️ Static Rules (불변 규칙)
        # ----------------------------------------------------
        self.MAX_SLOTS = getattr(Config, 'MAX_SLOTS', 2)
        self.SLOT_RATIO = 0.5       
        self.MIN_ORDER_AMT = 20.0   

    def sync_with_kis(self):
        """
        [Smart Sync Logic] 
        API 잔고를 가져오되, 로컬의 중요 정보(highest_price)는 보존하는 병합 로직
        - 페이퍼 모드 시 실제 증권사 계좌를 건드리지 않고 로컬 가상 포지션 시세만 갱신
        """
        if self.is_paper:
            # 🛡️ 페이퍼 모드: 가상 포지션 현재가 및 평가액 갱신
            current_stock_value = 0.0
            for ticker, pos in list(self.positions.items()):
                try:
                    cur_price = self.kis.get_current_price(ticker)
                    if cur_price and cur_price > 0:
                        pos['current_price'] = cur_price
                        pos['eval_value'] = cur_price * pos['qty']
                        pos['pnl_pct'] = ((cur_price - pos['entry_price']) / pos['entry_price'] * 100.0) if pos['entry_price'] > 0 else 0.0
                        if cur_price > pos.get('highest_price', 0):
                            pos['highest_price'] = cur_price
                    current_stock_value += pos.get('eval_value', pos['qty'] * pos['entry_price'])
                except Exception as e:
                    self.logger.warning(f"⚠️ [Paper Sync] {ticker} 시세 갱신 실패: {e}")
                    current_stock_value += pos.get('eval_value', pos['qty'] * pos['entry_price'])
            self.total_equity = self.balance + current_stock_value
            return

        try:
            # 1. 자산(예수금) 조회
            # TTTS3007R (주문 가능 금액) 사용 -> 미수 발생 방지
            buying_power = self.kis.get_buyable_cash()
            self.balance = float(buying_power)
            # [작업 C] sync_with_kis에서도 예수금 갱신 즉시 pending_sale_proceeds 해제 판정
            self._check_and_release_pending_sale()

            # 2. 보유 종목 API 조회
            holdings = self.kis.get_balance() # List[Dict] 반환
            
            # API에서 확인된 종목 코드 집합 (동기화 비교용)
            api_tickers = set()
            current_stock_value = 0.0

            if holdings:
                for item in holdings:
                    ticker = item['symbol']
                    qty = float(item['qty']) # 소수점 수량 대비 float
                    
                    if qty <= 0: continue # 잔여 찌꺼기 데이터 무시
                    
                    api_tickers.add(ticker)

                    # API 데이터 추출
                    avg_unit_price = float(item.get('price', 0.0))  # 매입 평단가 (Unit Price)
                    pnl_pct = float(item.get('pnl_pct', 0.0))       # 수익률(%)
                    
                    # 수량이 정수가 아니라면 정수 처리
                    qty = int(qty)

                    # [수정] 1. 현재가 계산 (평단가 * 수익률 적용)
                    # 수익률이 반영된 '현재 1주당 가격'을 구합니다.
                    current_price = avg_unit_price * (1.0 + pnl_pct / 100.0)

                    # [수정] 2. 평가 금액 계산 (현재가 * 보유수량)
                    # 비로소 '총 평가 금액'이 제대로 계산됩니다.
                    eval_amt = current_price * qty
                    
                    # 진입가 역산 (평단가가 정확하다면 avg_unit_price와 같음)
                    entry_price = avg_unit_price
                    
                    # API 수익률 기반 진입가 역산 (API 평단가가 부정확할 경우 대비)
                    if (1 + pnl_pct/100.0) != 0:
                        entry_price = current_price / (1 + pnl_pct/100.0)
                    else:
                        entry_price = current_price

                    # [핵심] 기존 정보 병합 (Merge)
                    if ticker in self.positions:
                        # 🕒 [Time Cut] 기존에 기록된 진입 시간 가져오기
                        cached_entry_time = self.positions[ticker].get('entry_time')

                        # 이미 로컬에 있는 종목 -> highest_price 및 entry_time 유지
                        self.positions[ticker].update({
                            'qty': qty,
                            'current_price': current_price,
                            'eval_value': eval_amt,
                            'pnl_pct': pnl_pct,
                            'entry_price': entry_price, # 👈 [핵심 추가] 실제 증권사 평단가로 덮어쓰기!
                            'entry_time': cached_entry_time # ✨ [추가] API 동기화 시 시간 정보 보존
                        })
                        if not self.positions[ticker].get('exchange') and item.get('exchange'):
                            self.positions[ticker]['exchange'] = item.get('exchange')
                        
                        # 고점 갱신 로직 (기존 유지)
                        if current_price > self.positions[ticker].get('highest_price', 0):
                            self.positions[ticker]['highest_price'] = current_price

                    else:
                        # 로컬에 없던 신규 종목 (API에는 있는데 로컬엔 없는 경우)
                        now_et = datetime.datetime.now(pytz.timezone('US/Eastern'))
                        restored_meta = self.last_sync_removed_positions.get(ticker, {})
                        strat_name = restored_meta.get('strategy_name', restored_meta.get('strategy', 'EMA'))
                        
                        is_bot = (ticker in self.bot_managed_tickers) or restored_meta.get('is_bot_managed', False)

                        self.positions[ticker] = {
                            'ticker': ticker,
                            'qty': qty,
                            'entry_price': entry_price,
                            'current_price': current_price,
                            'eval_value': eval_amt,
                            'pnl_pct': pnl_pct,
                            'highest_price': current_price,
                            'entry_time': restored_meta.get('entry_time', now_et),
                            'strategy': strat_name,
                            'strategy_name': strat_name,
                            'is_bot_managed': is_bot,
                            'is_manual': not is_bot,
                            'alpha_id': restored_meta.get('alpha_id', ''),
                            'target_price': restored_meta.get('target_price', 0.0),
                            'time_cut_minutes': restored_meta.get('time_cut_minutes', 45 if strat_name == 'ALPHA' else 0),
                            'tp_pct': restored_meta.get('tp_pct', getattr(Config, 'ALPHA_TP_PCT', 0.035) if strat_name == 'ALPHA' else getattr(Config, 'TARGET_PROFIT_PCT', 0.07)),
                            'sl_pct': restored_meta.get('sl_pct', -0.10),
                            'exchange': item.get('exchange') or restored_meta.get('exchange', 'NASD')
                        }
                    
                    current_stock_value += eval_amt

            # 3. 사라진 종목 처리 (매도 완료 감지)
            # 로컬에는 있었는데 API 목록(api_tickers)에 없다면 -> 매도된 것임
            self.last_sync_removed_positions.clear()
            local_tickers = list(self.positions.keys())
            now_for_grace = datetime.datetime.now(pytz.timezone('US/Eastern'))
            for ticker in local_tickers:
                if ticker not in api_tickers:
                    pos = self.positions[ticker]
                    entry_t = pos.get('entry_time')
                    # 🛡️ [매수 직후 원장 미반영 유예 가드 - 60초]
                    if entry_t:
                        entry_t_cmp = entry_t
                        if hasattr(entry_t_cmp, 'tzinfo') and entry_t_cmp.tzinfo is None:
                            entry_t_cmp = pytz.timezone('US/Eastern').localize(entry_t_cmp)
                        elapsed_sec = (now_for_grace - entry_t_cmp).total_seconds()
                        if 0 <= elapsed_sec < 60:
                            self.logger.info(f"⏳ [Sync Grace] {ticker} 최근 매수({elapsed_sec:.1f}초 전) 원장 미반영 대기 중 -> 삭제 유예")
                            continue

                    self.logger.info(f"🗑️ [Sync] Position Removed detected: {ticker}")
                    self.last_sync_removed_positions[ticker] = dict(self.positions[ticker])
                    del self.positions[ticker]
                    self.ban_list.add(ticker) # [Cool-down] 금일 재매수 금지 등록
                    self.bot_managed_tickers.discard(ticker)

            # 4. 체결기준 총 자산 가치 업데이트 (증권사 D+2 지연 대금 이중합산 제거)
            self.total_equity = self.get_effective_balance() + current_stock_value

            # 로그 출력 (선택 사항)
            # self._log_status()

        except Exception as e:
            self.logger.error(f"❌ [Sync Fail] Portfolio Sync Failed: {e}")
            # 동기화 실패 시 로컬 상태 유지 (삭제하지 않음)

    def _check_and_release_pending_sale(self):
        """매도 체결 후 잔고 반영 확인 및 해제 (sync_balance, sync_with_kis 공통, 120초 타임아웃 지원)"""
        if not getattr(self, '_sale_in_progress', False):
            return

        now_t = time.time()
        # 1) 120초 타임아웃 초과 시 강제 해제 (작업 C 추가조건 2)
        elapsed_t = now_t - getattr(self, '_sale_in_progress_time', now_t)
        if elapsed_t > 120.0:
            self.logger.warning(
                f"⏰ [_sale_in_progress 타임아웃 해제 ({elapsed_t:.1f}초 > 120초)] "
                f"pending_sale_proceeds(${self._pending_sale_proceeds:,.2f}) 강제 해제"
            )
            self._sale_in_progress = False
            self._pending_sale_proceeds = 0.0
            self.is_delayed_settlement = False
            self.is_balance_unconfirmed = False
            return

        # 2) 예수금에 매도대금이 정상 반영되었는지 확인 (직전 balance + pending의 50% 이상 증가)
        min_expected = getattr(self, '_balance_before_sale', 0.0) + (getattr(self, '_pending_sale_proceeds', 0.0) * 0.5)
        if self.balance >= min_expected:
            self.logger.info(
                f"✅ [잔고 반영 완료] balance(${self.balance:,.2f}) >= 최소기대치(${min_expected:,.2f}) "
                f"-> pending_sale_proceeds 정상 해제"
            )
            self.is_balance_unconfirmed = False
            self.is_delayed_settlement = False
            self._sale_in_progress = False
            self._pending_sale_proceeds = 0.0
        else:
            self.logger.warning(
                f"⚠️ [잔고 미반영 대기] balance(${self.balance:,.2f}) < 최소기대치(${min_expected:,.2f})"
            )
            self.is_balance_unconfirmed = True

    def confirm_post_sell_balance(self, old_balance: float, net_proceeds: float):
        """
        [작업 2] 실전 매도 후 예수금 반영 대기 및 재조회 엔진
        - 매도 직후 1.5초 대기 후 KIS 예수금을 재조회
        - 잔고가 늘지 않았으면 1초 간격으로 최대 3회 재조회
        - 그래도 늘지 않았을 때만 _pending_sale_proceeds를 가산하고 is_delayed_settlement=True, "(반영 지연 가능)" 표기
        - 잔고가 정상 증가했으면 _pending_sale_proceeds는 0 유지 및 is_delayed_settlement=False
        """
        if self.is_paper:
            return

        time.sleep(1.5)
        raw_cash = self.kis.get_buyable_cash()
        try:
            new_cash = float(raw_cash)
        except (TypeError, ValueError):
            new_cash = float(old_balance)
        min_expected = old_balance + (net_proceeds * 0.5)

        increased = (new_cash >= min_expected) or (new_cash > old_balance + 1.0)
        
        if not increased:
            for retry_i in range(1, 4):
                time.sleep(1.0)
                raw_retry = self.kis.get_buyable_cash()
                try:
                    new_cash = float(raw_retry)
                except (TypeError, ValueError):
                    new_cash = float(old_balance)
                if (new_cash >= min_expected) or (new_cash > old_balance + 1.0):
                    increased = True
                    self.logger.info(f"✅ [매도 후 잔고 확인 성공] 재시도 #{retry_i} KIS 예수금 반영 확인: ${new_cash:,.2f}")
                    break
                else:
                    self.logger.warning(f"⏳ [매도 후 잔고 대기] 재시도 #{retry_i}/3 KIS 예수금 미증가 (${new_cash:,.2f} <= ${old_balance:,.2f})")

        self.balance = float(new_cash)
        if increased:
            self._pending_sale_proceeds = 0.0
            self._sale_in_progress = False
            self.is_delayed_settlement = False
            self.is_balance_unconfirmed = False
            self.logger.info(f"✅ [매도 후 예수금 즉시 반영] KIS 잔고 ${self.balance:,.2f} (old: ${old_balance:,.2f})")
        else:
            self._pending_sale_proceeds = float(net_proceeds)
            self._sale_in_progress = True
            self._sale_in_progress_time = time.time()
            self._balance_before_sale = float(old_balance)
            self.is_delayed_settlement = True
            self.is_balance_unconfirmed = True
            self.logger.warning(
                f"⚠️ [매도 후 예수금 지연] 3회 재조회 후에도 KIS 미반영 -> pending_sale_proceeds(${net_proceeds:,.2f}) 가산 (반영 지연 가능)"
            )

        # 총자산 재계산
        current_val = sum(
            p['qty'] * p.get('current_price', p.get('entry_price', 0.0))
            for p in self.positions.values()
        )
        pending = self._pending_sale_proceeds if self.is_delayed_settlement else 0.0
        self.total_equity = self.balance + pending + current_val

    def register_realized_sale(self, ticker, qty, sell_price, entry_price, reason='SELL', fee_rate=0.001, is_broker_execution=False):
        """
        [Trade-Date Settlement]
        매도 체결 즉시 실현손익 및 D+2 미결제 매도대금을 로컬에 반영.
        증권사 D+2 예수금 지연 입금으로 인한 자산 증발 왜곡을 100% 방어함.
        - is_broker_execution=True (브로커 선주문 익절): 원장에 이미 반영되었으므로 pending 등록 생략
        """
        gross_proceeds = sell_price * qty
        fee = gross_proceeds * fee_rate
        net_proceeds = gross_proceeds - fee
        total_cost = entry_price * qty
        pnl = net_proceeds - total_cost
        ret_pct = ((sell_price - entry_price) / entry_price * 100.0) if entry_price > 0 else 0.0

        # [C2] 매도 정산 대조용 INFO 로그
        self.logger.info(
            f"🧾 [매도 정산 대조] 종목: {ticker} | 순매도대금(봇 계산): ${net_proceeds:,.2f} | 호출 직전 balance: ${self.balance:,.2f}"
        )

        # [A2] 원응답 로깅 (register_realized_sale 호출 시 [순매도대금, 호출 직전 balance])
        self.logger.debug(
            f"🔍 [register_realized_sale DEBUG] 순매도대금: ${net_proceeds:,.2f} | 호출 직전 balance: ${self.balance:,.2f}"
        )

        # [작업 2] pending_sale_proceeds는 confirm_post_sell_balance에서 3회 재조회 후에도 미반영 시에만 가산
        if is_broker_execution:
            self.logger.info(f"ℹ️ [{ticker}] 브로커 지정가 체결 확인 -> pending_sale_proceeds 가산 생략 (이중계산 방지)")

        self.daily_realized_pnl += pnl
        if not self.is_paper:
            self.unsettled_sell_amount += net_proceeds

        record = {
            'ticker': ticker,
            'qty': int(qty),
            'entry_price': round(entry_price, 4),
            'sell_price': round(sell_price, 4),
            'pnl': round(pnl, 2),
            'return_pct': round(ret_pct, 2),
            'reason': reason,
            'time': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self.closed_trades_today.append(record)
        
        # 체결기준 총자산 즉시 재계산 (매도된 종목은 평가액에서 즉시 제외, 체결기준 유효 가용금액 반영)
        current_val = sum(
            p['qty'] * p.get('current_price', p.get('entry_price', 0.0))
            for t, p in self.positions.items()
            if t != ticker
        )
        pending = self._pending_sale_proceeds if getattr(self, 'is_delayed_settlement', False) else 0.0
        self.total_equity = float(self.balance) + pending + current_val

        self.logger.info(
            f"📈 [Trade Realized] {ticker} | PnL: ${pnl:+,.2f} ({ret_pct:+.2f}%) | "
            f"금일 누적: ${self.daily_realized_pnl:+,.2f} | 미결제대금: ${self.unsettled_sell_amount:,.2f}"
        )
        return record

    def daily_reset(self):
        """[Daily Reset] 자정/세션 시작 시 당일 손익 초기화"""
        self.daily_realized_pnl = 0.0
        self.initial_seed_today = 0.0
        self.session_start_equity = 0.0
        self.unsettled_sell_amount = 0.0
        self.is_delayed_settlement = False
        self.is_balance_unconfirmed = False
        self._sale_in_progress = False
        self._pending_sale_proceeds = 0.0
        self.bot_managed_tickers.clear()
        self.closed_trades_today.clear()
        self.ban_list.clear()
        self.logger.info("🔄 [RealPortfolio] 일일 실현손익 및 미결제대금 초기화 완료")

    def validate_and_apply_state(self, saved_date: str, daily_pnl: float, unsettled: float, saved_seed: float = 0.0, session_start_equity: float = 0.0, bot_managed_tickers: list = None) -> bool:
        """
        [지시 1-2 & 작업 2 안전장치] system_state.json 로드 시, 저장된 trade_date가 현재 거래일 키와 다르면
        어제 손익 및 미결제 대금을 복원하지 않고 즉시 daily_reset()을 호출합니다.
        거래일이 일치하면 daily_pnl, unsettled, initial_seed_today, session_start_equity, bot_managed_tickers를 복원합니다.
        """
        from infra.utils import get_trade_date_key
        current_trade_date = get_trade_date_key()
        if not saved_date or saved_date != current_trade_date:
            self.logger.warning(
                f"📅 [거래일 불일치 감지] 저장일자({saved_date}) != 현재 거래일({current_trade_date}) "
                f"-> 어제 손익/미결제 복원 차단 및 daily_reset() 자동 집행"
            )
            self.daily_reset()
            return False

        self.daily_realized_pnl = float(daily_pnl)
        self.unsettled_sell_amount = float(unsettled)
        self.initial_seed_today = float(saved_seed)
        if session_start_equity and float(session_start_equity) > 0:
            self.session_start_equity = float(session_start_equity)
        if bot_managed_tickers:
            self.bot_managed_tickers.update(bot_managed_tickers)
        self.logger.info(
            f"🔄 [당일 상태 복원] 거래일: {saved_date} | 손익: ${self.daily_realized_pnl:+,.2f} | "
            f"미결제: ${self.unsettled_sell_amount:,.2f} | 시작시드: ${self.initial_seed_today:,.2f} | "
            f"세션시작총자산: ${self.session_start_equity:,.2f} | 봇관리종목: {list(self.bot_managed_tickers)}"
        )
        return True

    def recover_from_log(self, log_path=None, today_str=None):
        """
        [장중 재시작 복구력] trade.log를 파싱하여 당일 거래일 키 기준 실현손익과 미결제대금을 복구
        - 로그의 KST 타임스탬프를 Asia/Seoul로 localize 후 get_trade_date_key()로 변환하여 대조
        - '[LIVE] [YYYY-MM-DD HH:MM:SS]' 및 '[LIVE] YYYY-MM-DD HH:MM:SS,mmm' 두 가지 형식 모두 지원
        """
        import re
        from pathlib import Path
        from infra.utils import get_trade_date_key
        
        if log_path is None:
            log_path = Path(__file__).resolve().parent.parent / "logs" / "trade.log"
        log_path = Path(log_path)
        
        if not log_path.exists():
            return 0
            
        target_trade_date = today_str if today_str is not None else get_trade_date_key()
        kst_tz = pytz.timezone('Asia/Seoul')

        recovered_pnl = 0.0
        recovered_unsettled = 0.0
        recovered_count = 0
        last_unsettled = None
        
        pattern_realized = re.compile(
            r'\[LIVE\]\s+(?:\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\]|(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}))(?:,\d+)?.*?'
            r'📈\s+\[Trade Realized\]\s+(\S+)\s+\|\s+PnL:\s+\$([+\-\d\.,]+).*?미결제대금:\s+\$([+\-\d\.,]+)'
        )
        pattern_sell_fill = re.compile(
            r'\[LIVE\]\s+(?:\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\]|(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}))(?:,\d+)?.*?'
            r'🔴\s+\[.*?체결\]\s+(\S+).*?수량:\s+(\d+)주\s+\|\s+체결가:\s+\$([\d\.]+).*?손익:\s+\$([+\-\d\.,]+)'
        )

        try:
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    m1 = pattern_realized.search(line)
                    if m1:
                        ts1, ts2, sym, pnl_str, unsettled_str = m1.groups()
                        ts_str = ts1 or ts2
                        dt_naive = datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                        dt_aware = kst_tz.localize(dt_naive)
                        line_trade_date = get_trade_date_key(dt_aware)
                        
                        if line_trade_date == target_trade_date:
                            pnl_val = float(pnl_str.replace(',', ''))
                            unsettled_val = float(unsettled_str.replace(',', ''))
                            recovered_pnl += pnl_val
                            last_unsettled = unsettled_val
                            recovered_count += 1
                            continue

                    m2 = pattern_sell_fill.search(line)
                    if m2 and last_unsettled is None:
                        ts1, ts2, sym, qty_str, price_str, pnl_str = m2.groups()
                        ts_str = ts1 or ts2
                        dt_naive = datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                        dt_aware = kst_tz.localize(dt_naive)
                        line_trade_date = get_trade_date_key(dt_aware)
                        
                        if line_trade_date == target_trade_date:
                            pnl_val = float(pnl_str.replace(',', ''))
                            qty_val = float(qty_str)
                            price_val = float(price_str)
                            recovered_pnl += pnl_val
                            recovered_unsettled += (price_val * qty_val * 0.999)
                            recovered_count += 1

            if last_unsettled is not None:
                recovered_unsettled = last_unsettled

            if recovered_count > 0:
                self.daily_realized_pnl = round(recovered_pnl, 2)
                if not self.is_paper:
                    self.unsettled_sell_amount = round(recovered_unsettled, 2)
                self.logger.info(
                    f"🔄 [Log Recovery] trade.log로부터 {recovered_count}건의 당일 매매 복구 완료 | "
                    f"금일 누적 손익: ${self.daily_realized_pnl:+,.2f} | 미결제대금: ${self.unsettled_sell_amount:,.2f}"
                )
        except Exception as e:
            self.logger.warning(f"⚠️ [Log Recovery] trade.log 파싱 중 오류: {e}")
            
        return recovered_count

    def has_open_slot(self, strategy=None):
        """
        [공유자본풀 슬롯 및 단일 슬롯 가드]
        - Alpha: 단일 슬롯. 이미 Alpha 보유 중이거나 EMA가 2슬롯 사용 중이면 진입 불가.
        - EMA: 슬롯 0.5. Alpha 보유 중(가용자본 0)이거나 EMA가 2슬롯 사용 중이면 진입 불가.
        - strategy 미지정: Alpha 보유 중이면 진입 불가, 아니면 전체 슬롯 개수 확인.
        """
        has_alpha = any(
            p.get('strategy') == 'ALPHA' or p.get('strategy_name') == 'ALPHA'
            for p in self.positions.values()
        )
        ema_count = sum(
            1 for p in self.positions.values()
            if p.get('strategy') != 'ALPHA' and p.get('strategy_name') != 'ALPHA'
        )

        if strategy == 'ALPHA':
            if has_alpha:
                return False
            return ema_count < self.MAX_SLOTS

        if strategy == 'EMA':
            if has_alpha:
                return False
            return ema_count < self.MAX_SLOTS

        # strategy 미지정 (하위 호환)
        if has_alpha:
            return False
        return len(self.positions) < self.MAX_SLOTS

    def is_holding(self, ticker):
        """특정 종목 보유 여부"""
        return ticker in self.positions

    def is_banned(self, ticker):
        """[NEW] 금일 매매 금지 종목 확인"""
        return ticker in self.ban_list

    def get_position(self, ticker):
        """특정 종목 포지션 정보 반환"""
        return self.positions.get(ticker)

    def close_position(self, ticker):
        """
        [Live Sell Cleanup]
        브로커 측 매도 주문 성공 직후 로컬 포지션 상태만 정리한다.
        현금 반영은 이후 KIS 동기화가 맡고, 여기서는 중복 매도/재매수 방지용 상태만 맞춘다.
        """
        removed = ticker in self.positions

        if removed:
            del self.positions[ticker]
            self.logger.info(f"📕 [Local Close] Removed sold position: {ticker}")
        else:
            self.logger.info(f"📕 [Local Close] Position already absent: {ticker}")

        self.bot_managed_tickers.discard(ticker)
        self.ban_list.add(ticker)

        current_val = sum(
            p['qty'] * p.get('current_price', p.get('entry_price', 0.0))
            for p in self.positions.values()
        )
        pending = self._pending_sale_proceeds if getattr(self, 'is_delayed_settlement', False) else 0.0
        self.total_equity = float(self.balance) + pending + current_val

        return removed

    def get_max_order_amount(self, capital_frac=None, strategy=None):
        """
        [Double Engine 자금 관리 - Fixed for Market Order & Alpha Sweep]
        목표: 전체 자산의 50% 베팅 (EMA) 또는 잔여 가용자본 스윕 (Alpha, 최대 100%)
        수정: 시장가 주문(+5% 할증)을 고려하여 현금 버퍼를 2% -> 10%로 확대
        """
        # 1. 현재 슬롯 확인 (공유자본풀 및 Alpha 단일 슬롯 가드)
        if not self.has_open_slot(strategy=strategy):
            return 0.0

        ema_count = sum(
            1 for p in self.positions.values()
            if p.get('strategy') != 'ALPHA' and p.get('strategy_name') != 'ALPHA'
        )

        # 2. 목표 금액 계산
        if capital_frac is not None:
            target_amount = self.total_equity * float(capital_frac)
        elif strategy == 'ALPHA':
            # Alpha 단일 슬롯: 남은 가용 자본 비율 (EMA 0개면 1.0, EMA 1개면 0.5)
            slot_ratio = max(0.0, 1.0 - (ema_count * 0.5))
            target_amount = self.total_equity * slot_ratio
        else:
            target_amount = self.total_equity / self.MAX_SLOTS
        
        # 3. [옵션 A] 1회 주문 최대 한도 Hard Cap ($2,000)
        cap = getattr(Config, 'MAX_SINGLE_ORDER_AMOUNT', 2000.0)
        capped_target = min(target_amount, cap) if (cap is not None and cap > 0) else target_amount

        # 4. [안전 장치] 주문 가능 현금의 90% (수수료 + 시장가 할증 5% 커버)
        # [작업 2-2] 주문 한도 계산에는 pending을 더하지 않은 순수 KIS 예수금만 사용
        available_cash = float(self.balance)
        safe_cash = available_cash * 0.90 
        
        # 5. 최종 주문 금액 (둘 중 작은 값)
        final_amount = min(capped_target, safe_cash)
        
        # 최소 주문 금액 ($20 미만은 주문 안 함)
        if final_amount < 20:
            return 0.0
            
        return final_amount

    def _log_sizing_cap(self, record: dict):
        """
        [포워드 리스크 추적] $2,000 Hard Cap 발동 거래 전용 CSV 로깅 (sizing_cap_log.csv)
        """
        import csv
        from pathlib import Path
        try:
            base_dir = Path(__file__).resolve().parent.parent
            log_dir = base_dir / "logs" / "live"
            log_dir.mkdir(parents=True, exist_ok=True)
            
            file_path = log_dir / "sizing_cap_log.csv"
            file_exists = file_path.exists()
            
            with open(file_path, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=record.keys(), extrasaction='ignore')
                if not file_exists or file_path.stat().st_size == 0:
                    writer.writeheader()
                writer.writerow(record)
        except Exception as e:
            self.logger.error(f"⚠️ sizing_cap_log.csv 기록 실패: {e}")

    def calculate_qty(self, price, ticker=None, strategy=None, capital_frac=None):
        """
        [주문 수량 계산 & $2,000 Hard Cap 적용 추적]
        현재 가용 자금과 목표 투자 비중을 고려하여 주문할 수량을 계산합니다.
        캡 발동으로 수량이 축소된 경우 sizing_cap_log.csv에 별도 기록.
        """
        if price <= 0:
            return 0
            
        # 1. 캡 미적용 시 원본 목표 금액 및 수량
        if capital_frac is not None:
            uncapped_target = self.total_equity * float(capital_frac)
        elif strategy == 'ALPHA':
            has_alpha = any(
                p.get('strategy') == 'ALPHA' or p.get('strategy_name') == 'ALPHA'
                for p in self.positions.values()
            )
            ema_count = sum(
                1 for p in self.positions.values()
                if p.get('strategy') != 'ALPHA' and p.get('strategy_name') != 'ALPHA'
            )
            if has_alpha or ema_count >= self.MAX_SLOTS:
                return 0
            slot_ratio = max(0.0, 1.0 - (ema_count * 0.5))
            uncapped_target = self.total_equity * slot_ratio
        else:
            uncapped_target = self.total_equity / max(1, self.MAX_SLOTS)

        # [작업 2-2] 주문 수량 계산에는 pending을 더하지 않은 순수 KIS 예수금만 사용
        safe_cash = float(self.balance) * 0.90
        uncapped_order_amt = min(uncapped_target, safe_cash)
        uncapped_qty = int(uncapped_order_amt / price) if uncapped_order_amt >= 20 else 0

        # 2. 캡 적용 금액 및 최종 수량
        final_amount = self.get_max_order_amount(capital_frac=capital_frac, strategy=strategy)
        if final_amount < 20:
            return 0

        qty = int(final_amount / price)
        if qty < 1:
            return 0

        # 3. 캡 발동 여부 감지 및 로깅 (실제 수량 축소 발생 시)
        cap = getattr(Config, 'MAX_SINGLE_ORDER_AMOUNT', 2000.0)
        is_capped = (uncapped_qty > qty) or (uncapped_target > cap and (cap is not None and cap > 0))
        if is_capped and uncapped_qty > qty:
            reduced_qty = uncapped_qty - qty
            self.logger.warning(
                f"🛡️ [Sizing Cap Applied] {ticker or 'ORDER'}: "
                f"총자산 ${self.total_equity:,.0f} -> 원본 ${uncapped_target:,.2f}({uncapped_qty}주) "
                f"-> $2,000 캡 적용 ${final_amount:,.2f}({qty}주, -{reduced_qty}주 축소)"
            )
            self._log_sizing_cap({
                'timestamp_et': datetime.datetime.now(pytz.timezone('America/New_York')).strftime("%Y-%m-%d %H:%M:%S"),
                'ticker': ticker or 'UNKNOWN',
                'price': price,
                'total_equity': round(self.total_equity, 2),
                'uncapped_target': round(uncapped_target, 2),
                'capped_target': round(final_amount, 2),
                'uncapped_qty': uncapped_qty,
                'capped_qty': qty,
                'reduced_qty': reduced_qty,
                'is_capped': True
            })

        return qty
    def update_position(self, fill):
        """
        [호환성 래퍼] RealOrderManager가 호출하는 메서드명 맞춤
        내부적으로 update_local_after_order를 호출합니다.
        """
        # fill 딕셔너리에 'time'이 없으면 현재 시간 추가 (안전장치)
        if 'time' not in fill:
            fill['time'] = datetime.datetime.now(pytz.timezone('US/Eastern'))
            
        return self.update_local_after_order(fill)
    
    def update_local_after_order(self, fill):
        """
        [Optimistic Update]
        주문 직후 API 반영 전, 로컬 상태를 선제적으로 업데이트하여
        중복 주문 방지 및 반응 속도 향상
        """
        ticker = fill['ticker']
        qty = int(fill['qty'])
        price = float(fill['price'])
        
        if fill['type'] == 'BUY':
            cost = qty * price
            self.balance -= cost
            self.bot_managed_tickers.add(ticker)
            
            # 🕒 [Time Cut] 현재 미국 시간 기록
            now_et = datetime.datetime.now(pytz.timezone('US/Eastern'))

            # [수정 1] VIVS 사태 방지: 기존 데이터가 있으면 삭제 후 덮어쓰기 (강제 초기화)
            if ticker in self.positions:
                self.logger.warning(f"⚠️ [Data Clean] {ticker} 기존 데이터 삭제 후 재진입")
                del self.positions[ticker]

            # [수정 2] 신규 데이터 생성 (평단가 = 현재 매수가로 고정)
            strat_name = fill.get('strategy_name', fill.get('strategy', 'EMA'))
            self.positions[ticker] = {
                'ticker': ticker,
                'qty': qty,
                'entry_price': price,        # 진입가 확실하게 기록
                'current_price': price,
                'eval_value': cost,
                'pnl_pct': 0.0,
                'highest_price': price, 
                'entry_time': now_et,        # 진입 시간 기록
                'strategy': strat_name,      # 전략 식별 키 ('EMA' vs 'ALPHA')
                'strategy_name': strat_name,  # 전략 식별 메타데이터 태깅
                'is_bot_managed': True,
                'is_manual': False,
                'alpha_id': fill.get('alpha_id', ''),
                'time_cut_minutes': fill.get('time_cut_minutes', fill.get('max_hold_min', getattr(Config, 'ALPHA_TIME_CUT_MINUTES', 45) if strat_name == 'ALPHA' else 0)),
                'tp_pct': fill.get('tp_pct', getattr(Config, 'ALPHA_TP_PCT', 0.035) if strat_name == 'ALPHA' else getattr(Config, 'TARGET_PROFIT_PCT', 0.07)),
                'sl_pct': fill.get('sl_pct', getattr(Config, 'ALPHA_SL_PCT', -0.10) if strat_name == 'ALPHA' else -0.10),
                'exchange': fill.get('exchange', 'NASD')
            }
            
            self.logger.info(f"✅ [Local Update] BUY {ticker} ({qty}주 @ ${price}) [{strat_name}] | Balance: ${self.balance:.2f}")
            
        elif fill['type'] == 'SELL':
            # [수정 3] 수수료(0.2% 가정)를 뗀 금액만 예수금에 반영하여 '자금 부족' 방지
            revenue = (qty * price) * 0.998 
            self.balance += revenue
            self.bot_managed_tickers.discard(ticker)
            
            if ticker in self.positions:
                del self.positions[ticker]
                self.ban_list.add(ticker) # 매도 시 즉시 밴 리스트 추가
                
                self.logger.info(f"👋 [Local Update] SELL {ticker} -> Added to Ban List | Balance: ${self.balance:.2f}")
                
                # [필수] 주문 직후 총 자산(Equity) 재계산
                current_val = sum(p['qty'] * p['current_price'] for p in self.positions.values())
                pending = self._pending_sale_proceeds if getattr(self, 'is_delayed_settlement', False) else 0.0
                self.total_equity = self.balance + pending + current_val

    def update_highest_price(self, ticker, current_price):
        """
        [Backtest Logic 이식] 트레일링 스탑을 위한 고가 갱신
        """
        if ticker in self.positions:
            # 기존 고가보다 현재가가 높으면 갱신
            if current_price > self.positions[ticker]['highest_price']:
                old_high = self.positions[ticker]['highest_price']
                self.positions[ticker]['highest_price'] = current_price
                # (선택) 로그가 너무 많으면 주석 처리 가능
                # self.logger.info(f"📈 [{ticker}] 고가 갱신: ${old_high} -> ${current_price}")
    
    def get_effective_balance(self) -> float:
        """
        [가용 현금 반환]
        - 3회 재조회 후에도 KIS 원장 미반영(is_delayed_settlement=True)된 경우에만 pending_sale_proceeds 가산
        - 평상시 및 브로커 익절 시에는 순수 KIS balance 반환
        """
        if self.is_paper:
            return float(self.balance)
            
        if getattr(self, 'is_delayed_settlement', False) and getattr(self, '_pending_sale_proceeds', 0.0) > 0:
            return float(self.balance + self._pending_sale_proceeds)
            
        return float(self.balance)

    def get_account_pnl(self) -> float:
        """
        [작업 2-4] 계좌 기준 당일 손익 (현재 총자산 - 세션 시작 총자산)
        """
        if getattr(self, 'session_start_equity', 0.0) > 0:
            return float(self.total_equity - self.session_start_equity)
        return 0.0

    def _maybe_latch_initial_seed(self):
        """
        [작업 2-2 래치 조건 강화]
        - 아직 당일 매매가 시작되지 않은 깨끗한 상태(시드 미설정, 잔고>0, 포지션 0개, 당일 손익 0)일 때만
          현재 잔고를 당일 시작 시드로 래치 고정.
        - 장중 포지션 보유 중이거나 손익 발생 상태에서의 재시작 시에는 래치하지 않고 0.0 유지.
        """
        if (getattr(self, 'initial_seed_today', 0.0) == 0.0 and 
            self.balance > 0 and 
            len(self.positions) == 0 and 
            getattr(self, 'daily_realized_pnl', 0.0) == 0.0):
            self.initial_seed_today = self.balance
            self.logger.info(f"🌱 [RealPortfolio] 당일 시작 기준 예수금 고정: ${self.initial_seed_today:,.2f}")

    # [신규 추가] 외부(main.py)에서 호출할 잔고 강제 동기화 함수
    def sync_balance(self, wait_sec: float = 0.0):
        """API를 통해 예수금만 강제 동기화 (매도 직후 사용, 증권사 전산 지연 완충 지원)"""
        if self.is_paper:
            self._maybe_latch_initial_seed()
            return
        if wait_sec > 0:
            import time
            time.sleep(wait_sec)
        try:
            # get_buyable_cash는 kis_api에 구현되어 있어야 함
            old_balance = self.balance
            cash = self.kis.get_buyable_cash() 
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            raw_fields = getattr(self.kis, 'last_buyable_cash_raw', {})
            
            # [A2] 원응답 로깅
            self.logger.debug(
                f"🔍 [sync_balance DEBUG] 시각: {now_str} | 직전 balance: ${old_balance:.2f} | "
                f"파싱된 balance: ${cash:.2f} | API 원응답 금액 필드: {raw_fields}"
            )

            if cash > 0:
                self.balance = float(cash)
                # [작업 2-2] 최초 1회 당일 시작 기준 예수금 고정 보존 (포지션 0개, 당일 손익 0일 때만 래치)
                self._maybe_latch_initial_seed()
                self.logger.info(f"💰 [Sync] 잔고 갱신 완료: ${old_balance:.2f} -> ${self.balance:.2f}")

            # [A3 / 작업 C] 매도 체결 후 잔고 미반영 의심 감지 및 120초 타임아웃 해제
            self._check_and_release_pending_sale()

            # 체결기준 총자산 최신화
            current_stock_val = sum(
                p['qty'] * p.get('current_price', p.get('entry_price', 0.0))
                for p in self.positions.values()
            )
            self.total_equity = self.get_effective_balance() + current_stock_val
        except Exception as e:
            self.logger.error(f"❌ 잔고 동기화 실패: {e}")
    
    def _log_status(self):
        """현재 상태 로그 출력 (디버깅용)"""
        pos_str = ", ".join([f"{k}({v.get('pnl_pct',0):.1f}%)" for k, v in self.positions.items()])
        if not pos_str: pos_str = "None"
        
        self.logger.info(
            f"💰 Equity: ${self.total_equity:,.0f} | "
            f"Cash: ${self.balance:,.0f} | "
            f"Slots: {len(self.positions)}/{self.MAX_SLOTS} | "
            f"Holding: [{pos_str}] | "
            f"Ban List: {len(self.ban_list)}"
        )
