# infra/real_order_manager.py
import time
import datetime
from config import Config
from infra.utils import get_logger
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
        self.is_paper = (getattr(Config, 'EXECUTION_MODE', 'REAL') == 'PAPER_TRADING_ONLY' or getattr(Config, 'IS_PAPER_TRADING', False))
        self.virtual_engine = VirtualExecutionEngine(kis_api)
        
        # 🛡️ [로그 폭탄 방지] 종목별 마지막 로그 시간 기록부
        self.log_throttle_map = {} 
        self.apbk2623_cancel_guard = {}

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
            log_dir = Path("logs/spread_analysis")
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
        # 🛡️ [Anti-FOMO Buffer 0.5%] 시그널 가격 대비 +0.5% 초과 추격 매수 원천 차단
        # ============================================================
        buy_slippage_buffer = getattr(Config, 'BUY_SLIPPAGE_BUFFER', 0.005)
        if price > 0:
            max_allowed_buy_price = price * (1.0 + buy_slippage_buffer)
            if ask > max_allowed_buy_price:
                self.logger.warning(
                    f"🚫 [Anti-FOMO Reject] {ticker} 매수 호가 과열 이탈 "
                    f"(Ask ${ask:.2f} > 허용상한 ${max_allowed_buy_price:.2f}, +{buy_slippage_buffer*100:.1f}% 초과) -> 매수 차단"
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
        qty = portfolio.calculate_qty(price)
        if qty <= 0:
            return {'status': 'failed', 'msg': f"❌ 잔고 부족 또는 수량 계산 실패 ({ticker})"}

        # ============================================================
        # 4. 주문 전송 (페이퍼 모드 분기)
        # ============================================================
        if self.is_paper:
            resp = self.virtual_engine.execute_paper_buy(
                ticker=ticker,
                qty=qty,
                signal_price=price,
                exchange="NAS"
            )
        else:
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

            try:
                portfolio.update_position({
                    'ticker': ticker,
                    'qty': qty,
                    'price': entry_guess,
                    'entry_price': entry_guess,
                    'type': 'BUY',
                    'time': datetime.datetime.now()
                })
            except Exception as e:
                self.logger.error(f"❌ 포트폴리오 업데이트 실패: {e}")
            
            mode_tag = " [PAPER]" if self.is_paper else ""
            msg = (
                f"⚡ <b>가상 매수 체결 완료{mode_tag}</b>\n"
                f"📦 종목: {ticker}\n"
                f"🔢 수량: {qty}주\n"
                f"💵 체결가: ${entry_guess:.4f} (시그널: ${price:.4f})\n"
                f"📝 주문번호: {odno}"
            )
            return {'status': 'success', 'msg': msg, 'qty': qty, 'avg_price': entry_guess}
        else:
            fail_msg = resp.get('msg1', '알 수 없는 오류') if resp else '응답 없음'
            return {'status': 'failed', 'msg': f"❌ 매수 실패 ({ticker}): {fail_msg}"}

    def execute_sell(self, portfolio, ticker, reason, price=0):
        """
        [핵심 수정] 스마트 매도 집행 (Cancel-Then-Sell)
        
        우리의 3가지 문제(손절, 타임컷, 장마감)를 해결하는 곳입니다.
        매도 주문을 내기 전에 '미체결 주문'이 있는지 확인하고, 있다면 취소합니다.
        """
        position = portfolio.get_position(ticker)
        if not position:
            return None

        qty = position['qty']
        entry_price = position.get('entry_price', price)
        
        # ============================================================
        # 🛡️ [Safety Protocol] 기존 주문 취소 (선주문 해결)
        # ============================================================
        # 익절/손절/타임컷 상관없이, 매도를 하려면 기존 주문(익절 대기 등)을 치워야 합니다.
        if not self.is_paper:
            self._clear_pending_orders(ticker)

        # ============================================================
        # 🔫 [Execution] 매도 주문 실행 (페이퍼 모드 분기)
        # ============================================================
        if self.is_paper:
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
            order_type = "00" # 지정가 기본
            order_price = price

            # [조건별 주문 유형 설정]
            if reason == "TAKE_PROFIT":
                order_type = "00" 
            elif reason == "TRAILING_STOP":
                order_type = "00"
                if price > 0:
                    order_price = price * 0.99
            else:
                order_price = 0 
                order_type = "00" 
                if price > 0:
                    order_price = price * 0.95

            # 주문 전송
            self.logger.info(f"📉 [{reason}] 매도 시도: {ticker} (가격: {order_price}, 수량: {qty})")
            
            resp = self.kis.send_order(
                ticker=ticker,
                side="SELL",
                qty=qty,
                price=order_price,
                order_type=order_type 
            )

        if resp and resp.get('rt_cd') == '0':
            output_dict = resp.get('output', {}) if isinstance(resp.get('output'), dict) else {}
            realized_pnl = output_dict.get('realized_pnl', (order_price - entry_price) * qty)
            return_pct = output_dict.get('return_pct', ((order_price - entry_price) / entry_price * 100.0 if entry_price > 0 else 0.0))
            
            if self.is_paper:
                # 🛡️ 페이퍼 모드: 매도 대금 및 실현 손익 가상 잔고에 반영
                portfolio.update_position({
                    'ticker': ticker,
                    'qty': qty,
                    'price': order_price,
                    'type': 'SELL',
                    'time': datetime.datetime.now()
                })
            else:
                # 포트폴리오에서 즉시 제거 (재진입 방지 쿨다운은 main.py에서 처리)
                portfolio.close_position(ticker)
            
            mode_tag = " [PAPER]" if self.is_paper else ""
            return {
                'status': 'success',
                'msg': (
                    f"🔴 <b>[가상 매도 체결{mode_tag}] {ticker}</b>\n"
                    f"사유: {reason}\n"
                    f"수량: {qty}주 | 체결가: ${order_price:.4f}\n"
                    f"손익: ${realized_pnl:+.2f} ({return_pct:+.2f}%)"
                )
            }
        else:
            self.logger.error(f"❌ 매도 실패 ({ticker}): {resp}")
            return None

    def _clear_pending_orders(self, ticker):
        """
        [수정됨] 미체결 내역의 '거래소 코드'까지 파악하여 취소 (AMEX/NYSE 대응)
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
                    return

                self.logger.info(
                    f"🔁 [{ticker}] APBK2623 취소 보호 만료 -> 미체결 취소 재확인 재개"
                )
                self.apbk2623_cancel_guard.pop(ticker, None)

            # 1. 미체결 조회
            pending_list = self.kis.get_pending_orders(ticker)
            
            if not pending_list:
                self.apbk2623_cancel_guard.pop(ticker, None)
                return

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
            
            # 취소 반영 대기
            time.sleep(0.5)

        except Exception as e:
            self.logger.error(f"⚠️ 미체결 정리 중 오류: {e}")
