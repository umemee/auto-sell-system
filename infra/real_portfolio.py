#infra/real_portfolio.py
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
        self.unsettled_sell_amount = 0.0       # D+2 미결제 매도대금 합계 ($)
        self.closed_trades_today = []          # 당일 청산 거래 목록
        self.last_sync_removed_positions = {}  # 최근 동기화 시 매도 감지된 포지션 백업
        
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
                        
                        # 고점 갱신 로직 (기존 유지)
                        if current_price > self.positions[ticker].get('highest_price', 0):
                            self.positions[ticker]['highest_price'] = current_price

                    else:
                        # 로컬에 없던 신규 종목 (API에는 있는데 로컬엔 없는 경우)
                        # 이 경우 정확한 매수 시점을 알 수 없으므로, '현재 시간'을 기준으로 잡거나 비워둡니다.
                        # 여기서는 보수적으로 '현재 시간'을 넣어 타임 컷이 바로 발동되지 않게 합니다.
                        now_et = datetime.datetime.now(pytz.timezone('US/Eastern'))
                        
                        self.positions[ticker] = {
                            'ticker': ticker,
                            'qty': qty,
                            'entry_price': entry_price,
                            'current_price': current_price,
                            'eval_value': eval_amt,
                            'pnl_pct': pnl_pct,
                            'highest_price': current_price,
                            'entry_time': now_et # ✨ [추가] 초기화
                        }
                    
                    current_stock_value += eval_amt

            # 3. 사라진 종목 처리 (매도 완료 감지)
            # 로컬에는 있었는데 API 목록(api_tickers)에 없다면 -> 매도된 것임
            self.last_sync_removed_positions.clear()
            local_tickers = list(self.positions.keys())
            for ticker in local_tickers:
                if ticker not in api_tickers:
                    self.logger.info(f"🗑️ [Sync] Position Removed detected: {ticker}")
                    self.last_sync_removed_positions[ticker] = dict(self.positions[ticker])
                    del self.positions[ticker]
                    self.ban_list.add(ticker) # [Cool-down] 금일 재매수 금지 등록

            # 4. 체결기준 총 자산 가치 업데이트 (증권사 D+2 지연 대금 포함)
            self.total_equity = self.balance + current_stock_value + self.unsettled_sell_amount

            # 로그 출력 (선택 사항)
            # self._log_status()

        except Exception as e:
            self.logger.error(f"❌ [Sync Fail] Portfolio Sync Failed: {e}")
            # 동기화 실패 시 로컬 상태 유지 (삭제하지 않음)

    def register_realized_sale(self, ticker, qty, sell_price, entry_price, reason='SELL', fee_rate=0.001):
        """
        [Trade-Date Settlement]
        매도 체결 즉시 실현손익 및 D+2 미결제 매도대금을 로컬에 반영.
        증권사 D+2 예수금 지연 입금으로 인한 자산 증발 왜곡을 100% 방어함.
        """
        gross_proceeds = sell_price * qty
        fee = gross_proceeds * fee_rate
        net_proceeds = gross_proceeds - fee
        total_cost = entry_price * qty
        pnl = net_proceeds - total_cost
        ret_pct = ((sell_price - entry_price) / entry_price * 100.0) if entry_price > 0 else 0.0

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
        
        # 체결기준 총자산 즉시 재계산 (매도된 종목은 평가액에서 즉시 제외)
        current_val = sum(
            p['qty'] * p.get('current_price', p.get('entry_price', 0.0))
            for t, p in self.positions.items()
            if t != ticker
        )
        self.total_equity = self.balance + current_val + self.unsettled_sell_amount

        self.logger.info(
            f"📈 [Trade Realized] {ticker} | PnL: ${pnl:+,.2f} ({ret_pct:+.2f}%) | "
            f"금일 누적: ${self.daily_realized_pnl:+,.2f} | 미결제대금: ${self.unsettled_sell_amount:,.2f}"
        )
        return record

    def daily_reset(self):
        """[Daily Reset] 자정/세션 시작 시 당일 손익 초기화"""
        self.daily_realized_pnl = 0.0
        self.unsettled_sell_amount = 0.0
        self.closed_trades_today.clear()
        self.ban_list.clear()
        self.logger.info("🔄 [RealPortfolio] 일일 실현손익 및 미결제대금 초기화 완료")

    def recover_from_log(self, log_path=None, today_str=None):
        """
        [장중 재시작 복구력] trade.log를 파싱하여 당일 실현손익과 미결제대금을 복구
        """
        import re
        from pathlib import Path
        
        if log_path is None:
            log_path = Path(__file__).resolve().parent.parent / "logs" / "trade.log"
        log_path = Path(log_path)
        
        if not log_path.exists():
            return 0
            
        if today_str is None:
            today_str = datetime.datetime.now(pytz.timezone('US/Eastern')).strftime("%Y-%m-%d")

        recovered_pnl = 0.0
        recovered_unsettled = 0.0
        recovered_count = 0
        last_unsettled = None
        
        pattern_realized = re.compile(
            r'\[(\d{4}-\d{2}-\d{2})\s[\d:]+\].*?📈\s+\[Trade Realized\]\s+(\S+)\s+\|\s+PnL:\s+\$([+\-\d\.,]+).*?미결제대금:\s+\$([+\-\d\.,]+)'
        )
        pattern_sell_fill = re.compile(
            r'\[(\d{4}-\d{2}-\d{2})\s[\d:]+\].*?🔴\s+\[.*?체결\]\s+(\S+).*?수량:\s+(\d+)주\s+\|\s+체결가:\s+\$([\d\.]+).*?손익:\s+\$([+\-\d\.,]+)'
        )

        try:
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    m1 = pattern_realized.search(line)
                    if m1:
                        log_date, sym, pnl_str, unsettled_str = m1.groups()
                        if log_date == today_str:
                            pnl_val = float(pnl_str.replace(',', ''))
                            unsettled_val = float(unsettled_str.replace(',', ''))
                            recovered_pnl += pnl_val
                            last_unsettled = unsettled_val
                            recovered_count += 1
                            continue

                    m2 = pattern_sell_fill.search(line)
                    if m2 and last_unsettled is None:
                        log_date, sym, qty_str, price_str, pnl_str = m2.groups()
                        if log_date == today_str:
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

    def has_open_slot(self):
        """빈 슬롯 확인 (Double Engine)"""
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

        self.ban_list.add(ticker)

        current_val = sum(
            p['qty'] * p.get('current_price', p.get('entry_price', 0.0))
            for p in self.positions.values()
        )
        self.total_equity = self.balance + current_val + self.unsettled_sell_amount

        return removed

    def get_max_order_amount(self):
        """
        [Double Engine 자금 관리 - Fixed for Market Order]
        목표: 전체 자산의 50% 베팅 (단, 현금 범위 내에서)
        수정: 시장가 주문(+5% 할증)을 고려하여 현금 버퍼를 2% -> 10%로 확대
        """
        # 1. 현재 슬롯 확인 (이미 꽉 찼으면 0 반환)
        if len(self.positions) >= self.MAX_SLOTS:
            return 0.0

        # 2. 1슬롯당 목표 금액 계산 (총 자산 / 2)
        target_amount = self.total_equity / self.MAX_SLOTS
        
        # 3. [옵션 A] 1회 주문 최대 한도 Hard Cap ($2,000)
        cap = getattr(Config, 'MAX_SINGLE_ORDER_AMOUNT', 2000.0)
        capped_target = min(target_amount, cap) if (cap is not None and cap > 0) else target_amount

        # 4. [안전 장치] 주문 가능 현금의 90% (수수료 + 시장가 할증 5% 커버)
        safe_cash = self.balance * 0.90 
        
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

    def calculate_qty(self, price, ticker=None):
        """
        [주문 수량 계산 & $2,000 Hard Cap 적용 추적]
        현재 가용 자금과 목표 투자 비중을 고려하여 주문할 수량을 계산합니다.
        캡 발동으로 수량이 축소된 경우 sizing_cap_log.csv에 별도 기록.
        """
        if price <= 0:
            return 0
            
        # 1. 캡 미적용 시 원본 목표 금액 및 수량
        uncapped_target = self.total_equity / max(1, self.MAX_SLOTS)
        safe_cash = self.balance * 0.90
        uncapped_order_amt = min(uncapped_target, safe_cash)
        uncapped_qty = int(uncapped_order_amt / price) if uncapped_order_amt >= 20 else 0

        # 2. 캡 적용 금액 및 최종 수량
        final_amount = self.get_max_order_amount()
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
            
            # 🕒 [Time Cut] 현재 미국 시간 기록
            now_et = datetime.datetime.now(pytz.timezone('US/Eastern'))

            # [수정 1] VIVS 사태 방지: 기존 데이터가 있으면 삭제 후 덮어쓰기 (강제 초기화)
            if ticker in self.positions:
                self.logger.warning(f"⚠️ [Data Clean] {ticker} 기존 데이터 삭제 후 재진입")
                del self.positions[ticker]

            # [수정 2] 신규 데이터 생성 (평단가 = 현재 매수가로 고정)
            self.positions[ticker] = {
                'ticker': ticker,
                'qty': qty,
                'entry_price': price,        # 진입가 확실하게 기록
                'current_price': price,
                'eval_value': cost,
                'pnl_pct': 0.0,
                'highest_price': price, 
                'entry_time': now_et         # 진입 시간 기록
            }
            
            self.logger.info(f"✅ [Local Update] BUY {ticker} ({qty}주 @ ${price}) | Balance: ${self.balance:.2f}")
            
        elif fill['type'] == 'SELL':
            # [수정 3] 수수료(0.2% 가정)를 뗀 금액만 예수금에 반영하여 '자금 부족' 방지
            revenue = (qty * price) * 0.998 
            self.balance += revenue
            
            if ticker in self.positions:
                del self.positions[ticker]
                self.ban_list.add(ticker) # 매도 시 즉시 밴 리스트 추가
                
                self.logger.info(f"👋 [Local Update] SELL {ticker} -> Added to Ban List | Balance: ${self.balance:.2f}")
                
                # [필수] 주문 직후 총 자산(Equity) 재계산
                current_val = sum(p['qty'] * p['current_price'] for p in self.positions.values())
                self.total_equity = self.balance + current_val

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
    
    # [신규 추가] 외부(main.py)에서 호출할 잔고 강제 동기화 함수
    def sync_balance(self):
        """API를 통해 예수금만 강제 동기화 (매도 직후 사용)"""
        if self.is_paper:
            return
        try:
            # get_buyable_cash는 kis_api에 구현되어 있어야 함
            cash = self.kis.get_buyable_cash() 
            if cash > 0:
                old_balance = self.balance
                self.balance = float(cash)
                self.logger.info(f"💰 [Sync] 잔고 갱신 완료: ${old_balance:.2f} -> ${self.balance:.2f}")
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
