# core/risk_manager.py
import logging
from core.state_manager import StateManager, SystemState
from core.action_plan import ActionPlan

class RiskManager:
    def __init__(self, state_manager: StateManager):
        self.state_manager = state_manager
        self.logger = logging.getLogger("RiskManager")
        
        # --- [Auditor Approved Policy] ---
        # 이 기준을 변경하려면 Auditor 컨펌 필수
        self.MAX_DAILY_LOSS_PCT = -3.0   # 일일 손실 한도 (%)
        self.MAX_CONSECUTIVE_LOSS = 3    # 연속 손실 허용 횟수
        self.MAX_SLIPPAGE_PCT = 0.5      # 허용 슬리피지 (%)
        # ---------------------------------
        
        # 일일 상태 추적 변수
        self.current_daily_loss_pct = 0.0
        self.consecutive_loss_count = 0

    def check_entry_permit(self, plan: ActionPlan, account_balance: float) -> bool:
        """
        진입 전 최종 리스크 점검 (Gatekeeper)
        True 반환 시에만 주문 실행 가능
        """
        # 1. 시스템 상태 확인
        if not self.state_manager.can_trade():
            self.logger.warning(f"⛔ 진입 거부: 시스템이 {self.state_manager.get_state().name} 상태입니다.")
            return False

        # 2. 일일 손실 한도 체크 (Kill Switch)
        if self.current_daily_loss_pct <= self.MAX_DAILY_LOSS_PCT:
            self.state_manager.trigger_kill_switch(f"일일 손실 한도 초과 ({self.current_daily_loss_pct}%)")
            return False

        # 3. 연속 손실 체크 (Cooldown)
        if self.consecutive_loss_count >= self.MAX_CONSECUTIVE_LOSS:
            self.logger.warning(f"🧊 진입 거부: 연속 {self.consecutive_loss_count}회 손실로 인한 쿨다운 필요")
            self.state_manager.set_state(SystemState.COOLDOWN, reason="연속 손실 과다")
            return False

        # 4. Action Plan 무결성 체크
        try:
            plan.validate()
        except ValueError as e:
            self.logger.error(f"❌ 진입 거부: Action Plan 오류 - {e}")
            return False

        return True

    def record_trade_result(self, pnl_pct: float):
        """매매 종료 후 결과 업데이트 (손실 누적 등)"""
        self.current_daily_loss_pct += pnl_pct
        
        if pnl_pct < 0:
            self.consecutive_loss_count += 1
        else:
            self.consecutive_loss_count = 0 # 수익 나면 연속 손실 카운트 초기화
            
        # 결과 반영 후 즉시 리스크 상태 재점검
        if self.current_daily_loss_pct <= self.MAX_DAILY_LOSS_PCT:
            self.state_manager.trigger_kill_switch(f"매매 후 일일 손실 한도 도달 ({self.current_daily_loss_pct}%)")