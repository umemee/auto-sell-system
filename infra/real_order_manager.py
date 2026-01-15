import time
from config import Config
from infra.utils import get_logger

logger = get_logger("OrderManager")

class RealOrderManager:
    """
    [Real Order Manager V2.0 - Rich Message Edition]
    
    역할:
      1. 매수/매도 주문 집행 및 재진입 방지(Cool-down Check)
      2. 주문 후 포트폴리오 로컬 상태 즉시 업데이트 (Optimistic Update)
      3. 사용자 알림을 위한 상세 메시지(Formatted String) 생성 및 반환
    """
    def __init__(self, kis_api):
        self.kis = kis_api

    def execute_buy(self, portfolio, signal):
        """매수 집행"""
        ticker = signal['ticker']
        price = signal['price'] # 현재가

        # 1. [NEW] 쿨다운 체크 (금일 매도한 종목 재진입 방지)
        if portfolio.is_banned(ticker):
            logger.warning(f"🚫 [Buy Reject] 금일 매매 금지 종목 (Cool-down): {ticker}")
            return None

        # 2. 자금 관리: Portfolio에게 "얼마치 살 수 있어?" 물어보기
        invest_amt = portfolio.get_max_order_amount()
        
        if invest_amt <= 0:
            logger.warning(f"🚫 [Buy Reject] 자금 부족 또는 슬롯 Full ({ticker})")
            return None

        # 3. 수량 계산 (수수료 버퍼 고려는 get_max_order_amount에서 이미 처리됨)
        qty = int(invest_amt / price)
        
        if qty <= 0:
            logger.warning(f"🚫 [Buy Reject] 수량 0 ({ticker} @ ${price})")
            return None

        # 4. 호가 보정 (Config.BUY_TOLERANCE 사용)
        # 지정가지만 시장가처럼 체결되도록 약간 높게 잡음
        limit_price = price * getattr(Config, 'BUY_TOLERANCE', 1.01) 
        
        logger.info(f"⚡ [BUY EXEC] {ticker} {qty}주 @ ${limit_price:.2f} (Target: ${invest_amt:.2f})")

        # 5. API 주문 전송
        ord_no = self.kis.buy_limit(ticker, limit_price, qty)
        
        if ord_no:
            # 6. [중요] 주문 성공 시 Portfolio에 즉시 반영 (Phantom Buy 방지)
            fill_data = {
                'type': 'BUY',
                'ticker': ticker,
                'qty': qty,
                'price': price # 체결 추정가는 현재가로 기록
            }
            portfolio.update_local_after_order(fill_data)
            
            # [NEW] 텔레그램 전송용 상세 메시지 생성
            msg = (
                f"⚡ <b>매수 체결 완료</b>\n"
                f"📦 종목: <b>{ticker}</b>\n"
                f"💵 가격: ${price:.2f}\n"
                f"🔢 수량: {qty}주\n"
                f"💰 총액: ${invest_amt:.2f}\n"
                f"📝 주문번호: {ord_no}"
            )
            return msg # 메시지 문자열 반환
        
        return None

    def execute_sell(self, portfolio, ticker, reason="Unknown"):
        """매도 집행"""
        # 1. 포지션 확인
        pos = portfolio.get_position(ticker)
        if not pos:
            logger.warning(f"🚫 [Sell Reject] 보유하지 않음 ({ticker})")
            return None
            
        qty = pos['qty']
        current_price = pos['current_price']
        pnl_pct = pos.get('pnl_pct', 0.0)
        total_val = qty * current_price
        
        logger.info(f"👋 [SELL EXEC] {ticker} {qty}주 (Reason: {reason})")

        # 2. API 주문 전송 (시장가 매도)
        ord_no = self.kis.sell_market(ticker, qty)
        
        if ord_no:
            # 3. [중요] 주문 성공 시 Portfolio에서 즉시 삭제 (Phantom Sell 방지)
            fill_data = {
                'type': 'SELL',
                'ticker': ticker,
                'qty': qty,
                'price': current_price # 단순 기록용
            }
            portfolio.update_local_after_order(fill_data)
            
            # [NEW] 텔레그램 전송용 상세 메시지 생성
            icon = "🔴" if pnl_pct < 0 else "🟢"
            msg = (
                f"👋 <b>매도 체결 완료</b> [{reason}]\n"
                f"📦 종목: <b>{ticker}</b>\n"
                f"💵 매도가: ${current_price:.2f} (Est.)\n"
                f"🔢 수량: {qty}주\n"
                f"💰 총액: ${total_val:.2f}\n"
                f"📊 수익률: {icon} {pnl_pct:.2f}%\n"
                f"📝 주문번호: {ord_no}"
            )
            return msg # 메시지 문자열 반환
            
        return None