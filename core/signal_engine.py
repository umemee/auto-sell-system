# core/signal_engine.py
import logging
from typing import Optional
from core.action_plan import ActionPlan

class SignalEngine:
    def __init__(self):
        self.logger = logging.getLogger("SignalEngine")
        
        # --- NEW_PRE 전략 파라미터 (튜닝 가능) ---
        self.MIN_GAP_PCT = 2.0          # 최소 갭상승 (%)
        self.MIN_PM_VOLUME = 10000      # 프리마켓 최소 누적 거래량
        self.STOP_LOSS_PCT = 0.02       # 기본 손절폭 (2%)
        self.TAKE_PROFIT_PCT = 0.04     # 기본 익절폭 (4%)
        self.BASE_QUANTITY = 10         # 기본 주문 수량 (자금 상황에 따라 조절 필요)
        # -------------------------------------

    def analyze(self, symbol: str, current_price: float, open_price: float, pm_volume: int) -> Optional[ActionPlan]:
        """
        시장 데이터를 받아 분석 후, 진입 시그널이 발생하면 ActionPlan 반환
        조건 불충족 시 None 반환
        """
        
        # 1. 기초 데이터 검증
        if current_price <= 0:
            return None

        # 2. [전략 로직] NEW_PRE (Gap & Momentum)
        # 등락률 계산 (전일 종가 대비 혹은 시가 대비)
        # 여기서는 간단히 Open Price(오늘 시가) 대비 상승률로 계산
        momentum_pct = ((current_price - open_price) / open_price) * 100
        
        # 조건 A: 갭 상승이 충분한가?
        if momentum_pct < self.MIN_GAP_PCT:
            return None
            
        # 조건 B: 거래량이 받쳐주는가?
        if pm_volume < self.MIN_PM_VOLUME:
            return None

        # 3. Confidence(확신도) 계산
        # 거래량이 기준보다 많을수록, 상승폭이 클수록 점수 Up
        score = 0.5 # 기본 점수
        if momentum_pct > 5.0: score += 0.2
        if pm_volume > 50000: score += 0.2
        
        confidence = min(score, 0.95) # 최대 0.95 제한

        # 4. 가격 산정
        entry_price = current_price
        stop_price = entry_price * (1 - self.STOP_LOSS_PCT)
        target_price = entry_price * (1 + self.TAKE_PROFIT_PCT)

        self.logger.info(f"✨ Signal Detected: {symbol} (Conf: {confidence}, Mom: {momentum_pct:.2f}%)")

        # 5. 불변 객체(ActionPlan) 생성 및 반환
        return ActionPlan(
            symbol=symbol,
            signal_type='LONG',
            confidence=confidence,
            reason=f"Gap {momentum_pct:.1f}% / Vol {pm_volume}",
            entry_price=entry_price,
            quantity=self.BASE_QUANTITY, # 추후 RiskManager나 자금관리 로직에서 동적 계산 가능
            stop_loss=stop_price,
            take_profit=[target_price]
        )