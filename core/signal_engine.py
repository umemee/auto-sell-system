# core/signal_engine.py - Dependency Free & Balance Aware
import logging
from typing import Optional
from core.action_plan import ActionPlan

class SignalEngine:
    def __init__(self):
        self.logger = logging.getLogger("SignalEngine")
        
        # 전략 파라미터 (취향에 따라 튜닝 가능)
        self.MIN_GAP_PCT = 2.0          # 갭상승 최소 2%
        self.MIN_PM_VOLUME = 10000      # 프리마켓 거래량 최소 1만주
        self.STOP_LOSS_PCT = 0.02       # 손절 -2%
        self.TAKE_PROFIT_PCT = 0.04     # 익절 +4%

    def analyze(self, symbol: str, current_price: float, open_price: float, pm_volume: int, available_balance: float) -> Optional[ActionPlan]:
        """
        분석 및 자금 관리(Money Management) 통합
        :param available_balance: 현재 주문 가능한 현금 (USD)
        """
        
        # 1. 기초 데이터 검증
        if current_price <= 0:
            return None

        # 2. 전략 로직: 갭상승 & 거래량
        momentum_pct = ((current_price - open_price) / open_price) * 100
        
        if momentum_pct < self.MIN_GAP_PCT:
            return None
        if pm_volume < self.MIN_PM_VOLUME:
            return None

        # 3. 확신도(Confidence) 계산
        score = 0.5 
        if momentum_pct > 5.0: score += 0.2
        if pm_volume > 50000: score += 0.2
        confidence = min(score, 0.95)

        # 4. 진입가 및 청산가 설정
        entry_price = current_price
        stop_price = entry_price * (1 - self.STOP_LOSS_PCT)
        target_price = entry_price * (1 + self.TAKE_PROFIT_PCT)
        
        # 5. [중요] 잔고 기반 수량 계산 (All-in)
        # 미수 발생 방지를 위해 잔고의 99%만 사용
        safe_balance = available_balance * 0.99
        buy_qty = int(safe_balance // entry_price)
        
        if buy_qty < 1:
            # 잔고가 부족하여 1주도 못 사는 경우
            # (로그가 너무 많이 쌓일 수 있어 디버그 레벨로 낮춤)
            # self.logger.debug(f"자금 부족: {symbol} (Need: ${entry_price}, Have: ${available_balance})")
            return None

        self.logger.info(f"✨ Signal Detected: {symbol} (Qty: {buy_qty}, Cash: ${available_balance:.2f})")

        return ActionPlan(
            symbol=symbol,
            signal_type='LONG',
            confidence=confidence,
            reason=f"Gap {momentum_pct:.1f}% / Vol {pm_volume}",
            entry_price=entry_price,
            quantity=buy_qty, 
            stop_loss=stop_price,
            take_profit=[target_price]
        )