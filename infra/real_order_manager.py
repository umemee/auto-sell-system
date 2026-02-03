# infra/real_order_manager.py
import time
import datetime
from config import Config
from infra.utils import get_logger

logger = get_logger("OrderManager")

class RealOrderManager:
    """
    [Real Order Manager V3.0 - Smart Execution]
    
    핵심 기능:
    1. 선주문(Pre-Order) 대응: 매도 신호 발생 시, 기존에 걸려있던 익절 주문을 자동으로 '취소'하고 신규 주문을 넣습니다.
    2. 3중 안전장치: 손절/타임컷/장마감 시 '시장가'로 강제 청산합니다.
    """
    def __init__(self, kis_api):
        self.kis = kis_api
        self.logger = get_logger("OrderManager")

    def execute_buy(self, portfolio, signal):
        """
        [매수 집행] 시장가 진입 + 스프레드 방어 로직 (수정된 안전 버전)
        """
        ticker = signal['ticker']
        # signal에 가격이 없으면 현재가 조회, 그래도 없으면 0 (시장가)
        price = signal.get('price', 0) 

        # ============================================================
        # 🛡️ [Safety Protocol] 0. 스프레드(호가 간격) 체크
        # ============================================================
        try:
            ask, bid, ask_vol, bid_vol = self.kis.get_market_spread(ticker)
            
            if ask > 0 and bid > 0:
                spread = (ask - bid) / bid # (매도-매수)/매수
                
                # [설정] 허용 스프레드: 1.5% (0.015)
                if spread > 0.015: 
                    self.logger.warning(f"⚠️ [Spread Reject] {ticker} 호가 공백 과다 ({spread*100:.2f}%) -> 매수 포기")
                    return None
        except Exception as e:
            self.logger.error(f"⚠️ 스프레드 체크 중 오류: {e}")
            # 에러가 나도 매수는 진행하도록 pass (선택 사항)

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
        if "TAKE_PROFIT" in reason:
            # 익절은 지정가 유지 (단, 급격한 변동 시 시장가로 바꿀 수도 있음)
            # 여기서는 전략에 따라 받은 가격 그대로 사용
            order_type = "00" 
        else:
            # 🚨 비상 상황 (손절 -40%, 타임컷 240분, 장마감 EOD)
            # 무조건 팔려야 하므로 '시장가(Market)'로 던집니다.
            order_price = 0 
            order_type = "00" # 해외주식 API에서 시장가는 보통 가격 0 혹은 별도 코드 사용
                              # (사용하시는 API 버전에 따라 '00'에 가격0이면 시장가 인지 확인 필요)
                              # 안전하게는 현재가보다 훨씬 낮은 가격(하한가)으로 지정가 주문하면 시장가처럼 체결됨.
            
            # [Tip] 급등주 손절 팁: 현재가보다 3~5% 낮게 던지면 즉시 체결됨 (Slippage 감수)
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