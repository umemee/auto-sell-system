# infra/real_order_manager.py
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
        self.logger = get_logger("OrderManager")

    def execute_buy(self, portfolio, signal):
        """
        매수 집행: 포트폴리오 비중 계산 -> API 주문 -> 로컬 장부 반영
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

        # 4. 호가 보정 (Config.BUY_TOLERANCE 사용, 기본 0.5% 위)
        limit_price = price * getattr(Config, 'BUY_TOLERANCE', 1.005)
        
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
            
            # 성공 메시지 생성
            msg = (
                f"⚡ <b>매수 주문 전송 완료</b>\n"  # [수정] 체결 완료 -> 주문 전송 완료
                f"📦 종목: <b>{ticker}</b>\n"
                f"💵 가격: ${price:.2f} (주문가)\n" # [수정]
                f"🔢 수량: {qty}주\n"
                f"💰 예산: ${invest_amt:.2f}\n"
                f"📝 주문번호: {ord_no}"
            )
            return {"status": "success", "msg": msg}
        
        # 실패 시 로그는 kis_api 내부에서 이미 찍힘
        return None

    def execute_sell(self, portfolio, ticker, reason, price=None):
        """
        매도 주문 실행
        - 익절(TAKE_PROFIT): 지정가(Limit) 주문 (슬리피지 방지)
        - 손절(STOP_LOSS) 및 기타: 시장가(Market) 주문 (확실한 탈출)
        """
        if not portfolio.is_holding(ticker):
            return None

        pos = portfolio.positions[ticker]
        qty = pos['qty']
        # [추가] 진입 가격과 진입 시각을 확보합니다.
        entry_price = pos['entry_price']
        entry_time = pos.get('entry_time') # 포트폴리오 장부에 기록된 진입 시각
        # 0. 주문 가능 수량 확인 (혹시 모를 오류 방지)
        if qty <= 0:
            return None

        # -----------------------------------------------------
        # 1. 주문 타입 결정 (핵심 수정)
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
        # kis_api의 send_order 함수 시그니처에 맞춰 호출
        # 보통: send_order(ticker, type="SELL", qty=..., price=..., order_type=...)
        # kis_api.py 구현에 따라 다를 수 있으니 확인 필요. 
        # (아래는 일반적인 KIS API 래퍼 기준 코드입니다)
        
        # KIS API에서는 보통:
        # - 시장가(01): price=0
        # - 지정가(00): price=지정가격
        
        # [KIS API 호출]
        resp = self.kis.send_order(
            ticker=ticker,
            side="SELL",
            qty=qty,
            price=order_price,
            order_type=order_type  # kis_api 내부에서 'LIMIT'->'00', 'MARKET'->'01' 변환한다고 가정
        )

        # -----------------------------------------------------
        # 3. 결과 처리
        # -----------------------------------------------------
        if resp and resp.get('rt_cd') == '0':
            pnl_pct = pos['pnl_pct']
            
            # [수정] 주문 타입에 따라 메시지를 다르게 표시
            if "TAKE_PROFIT" in reason:
                 # 지정가 (익절)
                 title = "🟠 [익절] 지정가 주문 접수 (대기)"
                 price_desc = "목표가"
            else:
                 # 시장가 (손절/EOS) - 사실상 즉시 체결됨
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
        