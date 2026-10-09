# infra/real_order_manager.py
import time
import datetime
import requests
from config import Config
from infra.utils import get_logger, round_price
from infra.paper_execution_engine import VirtualExecutionEngine

class RealOrderManager:
    """
    [Real Order Manager V3.2 - Paper Trading & Virtual Execution Integrated]
    - EXECUTION_MODE == 'PAPER_TRADING_ONLY' 시 가상 체결 엔진(VirtualExecutionEngine) 자동 위임
    - 실계좌 주문 API 호출 100% 원천 차단
    """
    APBK2623_CANCEL_GUARD_SECONDS = 60

    def __init__(self, kis_api):
        self.kis = kis_api
        self.logger = get_logger("OrderManager")
        
        # 🚨 페이퍼 트레이딩 모드 여부 및 가상 체결 엔진 초기화
        self.is_paper = getattr(Config, 'IS_PURE_PAPER', getattr(Config, 'IS_PAPER_TRADING', False))
        self.virtual_engine = VirtualExecutionEngine(kis_api)
        
        # 🛡️ [로그 폭탄 방지] 종목별 마지막 로그 시간 기록부
        self.log_throttle_map = {} 
        self.apbk2623_cancel_guard = {}
        self.selling_in_progress = set()  # [작업 I-1] 종목별 매도 진행 중 잠금 플래그
        self.recent_order_time = {}       # [작업 I-3] 종목별 최근 발주 시간 (워치독 30초 유예용)

    def _log_signal_spread(self, ticker, signal_price, ask, bid, ask_vol, bid_vol):
        """
        [Data Enhancement] 시그널 발생 찰나의 호가창 스냅샷을 CSV로 기록
        """
        import csv
        import pytz
        from pathlib import Path
        import datetime
        try:
            # 로그 저장 폴더 생성 (logs/spread_analysis)
            base_dir = Path(__file__).resolve().parent.parent
            log_dir = base_dir / "logs" / "spread_analysis"
            log_dir.mkdir(parents=True, exist_ok=True)
            
            # 날짜별로 파일 분리 (미국 시간 기준)
            now_et = datetime.datetime.now(pytz.timezone('US/Eastern'))
            date_str = now_et.strftime("%Y%m%d")
            file_path = log_dir / f"signal_spreads_{date_str}.csv"
            
            file_exists = file_path.exists()
            
            # 스프레드 퍼센트 계산
            spread_pct = ((ask - bid) / bid * 100) if bid > 0 else 0
            
            # 기록할 데이터 한 줄 조립
            row = {
                "timestamp_et": now_et.strftime("%Y-%m-%d %H:%M:%S"),
                "ticker": ticker,
                "signal_price": round(signal_price, 4) if signal_price else 0,
                "ask_price": ask,
                "bid_price": bid,
                "ask_vol": ask_vol,
                "bid_vol": bid_vol,
                "spread_pct": round(spread_pct, 3)
            }
            
            # CSV 파일에 한 줄 이어쓰기 (Append)
            with open(file_path, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=row.keys())
                if not file_exists:
                    writer.writeheader()
                writer.writerow(row)
                
        except Exception as e:
            self.logger.error(f"⚠️ 스프레드 데이터 수집 실패: {e}")

    def execute_buy(self, portfolio, signal):
        """
        [매수 집행] 나스닥 전용 시장가 진입 + 호가 스냅샷 CSV 기록
        """
        ticker = signal['ticker']
        price = signal.get('price', 0) 

        # ============================================================
        # 🛡️ [Safety Protocol] 1. 스프레드 및 호가 잔량 체크 (나스닥 NAS 기준)
        # ============================================================
        try:
            ask, bid, ask_vol, bid_vol = self.kis.get_market_spread(ticker, exchange="NAS")
            
            # 💡 시그널 발생 찰나의 호가창 스냅샷 기록
            self._log_signal_spread(ticker, price, ask, bid, ask_vol, bid_vol)

            if bid <= 0:
                if price > 0:
                    self.logger.warning(f"⚠️ [Liquidity] {ticker} 호가(Bid) 0 발견 -> 전략가(${price})로 대체하여 강제 진입")
                    bid = price
                    ask = price
                else:
                    self.logger.warning(f"📉 [MISS] {ticker} 매수 잔량 없음 (Bid:0, Last:0) -> 진입 불가")
                    return None

            spread = (ask - bid) / bid if bid > 0 else 0
            
            # 허용 스프레드 3.0%
            if spread > 0.03:
                last_log = self.log_throttle_map.get(ticker, 0)
                now = time.time()
                if now - last_log > 60:
                    self.logger.warning(
                        f"📉 [MISS] {ticker} 스프레드({spread*100:.2f}%) 과다로 매수 포기 "
                        f"| Price: {bid}(Bid) vs {ask}(Ask) "
                        f"| Vol: {bid_vol} vs {ask_vol}"
                    )
                    self.log_throttle_map[ticker] = now
                return None

        except Exception as e:
            self.logger.error(f"⚠️ 스프레드 체크 중 오류({ticker}): {e}")
            return None

        # ============================================================
        # 🛡️ [Anti-FOMO Buffer] 시그널 가격 대비 허용 버퍼(+0.5% ~ +1.0%) 초과 추격 매수 원천 차단
        # ============================================================
        buy_slippage_buffer = float(getattr(Config, 'BUY_SLIPPAGE_BUFFER', 0.005))
        if price > 0:
            max_allowed_buy_price = price * (1.0 + buy_slippage_buffer)
            if ask <= 0:
                self.logger.warning(f"🚫 [Anti-FOMO Reject] {ticker} 유효 매수 호가 부재 (Ask: {ask}) -> 매수 차단")
                return None

            if ask > max_allowed_buy_price:
                self.logger.warning(
                    f"🚫 [Anti-FOMO Reject] {ticker} 매수 호가 과열 이탈 "
                    f"(Ask ${ask:.4f} > 허용상한 ${max_allowed_buy_price:.4f}, +{buy_slippage_buffer*100:.1f}% 초과) -> 매수 차단"
                )
                return None

        # ============================================================
        # 2. 쿨다운 체크
        # ============================================================
        if portfolio.is_banned(ticker):
            self.logger.warning(f"🚫 [Buy Reject] 금일 매매 금지 종목 ({ticker})")
            return None

        # ============================================================
        # 3. 수량 계산
        # ============================================================
        strat_name = signal.get('strategy_name', signal.get('strategy', 'EMA'))
        cap_frac = signal.get('capital_frac')
        qty = portfolio.calculate_qty(price, ticker=ticker, strategy=strat_name, capital_frac=cap_frac)
        if qty <= 0:
            return {'status': 'failed', 'msg': f"❌ 잔고 부족 또는 수량 계산 실패 ({ticker})"}

        # ============================================================
        # 4. 주문 전송 (페이퍼 모드 분기 및 원천 차단 가드)
        # ============================================================
        is_pure_paper = getattr(Config, 'IS_PURE_PAPER', self.is_paper)
        if is_pure_paper:
            resp = self.virtual_engine.execute_paper_buy(
                ticker=ticker,
                qty=qty,
                signal_price=price,
                exchange="NAS"
            )
        else:
            assert not is_pure_paper, "CRITICAL GUARD: Pure paper mode must NEVER call send_order"
            resp = self.kis.send_order(
                ticker=ticker,
                side="BUY",
                qty=qty,
                price=price,        
                order_type="MARKET",
                exchange="NAS"
            )
        
        # ============================================================
        # 5. 결과 처리
        # ============================================================
        if resp and resp.get('rt_cd') == '0':
            output_dict = resp.get('output', {}) if isinstance(resp.get('output'), dict) else {}
            entry_guess = output_dict.get('fill_price', price)
            odno = output_dict.get('ODNO', 'Unknown')
            filled_exch = output_dict.get('exchange') or getattr(self.kis, 'last_order_exchange', 'NASD')

            try:
                portfolio.update_position({
                    'ticker': ticker,
                    'qty': qty,
                    'price': entry_guess,
                    'entry_price': entry_guess,
                    'exchange': filled_exch,
                    'strategy': strat_name,
                    'strategy_name': strat_name,
                    'alpha_id': signal.get('alpha_id', ''),
                    'time_cut_minutes': signal.get('time_cut_minutes', signal.get('max_hold_min', getattr(Config, 'ALPHA_TIME_CUT_MINUTES', 45) if strat_name == 'ALPHA' else 0)),
                    'tp_pct': signal.get('tp_pct', getattr(Config, 'ALPHA_TP_PCT', 0.035) if strat_name == 'ALPHA' else getattr(Config, 'TARGET_PROFIT_PCT', 0.07)),
                    'sl_pct': signal.get('sl_pct', getattr(Config, 'ALPHA_SL_PCT', -0.10) if strat_name == 'ALPHA' else -0.10),
                    'type': 'BUY',
                    'time': datetime.datetime.now()
                })
            except Exception as e:
                self.logger.error(f"❌ 포트폴리오 업데이트 실패: {e}")
            
            if self.is_paper:
                mode_str = "[PAPER]"
            else:
                env_mode = getattr(Config, 'EXECUTION_ENVIRONMENT', '')
                mode_str = "[SHADOW]" if env_mode == 'DUAL_SHADOW' else "[REAL]"

            is_alpha_strat = (strat_name == 'ALPHA')
            tp_pct = getattr(Config, 'ALPHA_TP_PCT', 0.035) if is_alpha_strat else getattr(Config, 'TARGET_PROFIT_PCT', 0.07)
            target_price = round_price(entry_guess * (1.0 + tp_pct)) if entry_guess > 0 else 0.0

            from infra.utils import format_buy_fill_message
            rem_cash = getattr(portfolio, 'balance', 0.0)
            msg = format_buy_fill_message(
                strategy=strat_name,
                ticker=ticker,
                qty=qty,
                price=entry_guess,
                target_price=target_price,
                tp_pct=tp_pct,
                cash=rem_cash,
                is_paper=self.is_paper
            )
            self.recent_order_time[ticker] = time.time()
            return {'status': 'success', 'msg': msg, 'qty': qty, 'avg_price': entry_guess}
        else:
            fail_msg = resp.get('msg1', '알 수 없는 오류') if resp else '응답 없음'
            return {'status': 'failed', 'msg': f"❌ 매수 실패 ({ticker}): {fail_msg}"}

    def _calculate_dynamic_stop_buffer(self, ticker, price, exchange="NAS"):
        """
        [수정안 2] 최근 1분봉 레인지(ATR) 및 호가 스프레드 기반 동적 손절 지정가 버퍼 계산
        - 목표: 저유동성 종목의 불필요한 -5% 시장가 슬리피지 방지
        - 최근 1분봉 레인지 비율 ((High - Low) / Open)의 50%를 1단계 지정가 버퍼로 적용
        - 클램프 범위: 최소 0.5% ~ 최대 2.0%
        """
        dynamic_buffer = 0.010  # 기본값 1.0%
        try:
            df = self.kis.get_minute_candles(exchange, ticker, limit=5)
            if df is not None and not df.empty and len(df) >= 2:
                recent_candle = df.iloc[-1]
                high = float(recent_candle.get('high', 0))
                low = float(recent_candle.get('low', 0))
                open_p = float(recent_candle.get('open', 0))
                if open_p > 0 and high >= low:
                    candle_range_ratio = (high - low) / open_p
                    dynamic_buffer = max(0.005, min(0.020, candle_range_ratio * 0.5))
                    return dynamic_buffer
        except Exception as e:
            self.logger.debug(f"⚠️ [{ticker}] 캔들 기반 동적 버퍼 계산 실패, 호가 스프레드로 폴백: {e}")

        try:
            ask, bid, _, _ = self.kis.get_market_spread(ticker, exchange=exchange)
            if ask > 0 and bid > 0:
                spread = (ask - bid) / bid
                dynamic_buffer = max(0.005, min(0.020, spread * 1.5))
        except Exception:
            pass

        return dynamic_buffer

    def _send_telegram_alert(self, text: str):
        """
        [긴급 경보] 손절 3단계 실패 등 치명적 이벤트 발생 시 텔레그램 발송
        """
        token = getattr(Config, 'TELEGRAM_BOT_TOKEN', None)
        chat_id = getattr(Config, 'TELEGRAM_CHAT_ID', None)
        if not token or not chat_id:
            self.logger.warning("Telegram alert skipped: missing bot token/chat_id")
            return
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
            requests.get(url, params=params, timeout=10)
        except Exception as e:
            self.logger.error(f"Telegram emergency alert failed: {e}")

    def _log_stop_loss_execution(self, record: dict):
        """
        [Data Enhancement] 3단계 동적 지정가 & 최후 탈출 손절 체결 추적 로깅 (CSV 저장)
        - 1단계 지정가 체결 소요시간, 체결 실패율, 2단계 전환, 3단계 진입/최종 체결 여부 추적
        """
        import csv
        from pathlib import Path
        try:
            base_dir = Path(__file__).resolve().parent.parent
            log_dir = base_dir / "logs" / "live"
            log_dir.mkdir(parents=True, exist_ok=True)
            
            file_path = log_dir / "stop_loss_execution_tracker.csv"
            file_exists = file_path.exists()
            
            with open(file_path, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=record.keys(), extrasaction='ignore')
                if not file_exists or file_path.stat().st_size == 0:
                    writer.writeheader()
                writer.writerow(record)
        except Exception as e:
            self.logger.error(f"⚠️ 손절 체결 추적 로그 기록 실패: {e}")

    def check_alpha_exit(self, position: dict, current_price: float, now_time: datetime.datetime = None):
        """
        [알파 전략 전용 실시간 청산 감시 엔진] (지시 9)
        1. 익절: +3.5% 도달 시 매도 집행 (TARGET_PROFIT_0.035)
        2. 손절: -10.0% 이탈 시 시장가 손절 집행 (STOP_LOSS_0.10)
        3. 타임컷: 진입 시각으로부터 45분 경과 시 시장가 청산 집행 (TIME_CUT_45M)
        """
        if current_price <= 0:
            return None
        entry_price = position.get('entry_price', 0.0)
        if entry_price <= 0:
            return None

        pnl_pct = round((current_price - entry_price) / entry_price, 6)
        tp_target = position.get('tp_pct', getattr(Config, 'ALPHA_TP_PCT', 0.035))
        sl_target = position.get('sl_pct', getattr(Config, 'ALPHA_SL_PCT', -0.10))

        # (1) 익절 +3.5%
        if pnl_pct >= tp_target:
            return {
                'type': 'SELL',
                'reason': 'TARGET_PROFIT_0.035',
                'price': current_price,
                'pnl_pct': pnl_pct
            }

        # (2) 손절 -10.0%
        if pnl_pct <= sl_target:
            return {
                'type': 'SELL',
                'reason': 'STOP_LOSS_0.10',
                'price': current_price,
                'pnl_pct': pnl_pct
            }

        # (3) 타임컷 45분
        entry_time = position.get('entry_time')
        if entry_time:
            if now_time is None:
                now_time = datetime.datetime.now(pytz.timezone('US/Eastern'))

            if hasattr(now_time, 'tzinfo') and now_time.tzinfo is not None and hasattr(entry_time, 'tzinfo') and entry_time.tzinfo is None:
                entry_time = pytz.timezone('US/Eastern').localize(entry_time)
            elif hasattr(entry_time, 'tzinfo') and entry_time.tzinfo is not None and hasattr(now_time, 'tzinfo') and now_time.tzinfo is None:
                now_time = pytz.timezone('US/Eastern').localize(now_time)

            elapsed_minutes = (now_time - entry_time).total_seconds() / 60.0
            time_cut_minutes = float(position.get('time_cut_minutes', position.get('max_hold_min', getattr(Config, 'ALPHA_TIME_CUT_MINUTES', 45))))
            if elapsed_minutes >= time_cut_minutes:
                time_cut_int = int(round(time_cut_minutes))
                return {
                    'type': 'SELL',
                    'reason': f'TIME_CUT_{time_cut_int}M',
                    'price': current_price,
                    'elapsed_minutes': elapsed_minutes
                }

        return None

    def execute_sell(self, portfolio, ticker, reason, price=0):
        """
        [핵심 수정] 스마트 매도 집행 (selling_in_progress 락 + Cancel-Then-Sell + 2단계 동적 지정가 손절)
        """
        position = portfolio.get_position(ticker)
        if not position:
            return None

        if ticker in self.selling_in_progress:
            self.logger.warning(f"⚠️ [{ticker}] 이미 매도 진행 중 -> 중복 매도 방지 스킵")
            return None

        self.selling_in_progress.add(ticker)
        try:
            return self._execute_sell_internal(portfolio, ticker, reason, price=price)
        finally:
            self.selling_in_progress.discard(ticker)

    def _execute_sell_internal(self, portfolio, ticker, reason, price=0):
        position = portfolio.get_position(ticker)
        if not position:
            return None

        qty = position['qty']
        entry_price = position.get('entry_price', price)
        old_balance = float(portfolio.balance)
        
        # ============================================================
        # 🛡️ [Safety Protocol] 기존 주문 취소 (선주문 해결)
        # ============================================================
        # 익절/손절/타임컷 상관없이, 매도를 하려면 기존 주문(익절 대기 등)을 치워야 합니다.
        is_pure_paper = getattr(Config, 'IS_PURE_PAPER', self.is_paper)
        if not is_pure_paper:
            is_urgent = reason in ["STOP_LOSS", "TRAILING_STOP", "FORCE_EOD_EXIT"]
            self._clear_pending_orders(ticker, is_urgent=is_urgent)

        # ============================================================
        # 🔫 [Execution] 매도 주문 실행 (페이퍼 모드 분기)
        # ============================================================
        if is_pure_paper:
            resp = self.virtual_engine.execute_paper_sell(
                ticker=ticker,
                qty=qty,
                entry_price=entry_price,
                signal_price=price,
                reason=reason,
                exchange="NAS"
            )
            order_price = resp.get('output', {}).get('fill_price', price) if resp else price
        else:
            assert not is_pure_paper, "CRITICAL GUARD: Pure paper mode must NEVER call send_order"
            order_type = "00" # 지정가 기본
            order_price = price

            # [조건별 주문 유형 설정]
            pos_exch = position.get('exchange', 'NASD')
            if reason in ["TAKE_PROFIT", "TARGET_PROFIT_0.035"]:
                order_type = "00"
                self.logger.info(f"💰 [{reason}] 매도 시도 ({pos_exch}): {ticker} (가격: {order_price}, 수량: {qty})")
                resp = None
                for attempt in range(1, 4):
                    resp = self.kis.send_order(
                        ticker=ticker,
                        side="SELL",
                        qty=qty,
                        price=order_price,
                        order_type=order_type,
                        exchange=pos_exch
                    )
                    if resp and resp.get('rt_cd') == '0':
                        break
                    self.logger.warning(f"⚠️ [{reason}] {ticker} 매도 #{attempt} 실패 -> 재시도 (오류: {resp.get('msg1') if resp else '무응답'})")
                    time.sleep(0.5)
            elif reason == "TRAILING_STOP":
                order_type = "00"
                if price > 0:
                    order_price = round_price(price * 0.99)
                self.logger.info(f"📉 [{reason}] 매도 시도 ({pos_exch}): {ticker} (가격: {order_price}, 수량: {qty})")
                resp = None
                start_t = time.time()
                attempt = 0
                while time.time() - start_t < 2.0:
                    attempt += 1
                    resp = self.kis.send_order(
                        ticker=ticker,
                        side="SELL",
                        qty=qty,
                        price=order_price,
                        order_type=order_type,
                        exchange=pos_exch
                    )
                    if resp and resp.get('rt_cd') == '0':
                        break
                    err_m = resp.get('msg1') if resp else '무응답'
                    self.logger.warning(f"⚠️ [{reason}] {ticker} 트레일링 매도 #{attempt} 거부/실패 ({err_m}) -> 0.2초 후 재시도")
                    time.sleep(0.2)
            elif reason == "FORCE_EOD_EXIT":
                order_price = round_price(price * 0.95) if price > 0 else 0
                self.logger.info(f"⏰ [{reason}] 장마감 긴급 매도 ({pos_exch}): {ticker} (가격: {order_price}, 수량: {qty})")
                resp = None
                start_t = time.time()
                attempt = 0
                while time.time() - start_t < 2.0:
                    attempt += 1
                    resp = self.kis.send_order(
                        ticker=ticker,
                        side="SELL",
                        qty=qty,
                        price=order_price,
                        order_type="00",
                        exchange=pos_exch
                    )
                    if resp and resp.get('rt_cd') == '0':
                        break
                    err_m = resp.get('msg1') if resp else '무응답'
                    self.logger.warning(f"⚠️ [{reason}] {ticker} EOD 긴급 매도 #{attempt} 거부/실패 ({err_m}) -> 0.2초 후 재시도")
                    time.sleep(0.2)
            else:
                # --------------------------------------------------------
                # 🛡️ [3단계 동적 지정가 & 최후 탈출 손절 체계 (3-Step Guaranteed Exit)]
                # 1단계: 종목 최근 변동성(ATR/레인지) 기반 동적 지정가 우선 발주
                # 2단계: 1.5초 내 미체결 시 취소 후 긴급 탈출가(-5%)로 전환
                # 3단계: 2단계 미체결 시 실시간 Bid-3% 관통 재시도(최대 3회),
                #       3회 모두 실패 시 텔레그램 긴급 경보 발송 및 HTS 수동 청산 유도
                # --------------------------------------------------------
                exchange = pos_exch
                dyn_buffer = self._calculate_dynamic_stop_buffer(ticker, price, exchange=exchange)
                first_limit_price = round_price(price * (1.0 - dyn_buffer)) if price > 0 else 0
                order_price = first_limit_price
                step1_sent_time = time.time()

                self.logger.info(
                    f"📉 [{reason}] [1단계 동적 지정가] 매도 시도: {ticker} "
                    f"(가격: ${first_limit_price:.4f}, 동적버퍼: -{dyn_buffer*100:.2f}%, 수량: {qty})"
                )

                # [작업 O-1] 거부/수량부족 시 0.2초 간격 재시도 (총 2초 한도)
                resp = None
                start_t = time.time()
                attempt = 0
                while time.time() - start_t < 2.0:
                    attempt += 1
                    resp = self.kis.send_order(
                        ticker=ticker,
                        side="SELL",
                        qty=qty,
                        price=first_limit_price,
                        order_type="00",
                        exchange=exchange
                    )
                    if resp and resp.get('rt_cd') == '0':
                        break
                    err_m = resp.get('msg1') if resp else '무응답'
                    self.logger.warning(f"⚠️ [{reason}] {ticker} 1단계 손절 #{attempt} 거부/실패 ({err_m}) -> 0.2초 후 재시도")
                    time.sleep(0.2)

                step1_status = "UNKNOWN"
                step2_triggered = False
                step1_elapsed = 0.0
                step3_entered = False
                step3_retries = 0
                step3_success = False
                final_status = "UNKNOWN"

                # 2단계: 1단계 주문 접수 성공 시 미체결 체크 후 전환
                if resp and resp.get('rt_cd') == '0':
                    time.sleep(1.5)
                    step1_elapsed = round(time.time() - step1_sent_time, 2)
                    try:
                        pending_list = self.kis.get_pending_orders(ticker)
                        if pending_list and len(pending_list) > 0:
                            step1_status = "FAILED_PENDING"
                            step2_triggered = True
                            self.logger.warning(
                                f"⚠️ [{ticker}] 1단계 지정가(${first_limit_price:.4f}) {step1_elapsed}초 내 미체결 잔량 감지 "
                                f"-> 2단계 긴급 탈출가(-5%)로 전환"
                            )
                            # 1단계 미체결 주문 취소
                            for p_order in pending_list:
                                oid = p_order.get('odno')
                                excd = p_order.get('ovrs_excg_cd', exchange)
                                self.kis.cancel_order(ticker, oid, qty=0, exchange=excd)

                            time.sleep(0.5)

                            # 2단계 긴급 탈출가 재발주
                            emergency_price = round_price(price * 0.95) if price > 0 else 0
                            resp_step2 = self.kis.send_order(
                                ticker=ticker,
                                side="SELL",
                                qty=qty,
                                price=emergency_price,
                                order_type="00",
                                exchange=exchange
                            )
                            if resp_step2 and resp_step2.get('rt_cd') == '0':
                                resp = resp_step2
                                order_price = emergency_price
                                self.logger.info(f"✅ [{ticker}] 2단계 긴급 매도 주문 전송 완료 (${emergency_price:.4f})")

                                # 3단계: 2단계 체결 여부 확인 (1.5초 대기)
                                time.sleep(1.5)
                                pending_step2 = self.kis.get_pending_orders(ticker)
                                if pending_step2 and len(pending_step2) > 0:
                                    step3_entered = True
                                    self.logger.error(
                                        f"🚨 [{ticker}] 2단계 긴급 탈출가(${emergency_price:.4f})도 1.5초 내 미체결! "
                                        f"-> 3단계 최후 탈출 루프(Bid-3% 관통 재시도 최대 3회) 가동"
                                    )
                                    # 2단계 미체결 취소
                                    for p_order in pending_step2:
                                        oid = p_order.get('odno')
                                        excd = p_order.get('ovrs_excg_cd', exchange)
                                        self.kis.cancel_order(ticker, oid, qty=0, exchange=excd)
                                    time.sleep(0.5)

                                    # 최대 3회 재시도 루프
                                    for retry in range(1, 4):
                                        step3_retries = retry
                                        try:
                                            ask_p, bid_p, _, _ = self.kis.get_market_spread(ticker, exchange=exchange)
                                        except Exception:
                                            bid_p = 0.0

                                        if bid_p and bid_p > 0:
                                            deep_price = round_price(bid_p * 0.97)
                                        else:
                                            deep_price = round_price(price * 0.90) if price > 0 else 0

                                        self.logger.warning(
                                            f"🔥 [{ticker}] 3단계 최후 탈출 시도 #{retry}/3: 매도가 ${deep_price:.4f} (현재 Bid: ${bid_p:.4f})"
                                        )

                                        resp_step3 = self.kis.send_order(
                                            ticker=ticker,
                                            side="SELL",
                                            qty=qty,
                                            price=deep_price,
                                            order_type="00",
                                            exchange=exchange
                                        )
                                        if resp_step3 and resp_step3.get('rt_cd') == '0':
                                            resp = resp_step3
                                            order_price = deep_price
                                            time.sleep(1.5)
                                            pending_step3 = self.kis.get_pending_orders(ticker)
                                            if not pending_step3 or len(pending_step3) == 0:
                                                step3_success = True
                                                final_status = f"STEP3_FILLED_RETRY_{retry}"
                                                self.logger.info(f"🎯 [{ticker}] 3단계 최후 탈출 체결 성공! (시도 #{retry}, 체결가: ${deep_price:.4f})")
                                                break
                                            else:
                                                self.logger.warning(f"⚠️ [{ticker}] 3단계 시도 #{retry} 여전히 미체결 -> 취소 후 재시도")
                                                for p_order in pending_step3:
                                                    oid = p_order.get('odno')
                                                    excd = p_order.get('ovrs_excg_cd', exchange)
                                                    self.kis.cancel_order(ticker, oid, qty=0, exchange=excd)
                                                time.sleep(0.5)
                                        else:
                                            self.logger.error(f"❌ [{ticker}] 3단계 시도 #{retry} 주문 전송 실패: {resp_step3}")
                                            time.sleep(0.5)

                                    if not step3_success:
                                        final_status = "STEP3_ALL_FAILED"
                                        alert_msg = (
                                            f"🚨🚨 <b>[긴급 경보] 손절 3단계 최후 탈출 3회 연속 실패!</b>\n"
                                            f"• 종목: <b>{ticker}</b>\n"
                                            f"• 잔여 수량: <b>{qty}주</b>\n"
                                            f"• 최종 시도가: <b>${order_price:.4f}</b>\n"
                                            f"• 상태: <b>미체결 호가 붕괴/호가 공백 의심 - 즉시 HTS 수동 청산 필요!</b>"
                                        )
                                        self._send_telegram_alert(alert_msg)
                                        self.logger.critical(alert_msg)
                                else:
                                    step3_entered = False
                                    final_status = "STEP2_FILLED"
                                    self.logger.info(f"🎯 [{ticker}] 2단계 긴급 탈출가(${emergency_price:.4f}) 정상 체결 확인!")
                        else:
                            step1_status = "FILLED"
                            final_status = "STEP1_FILLED"
                            self.logger.info(f"🎯 [{ticker}] 1단계 동적 지정가(${first_limit_price:.4f}) {step1_elapsed}초 내 정상 체결 확인!")
                    except Exception as err:
                        self.logger.error(f"⚠️ [{ticker}] 2/3단계 손절 전환 처리 중 오류: {err}")
                else:
                    # 1단계 발주 접수 자체가 2초 재시도 후에도 실패한 경우 -> 즉시 2단계 긴급 탈출 전환
                    step1_status = "FAILED_SEND"
                    step2_triggered = True
                    self.logger.warning(
                        f"⚠️ [{ticker}] 1단계 손절 발주 2초 재시도 최종 실패({resp.get('msg1') if resp else '무응답'}) "
                        f"-> 2단계 긴급 탈출가(-5%)로 즉시 전환"
                    )
                    try:
                        pending_list = self.kis.get_pending_orders(ticker)
                        if pending_list:
                            for p_order in pending_list:
                                oid = p_order.get('odno')
                                excd = p_order.get('ovrs_excg_cd', exchange)
                                self.kis.cancel_order(ticker, oid, qty=0, exchange=excd)
                            time.sleep(0.5)
                    except Exception as e:
                        self.logger.warning(f"⚠️ [{ticker}] 미체결 확인 중 예외: {e}")

                    emergency_price = round_price(price * 0.95) if price > 0 else 0
                    start_s2 = time.time()
                    resp_step2 = None
                    while time.time() - start_s2 < 2.0:
                        resp_step2 = self.kis.send_order(
                            ticker=ticker,
                            side="SELL",
                            qty=qty,
                            price=emergency_price,
                            order_type="00",
                            exchange=exchange
                        )
                        if resp_step2 and resp_step2.get('rt_cd') == '0':
                            break
                        time.sleep(0.2)

                    if resp_step2 and resp_step2.get('rt_cd') == '0':
                        resp = resp_step2
                        order_price = emergency_price
                        self.logger.info(f"✅ [{ticker}] 2단계 긴급 매도 주문 전송 완료 (${emergency_price:.4f})")
                        time.sleep(1.5)
                        try:
                            pending_step2 = self.kis.get_pending_orders(ticker)
                            if pending_step2 and len(pending_step2) > 0:
                                step3_entered = True
                                for p_order in pending_step2:
                                    oid = p_order.get('odno')
                                    excd = p_order.get('ovrs_excg_cd', exchange)
                                    self.kis.cancel_order(ticker, oid, qty=0, exchange=excd)
                                time.sleep(0.5)

                                for retry in range(1, 4):
                                    step3_retries = retry
                                    try:
                                        ask_p, bid_p, _, _ = self.kis.get_market_spread(ticker, exchange=exchange)
                                    except Exception:
                                        bid_p = 0.0
                                    deep_price = round_price(bid_p * 0.97) if bid_p > 0 else (round_price(price * 0.90) if price > 0 else 0)
                                    resp_step3 = self.kis.send_order(
                                        ticker=ticker, side="SELL", qty=qty, price=deep_price, order_type="00", exchange=exchange
                                    )
                                    if resp_step3 and resp_step3.get('rt_cd') == '0':
                                        resp = resp_step3
                                        order_price = deep_price
                                        time.sleep(1.5)
                                        pending_step3 = self.kis.get_pending_orders(ticker)
                                        if not pending_step3 or len(pending_step3) == 0:
                                            step3_success = True
                                            final_status = f"STEP3_FILLED_RETRY_{retry}"
                                            break
                                        else:
                                            for p_order in pending_step3:
                                                self.kis.cancel_order(ticker, p_order.get('odno'), qty=0, exchange=p_order.get('ovrs_excg_cd', exchange))
                                            time.sleep(0.5)
                                    else:
                                        time.sleep(0.5)
                                if not step3_success:
                                    final_status = "STEP3_ALL_FAILED"
                                    alert_msg = (
                                        f"🚨🚨 <b>[긴급 경보] 손절 3단계 최후 탈출 3회 연속 실패!</b>\n"
                                        f"• 종목: <b>{ticker}</b>\n"
                                        f"• 잔여 수량: <b>{qty}주</b>\n"
                                        f"• 최종 시도가: <b>${order_price:.4f}</b>\n"
                                        f"• 상태: <b>즉시 HTS 수동 청산 필요!</b>"
                                    )
                                    self._send_telegram_alert(alert_msg)
                                    self.logger.critical(alert_msg)
                            else:
                                final_status = "STEP2_FILLED"
                        except Exception as e2:
                            self.logger.error(f"⚠️ [{ticker}] 2단계 미체결 확인 오류: {e2}")

                # 📊 [Stop Loss Execution Logging]
                import pytz
                now_et_str = datetime.datetime.now(pytz.timezone('America/New_York')).strftime("%Y-%m-%d %H:%M:%S")
                self._log_stop_loss_execution({
                    "timestamp_et": now_et_str,
                    "ticker": ticker,
                    "trigger_price": price,
                    "step1_price": first_limit_price,
                    "step1_buffer_pct": round(dyn_buffer * 100.0, 3),
                    "step1_elapsed_sec": step1_elapsed,
                    "step1_status": step1_status,
                    "step2_triggered": step2_triggered,
                    "step3_entered": step3_entered,
                    "step3_retries": step3_retries,
                    "step3_success": step3_success,
                    "final_status": final_status,
                    "final_order_price": order_price,
                    "qty": qty,
                    "reason": reason
                })

        if resp and resp.get('rt_cd') == '0':
            output_dict = resp.get('output', {}) if isinstance(resp.get('output'), dict) else {}
            realized_pnl = output_dict.get('realized_pnl', (order_price - entry_price) * qty)
            return_pct = output_dict.get('return_pct', ((order_price - entry_price) / entry_price * 100.0 if entry_price > 0 else 0.0))
            
            if self.is_paper:
                # 🛡️ 페이퍼 모드: 손익 누적 및 매도 대금 가상 잔고에 반영
                portfolio.register_realized_sale(
                    ticker=ticker,
                    qty=qty,
                    sell_price=order_price,
                    entry_price=entry_price,
                    reason=reason
                )
                portfolio.update_position({
                    'ticker': ticker,
                    'qty': qty,
                    'price': order_price,
                    'type': 'SELL',
                    'time': datetime.datetime.now()
                })
            else:
                # [체결기준 손익 즉시 반영] 실현손익 및 D+2 미결제 매도대금 등록
                portfolio.register_realized_sale(
                    ticker=ticker,
                    qty=qty,
                    sell_price=order_price,
                    entry_price=entry_price,
                    reason=reason
                )
                # 포트폴리오에서 즉시 제거 (재진입 방지 쿨다운은 main.py에서 처리)
                portfolio.close_position(ticker)

                # [작업 2] 1.5초 대기 + 최대 3회 재조회 잔고 확인
                net_proceeds = (order_price * qty) * (1.0 - 0.001)
                if hasattr(portfolio, 'confirm_post_sell_balance'):
                    portfolio.confirm_post_sell_balance(old_balance=old_balance, net_proceeds=net_proceeds)
            
            # [작업 4-3] 표준 청산 메시지 포맷팅
            final_fill_p = output_dict.get('fill_price') or order_price
            is_fill_price = ('fill_price' in output_dict)
            is_win = (realized_pnl >= 0)
            from infra.utils import format_exit_message
            eff_cash = portfolio.get_effective_balance() if hasattr(portfolio, 'get_effective_balance') else portfolio.balance
            tot_eq = getattr(portfolio, 'total_equity', eff_cash)
            acct_pnl = portfolio.get_account_pnl() if hasattr(portfolio, 'get_account_pnl') else getattr(portfolio, 'daily_realized_pnl', 0.0)
            is_del = getattr(portfolio, 'is_delayed_settlement', False)
            strat_label = position.get('strategy_name', position.get('strategy', 'EMA'))
            if strat_label == 'ALPHA':
                strat_label = 'ALPHA 전략'
            msg = format_exit_message(
                ticker=ticker,
                strategy=strat_label,
                entry_price=entry_price,
                exit_price=final_fill_p,
                ret_pct=return_pct,
                pnl=realized_pnl,
                cash=eff_cash,
                total_equity=tot_eq,
                daily_account_pnl=acct_pnl,
                is_win=is_win,
                is_fill_price=is_fill_price,
                is_delayed=is_del,
                qty=qty,
                reason=reason,
                is_paper=self.is_paper
            )
            return {
                'status': 'success',
                'msg': msg
            }
        else:
            self.logger.error(f"❌ 매도 실패 ({ticker}): {resp}")
            return None

    def _clear_pending_orders(self, ticker, is_urgent=False):
        """
        [수정됨] 미체결 내역의 '거래소 코드'까지 파악하여 취소 (AMEX/NYSE 대응)
        - is_urgent=True: 긴급 손절 모드 (최대 0.5초, 0.1초 간격 폴링 후 즉시 탈출)
        - is_urgent=False: 일반 정정 모드 (최대 1.8초, 0.3초 간격 폴링)
        """
        try:
            guard = self.apbk2623_cancel_guard.get(ticker)
            now = time.time()

            if guard:
                if now < guard['until']:
                    last_skip_log = guard.get('last_skip_log', 0)
                    if now - last_skip_log >= 15:
                        remaining = max(1, int(guard['until'] - now))
                        self.logger.warning(
                            f"⏸️ [{ticker}] APBK2623 취소 보호 활성화 "
                            f"({remaining}초 남음 | OID: {guard['order_id']} | {guard['exchange']}) "
                            f"-> 반복 취소 재시도 생략"
                        )
                        guard['last_skip_log'] = now
                    return False

                self.logger.info(
                    f"🔁 [{ticker}] APBK2623 취소 보호 만료 -> 미체결 취소 재확인 재개"
                )
                self.apbk2623_cancel_guard.pop(ticker, None)

            # 1. 미체결 조회
            pending_list = self.kis.get_pending_orders(ticker)
            
            if not pending_list:
                self.apbk2623_cancel_guard.pop(ticker, None)
                return True

            self.logger.info(f"🧹 [{ticker}] 미체결 {len(pending_list)}건 발견 -> 취소 시도")

            # 2. 거래소 정보(excd)를 포함하여 취소 실행
            for order in pending_list:
                oid = order['odno']
                # [핵심] 미체결 내역에서 거래소 코드 추출 (없으면 기본값 NASD)
                excd = order.get('ovrs_excg_cd', 'NASD') 
                
                # kis_api.cancel_order 함수 호출 (exchange 인자 추가)
                res = self.kis.cancel_order(ticker, oid, qty=0, exchange=excd)
                
                if res and res.get('rt_cd') == '0':
                    self.apbk2623_cancel_guard.pop(ticker, None)
                    self.logger.info(f"   ㄴ 취소 성공 (OID: {oid} | {excd})")
                elif res and res.get('msg_cd') == 'APBK2623':
                    armed_at = time.time()
                    self.apbk2623_cancel_guard[ticker] = {
                        'order_id': oid,
                        'exchange': excd,
                        'until': armed_at + self.APBK2623_CANCEL_GUARD_SECONDS,
                        'last_skip_log': armed_at
                    }
                    self.logger.warning(
                        f"⏸️ [{ticker}] APBK2623 감지 "
                        f"(OID: {oid} | {excd}) -> "
                        f"{self.APBK2623_CANCEL_GUARD_SECONDS}초 동안 반복 취소 재시도 차단: {res}"
                    )
                    break
                else:
                    self.logger.error(f"   ㄴ 취소 실패 (OID: {oid}): {res}")
            
            # [작업 O-1] 취소 확인 폴링: is_urgent 여부에 따라 대기 시간 차등 적용
            all_cleared = False
            poll_interval = 0.1 if is_urgent else 0.3
            max_polls = 5 if is_urgent else 6  # is_urgent: 최대 0.5초 (0.1s x 5회), 일반: 최대 1.8초 (0.3s x 6회)

            for poll_idx in range(max_polls):
                time.sleep(poll_interval)
                rem_pending = self.kis.get_pending_orders(ticker)
                if not rem_pending or len(rem_pending) == 0:
                    all_cleared = True
                    self.logger.info(f"✅ [{ticker}] 미체결 주문 취소 완료 확인 (잔여 0건)")
                    break

            if not all_cleared:
                if is_urgent:
                    self.logger.warning(f"⚠️ [{ticker}] 긴급 손절 취소 확인 제한시간(0.5초) 도달 -> 확인 여부 무관 즉시 매도 발주")
                else:
                    self.logger.warning(f"⚠️ [{ticker}] 취소 요청 후 미체결 잔여 감지 또는 확인 지연")
            return all_cleared

        except Exception as e:
            self.logger.error(f"⚠️ 미체결 정리 중 오류: {e}")
            return False
