# infra/real_order_manager.py
import time
from config import Config
from infra.utils import get_logger

logger = get_logger("OrderManager")

class RealOrderManager:
    """
    [Real Order Manager V2.1 - Market Entry Edition]
    
    역할:
      1. 매수: 00초 급등주 진입을 위해 '시장가(공격적 지정가)' 주문 실행
      2. 매도: 익절은 지정가, 손절은 시장가로 실행
      3. 상태 관리: 주문 직후 로컬 포트폴리오 선반영 (Phantom Buy 방지)
    """
    def __init__(self, kis_api):
        self.kis = kis_api
        self.logger = get_logger("OrderManager")

    def execute_buy(self, portfolio, signal):
        """
        [수정됨] 매수 집행
        - 기존: 지정가(Limit) + 0.5% 상방 (체결 실패 가능성 있음)
        - 변경: 시장가(Market) 모드 요청 -> 내부적으로 +5% 상방 주문 (체결 확률 극대화)
        """
        ticker = signal['ticker']
        price = signal['price'] # 현재가

        # 1. [Double Check] 쿨다운 체크 (금일 매도한 종목 재진입 방지)
        if portfolio.is_banned(ticker):
            logger.warning(f"🚫 [Buy Reject] 금일 매매 금지 종목 (Cool-down): {ticker}")
            return None

        # 2. 자금 관리: Portfolio에게 "얼마치 살 수 있어?" 물어보기
        invest_amt = portfolio.get_max_order_amount()
        
        if invest_amt <= 0:
            logger.warning(f"🚫 [Buy Reject] 자금 부족 또는 슬롯 Full ({ticker})")
            return None

        # 3. 수량 계산
        qty = int(invest_amt / price)
        
        if qty <= 0:
            logger.warning(f"🚫 [Buy Reject] 계산된 수량 0 ({ticker} @ ${price})")
            return None

        # ------------------------------------------------------------------
        # [핵심 변경] 급등주 00초 진입을 위한 주문 방식 교체
        # ------------------------------------------------------------------
        # 기존: limit_price = price * getattr(Config, 'BUY_TOLERANCE', 1.005)
        #      ord_no = self.kis.buy_limit(ticker, limit_price, qty)
        
        logger.info(f"⚡ [BUY EXEC] {ticker} {qty}주 @ ${price:.2f} (시장가 진입 시도)")

        # 변경: send_order에 'MARKET' 타입을 전달하여 kis_api가 '공격적 지정가(+5%)'를 내도록 함
        resp = self.kis.send_order(
            ticker=ticker,
            side="BUY",
            qty=qty,
            price=price,        # 기준 가격 (이 가격의 +5%로 주문 나감)
            order_type="MARKET" # 시장가(공격적 체결) 플래그
        )

        # ------------------------------------------------------------------
        # 4. 결과 처리
        # ------------------------------------------------------------------
        if resp and resp.get('rt_cd') == '0':
            # 주문 성공 시 Portfolio에 즉시 반영 (낙관적 업데이트)
            fill_data = {
                'type': 'BUY',
                'ticker': ticker,
                'qty': qty,
                'price': price # 체결 추정가는 현재가로 기록
            }
            portfolio.update_local_after_order(fill_data)
            
            # 성공 메시지 생성
            msg = (
                f"⚡ <b>매수 주문 전송 완료 (시장가)</b>\n"
                f"📦 종목: <b>{ticker}</b>\n"
                f"💵 기준가: ${price:.2f}\n"
                f"🔢 수량: {qty}주\n"
                f"💰 예산: ${invest_amt:.2f}\n"
                f"📝 상태: 체결 대기 (Aggressive Buy)"
            )
            return {"status": "success", "msg": msg}
        
        # 실패 시 로그는 kis_api 내부에서 이미 찍힘
        return None

    def execute_sell(self, portfolio, ticker, reason, price=None):
        """
        매도 주문 실행 (기존 로직 유지)
        """
        if not portfolio.is_holding(ticker):
            return None

        pos = portfolio.positions[ticker]
        qty = pos['qty']
        
        # [참고] 로직에는 쓰이지 않지만, 원본 코드의 변수 선언 유지 (디버깅용)
        entry_price = pos['entry_price']
        entry_time = pos.get('entry_time') 

        # 0. 주문 가능 수량 확인
        if qty <= 0:
            return None

        # -----------------------------------------------------
        # 1. 주문 타입 결정
        # -----------------------------------------------------
        order_type = "MARKET" # 기본은 시장가
        order_price = 0       # 시장가는 가격 0
        
        # 이유가 '익절(TAKE_PROFIT)'이고, 가격이 전달되었다면 -> 지정가 주문
        if "TAKE_PROFIT" in reason and price is not None and price > 0:
            order_type = "LIMIT"
            order_price = price
            type_str = f"지정가(${price})"
        else:
            type_str = "시장가"

        # -----------------------------------------------------
        # 2. 주문 전송
        # -----------------------------------------------------
        resp = self.kis.send_order(
            ticker=ticker,
            side="SELL",
            qty=qty,
            price=order_price,
            order_type=order_type 
        )

        # -----------------------------------------------------
        # 3. 결과 처리
        # -----------------------------------------------------
        if resp and resp.get('rt_cd') == '0':
            pnl_pct = pos['pnl_pct']
            
            # 주문 타입에 따라 메시지 분기
            if "TAKE_PROFIT" in reason:
                 title = "🟠 [익절] 지정가 주문 접수 (대기)"
                 price_desc = "목표가"
            else:
                 title = "🔴 [매도] 시장가 주문 전송 (체결)"
                 price_desc = "시장가"

            msg = (
                f"{title}\n"
                f"📦 종목: <b>{ticker}</b>\n"
                f"📜 사유: {reason}\n"
                f"💵 가격: ${order_price if order_price > 0 else 0:.2f} ({price_desc})\n"
                f"🔢 수량: {qty}주\n"
                f"📊 수익률: {pnl_pct:.2f}% (추정)"
            )
            self.logger.info(f"매도 주문 완료: {ticker} ({type_str})")
            
            return {'status': 'success', 'msg': msg}
            
        return None