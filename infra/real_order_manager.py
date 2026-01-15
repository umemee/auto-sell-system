import time
from config import Config
from infra.utils import get_logger

logger = get_logger("OrderManager")

class RealOrderManager:
    """
    [Real Order Manager]
    역할:
      1. 매수/매도 주문 집행 (Execution)
      2. 주문 후 즉시 Portfolio의 로컬 상태 업데이트 (Optimistic Update)
      3. 실패 시나리오 방어 (Fat Finger 등)
    """
    def __init__(self, kis_api):
        self.kis = kis_api

    def execute_buy(self, portfolio, signal):
        """매수 집행: 포트폴리오 비중 계산 -> API 주문 -> 로컬 장부 반영"""
        ticker = signal['ticker']
        price = signal['price'] # 현재가

        # 1. 자금 관리: Portfolio에게 "얼마치 살 수 있어?" 물어보기
        invest_amt = portfolio.get_max_order_amount()
        
        if invest_amt <= 0:
            logger.warning(f"🚫 [Buy Reject] 자금 부족 또는 슬롯 Full ({ticker})")
            return None

        # 2. 수량 계산 (수수료 버퍼 고려는 get_max_order_amount에서 이미 처리됨)
        # 하지만 혹시 모르니 여기서 정수로 내림 변환
        qty = int(invest_amt / price)
        
        if qty <= 0:
            logger.warning(f"🚫 [Buy Reject] 수량 0 ({ticker} @ ${price})")
            return None

        # 3. 호가 보정 (Config.BUY_TOLERANCE 사용)
        # 지정가지만 시장가처럼 체결되도록 약간 높게 잡음
        limit_price = price * getattr(Config, 'BUY_TOLERANCE', 1.01) 
        
        logger.info(f"⚡ [BUY EXEC] {ticker} {qty}주 @ ${limit_price:.2f} (Target: ${invest_amt:.2f})")

        # 4. API 주문 전송
        ord_no = self.kis.buy_limit(ticker, limit_price, qty)
        
        if ord_no:
            # 5. [중요] 주문 성공 시 Portfolio에 즉시 반영 (Phantom Buy 방지)
            fill_data = {
                'type': 'BUY',
                'ticker': ticker,
                'qty': qty,
                'price': price # 체결 추정가는 현재가로 기록
            }
            portfolio.update_local_after_order(fill_data)
            return ord_no
        
        return None

    def execute_sell(self, portfolio, ticker, reason="Unknown"):
        """매도 집행: 전량 매도 -> API 주문 -> 로컬 장부 반영"""
        # 1. 포지션 확인
        pos = portfolio.get_position(ticker)
        if not pos:
            logger.warning(f"🚫 [Sell Reject] 보유하지 않음 ({ticker})")
            return None
            
        qty = pos['qty']
        
        logger.info(f"👋 [SELL EXEC] {ticker} {qty}주 (Reason: {reason})")

        # 2. API 주문 전송 (시장가 매도)
        ord_no = self.kis.sell_market(ticker, qty)
        
        if ord_no:
            # 3. [중요] 주문 성공 시 Portfolio에서 즉시 삭제 (Phantom Sell 방지)
            fill_data = {
                'type': 'SELL',
                'ticker': ticker,
                'qty': qty,
                'price': pos['current_price'] # 단순 기록용
            }
            portfolio.update_local_after_order(fill_data)
            return ord_no
            
        return None