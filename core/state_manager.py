# core/state_manager.py
from enum import Enum, auto
from dataclasses import dataclass
import logging

# 1. 상태 정의 (State Enum)
class SystemState(Enum):
    IDLE = auto()           # 장 시작 전 대기
    SCANNING = auto()       # 탐색 중 (기본)
    SIGNAL_LOCKED = auto()  # 신호 포착 (검증 단계)
    IN_POSITION = auto()    # 진입 완료 (매수 잔고 보유)
    COOLDOWN = auto()       # 매매 종료 후 휴식
    HALTED = auto()         # 비상 정지 (Kill Switch)

class StateManager:
    def __init__(self):
        self._current_state = SystemState.IDLE
        self.logger = logging.getLogger("StateManager")
        self.logger.info(f"🆕 StateManager Initialized: {self._current_state.name}")

    def get_state(self) -> SystemState:
        """현재 상태 반환 (읽기 전용)"""
        return self._current_state

    def set_state(self, new_state: SystemState, reason: str = ""):
        """
        상태 변경 (로그 기록 필수)
        HALTED 상태에서는 수동 리셋 전까지 변경 불가하도록 방어 로직 추가 가능
        """
        if self._current_state == SystemState.HALTED and new_state != SystemState.IDLE:
            self.logger.warning(f"⛔ 차단됨: HALTED 상태에서는 {new_state.name}로 변경할 수 없습니다.")
            return

        prev_state = self._current_state
        self._current_state = new_state
        
        log_msg = f"🔄 State Change: {prev_state.name} ➡️ {new_state.name}"
        if reason:
            log_msg += f" ({reason})"
        
        self.logger.info(log_msg)
        print(log_msg) # 콘솔 출력용

    def can_trade(self) -> bool:
        """
        현재 매매 프로세스를 진행해도 되는지 확인
        HALTED나 IDLE 상태면 False
        """
        return self._current_state not in [SystemState.IDLE, SystemState.HALTED]

    def trigger_kill_switch(self, reason: str):
        """비상 정지 발동"""
        self.logger.critical(f"🚨 KILL SWITCH TRIGGERED: {reason}")
        self.set_state(SystemState.HALTED, reason=reason)