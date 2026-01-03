# core/action_plan.py
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime

@dataclass(frozen=True)
class ActionPlan:
    """
    [Contract] 전략(Brain) -> 실행(Execution)으로 전달되는 불변의 행동 지침
    생성 이후 절대 수정될 수 없음 (frozen=True)
    """
    # 1. 기본 식별 정보
    symbol: str
    signal_type: str        # 'LONG' only for NEW_PRE
    
    # 2. 판단 근거 (로그 및 사후 분석용)
    confidence: float       # 0.0 ~ 1.0
    reason: str             # 예: "Pre-market volume surge"
    
    # 3. 구체적 집행 수치 (Execution이 그대로 따를 것)
    entry_price: float      # 진입 희망가 (지정가)
    quantity: int           # 진입 수량
    
    # 4. 청산 계획 (Risk Manager가 검증할 항목)
    stop_loss: float        # 손절가 (필수)
    take_profit: List[float] # 익절가 리스트 (분할 매도 등)
    
    # 5. 메타 데이터
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def validate(self):
        """기본적인 데이터 무결성 검증"""
        if self.quantity <= 0:
            raise ValueError("수량은 0보다 커야 합니다.")
        if self.stop_loss >= self.entry_price and self.signal_type == 'LONG':
            raise ValueError("LONG 포지션의 StopLoss는 진입가보다 낮아야 합니다.")