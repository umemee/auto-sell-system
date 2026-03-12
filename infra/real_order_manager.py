# infra/real_order_manager.py
import time
import datetime
from config import Config
from infra.utils import get_logger

class RealOrderManager:
    """
    [Real Order Manager V3.1 - Smart Logging Edition]
    - 스프레드 과다 시 1분 간격으로만 로그 기록 (I/O 부하 방지)
    - 호가 잔량(Volume) 정보를 함께 기록하여 원인 분석 강화
    - Bid가 0일 경우(매수세 실종) 0으로 나누기 에러 방지
    """
    def __init__(self, kis_api):
        self.kis = kis_api
        self.logger = get_logger("OrderManager")
        
        # 🛡️ [로그 폭탄 방지] 종목별 마지막 로그 시간 기록부
        self.log_throttle_map = {} 

    def execute_buy(self, portfolio, signal):
        """
        [매수 집행] 시장가 진입 + 스프레드 방어 로직
        """
        ticker = signal['ticker']
        price = signal.get('price', 0) 

        # ============================================================
        # 🛡️ [Safety Protocol] 1. 스프레드 및 호가 잔량 체크
        # ============================================================
        try:
            # API를 통해 4가지 데이터 모두 수신
            ask, bid, ask_vol, bid_vol = self.kis.get_market_spread(ticker)
            
            # [방어] 매수 호가(Bid)가 0이면(살 사람이 아예 없으면) 계산 불가 -> 즉시 포기
            if bid <= 0:
                # 호가가 없더라도, 전략이 넘겨준 '현재가(price)'가 있다면 그걸 믿고 진행
                if price > 0:
                    self.logger.warning(f"⚠️ [Liquidity] {ticker} 호가(Bid) 0 발견 -> 전략가({price})로 대체하여 강제 진입")
                    bid = price
                    ask = price # 스프레드를 0으로 가정하여 통과시킴
                else:
                    # 현재가조차 없으면 진짜 위험한 상태이므로 차단
                    self.logger.warning(f"📉 [MISS] {ticker} 매수 잔량 없음 (Bid:0, Last:0) -> 진입 불가")
                    return None

            # 스프레드 계산
            spread = (ask - bid) / bid
            
            # [설정] 허용 스프레드 1.5% (0.015)
            if spread > 0.015:
                # 🛡️ [Smart Logging] 1분 쿨타임 적용
                last_log = self.log_throttle_map.get(ticker, 0)
                now = time.time()
                
                # 60초가 지났을 때만 로그 기록
                if now - last_log > 60:
                    self.logger.warning(
                        f"📉 [MISS] {ticker} 스프레드({spread*100:.2f}%) 과다로 매수 포기 "
                        f"| Price: {bid}(Bid) vs {ask}(Ask) "
                        f"| Vol: {bid_vol} vs {ask_vol}"  # ✅ 핵심 증거 추가
                    )
                    # 기록 시간 갱신
                    self.log_throttle_map[ticker] = now
                    
                return None # 주문 안 함

        except Exception as e:
            self.logger.error(f"⚠️ 스프레드 체크 중 오류({ticker}): {e}")
            # 안전을 위해 에러 발생 시 매수 포기 (보수적 접근)
            return None

        # ============================================================
        # 1. 쿨다운 체크
        # ============================================================
        if portfolio.is_banned(ticker):
            self.logger.warning(f"🚫 [Buy Reject] 금일 매매 금지 종목 ({ticker})")
            return None

        # 2. 수량 계산
        qty = portfolio.calculate_qty(price)
        if qty <= 0:
            return {'status': 'failed', 'msg': f"❌ 잔고 부족 또는 수량 계산 실패 ({ticker})"}

        # 3. 주문 전송 (시장가)
        resp = self.kis.send_order(
            ticker=ticker,
            side="BUY",
            qty=qty,
            price=price,        
            order_type="MARKET" 
        )
        
        # 4. 결과 처리 (수정된 부분)
        if resp and resp.get('rt_cd') == '0':
            # [수정] ODNO(주문번호)를 가격으로 변환하던 버그 제거
            # 시장가 주문 직후에는 정확한 체결가를 알 수 없으므로,
            # 일단 진입 시도한 가격(price)을 평단가로 가정합니다.
            entry_guess = price 
            odno = resp['output'].get('ODNO', 'Unknown')

            try:
                portfolio.update_position({
                    'ticker': ticker,
                    'qty': qty,
                    'price': entry_guess,  # <--- ✨ 여기가 핵심 수정입니다 ('price'로 통일)
                    'type': 'BUY',
                    'time': datetime.datetime.now() # 시간 정보도 명시적으로 전달
                })
            except Exception as e:
                self.logger.error(f"❌ 포트폴리오 업데이트 실패: {e}")
                # 포트폴리오 업데이트 실패해도 메시지는 보내야 함
            
            msg = (
                f"⚡ <b>매수 주문 완료</b>\n"
                f"📦 종목: {ticker}\n"
                f"🔢 수량: {qty}주\n"
                f"💵 기준가: ${price}\n"
                f"📝 주문번호: {odno}"
            )
            return {'status': 'success', 'msg': msg, 'qty': qty, 'avg_price': price}
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
        
        # ============================================================
        # 🛡️ [Safety Protocol] 기존 주문 취소 (선주문 해결)
        # ============================================================
        # 익절/손절/타임컷 상관없이, 매도를 하려면 기존 주문(익절 대기 등)을 치워야 합니다.
        self._clear_pending_orders(ticker)

        # ============================================================
        # 🔫 [Execution] 매도 주문 실행
        # ============================================================
        order_type = "00" # 지정가 기본
        order_price = price

        # [조건별 주문 유형 설정]
        if reason == "TAKE_PROFIT":
            # 익절 대기는 전략이 지정한 가격 그대로
            order_type = "00" 
        elif reason == "TRAILING_STOP":
            # 🚀 트레일링 스탑: 수익 보존이 목적이므로 너무 후려치지 않고 -1% 하단으로 시장가 체결 유도
            order_type = "00"
            if price > 0:
                order_price = price * 0.99
        else:
            # 🚨 비상 상황 (STOP_LOSS, TIME_CUT, EOD)
            # 무조건 팔려야 하므로 현재가 대비 -5% 하한가로 강하게 집어 던짐
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
            # 포트폴리오에서 즉시 제거 (재진입 방지 쿨다운은 main.py에서 처리)
            portfolio.close_position(ticker)
            
            return {
                'status': 'success',
                'msg': f"🔴 [매도] {ticker} ({reason})\n수량: {qty}주 | 가격: ${order_price:.2f}"
            }
        else:
            self.logger.error(f"❌ 매도 실패 ({ticker}): {resp}")
            return None

    def _clear_pending_orders(self, ticker):
        """
        [수정됨] 미체결 내역의 '거래소 코드'까지 파악하여 취소 (AMEX/NYSE 대응)
        """
        try:
            # 1. 미체결 조회
            pending_list = self.kis.get_pending_orders(ticker)
            
            if not pending_list:
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
                    self.logger.info(f"   ㄴ 취소 성공 (OID: {oid} | {excd})")
                else:
                    self.logger.error(f"   ㄴ 취소 실패 (OID: {oid}): {res}")
            
            # 취소 반영 대기
            time.sleep(0.5)

        except Exception as e:
            self.logger.error(f"⚠️ 미체결 정리 중 오류: {e}")