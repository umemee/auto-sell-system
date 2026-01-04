# core/state_manager.py
from enum import Enum, auto
import logging
from datetime import datetime

class SystemState(Enum):
    IDLE = auto()
    SCANNING = auto()
    SIGNAL_LOCKED = auto()
    IN_POSITION = auto()
    COOLDOWN = auto()
    HALTED = auto()

class StateManager:
    def __init__(self):
        self._current_state = SystemState.IDLE
        self.logger = logging.getLogger("StateManager")
        
        # [NEW] One-Shot Rule을 위한 메모리
        self.traded_symbols = set() 
        self.last_reset_date = datetime.now().date()
        
        self.logger.info(f"🆕 StateManager Initialized")

    def get_state(self) -> SystemState:
        return self._current_state

    def set_state(self, new_state: SystemState, reason: str = ""):
        if self._current_state == SystemState.HALTED and new_state != SystemState.IDLE:
            self.logger.warning(f"⛔ 차단됨: HALTED 상태 유지")
            return

        # 날짜 변경 시 거래 기록 리셋
        current_date = datetime.now().date()
        if current_date != self.last_reset_date:
            self.traded_symbols.clear()
            self.last_reset_date = current_date
            self.logger.info("📅 날짜 변경: 금일 거래 기록 리셋 완료")

        prev_state = self._current_state
        self._current_state = new_state
        
        log_msg = f"🔄 State Change: {prev_state.name} ➡️ {new_state.name}"
        if reason: log_msg += f" ({reason})"
        self.logger.info(log_msg)
        print(log_msg)

    def can_trade(self) -> bool:
        return self._current_state not in [SystemState.IDLE, SystemState.HALTED]

    def record_trade(self, symbol: str):
        """[One-Shot] 매매 발생 시 기록"""
        self.traded_symbols.add(symbol)
        self.logger.info(f"✅ [One-Shot] {symbol} 금일 매매 완료 처리 (재진입 불가)")

    def is_traded_today(self, symbol: str) -> bool:
        """[One-Shot] 금일 매매 여부 확인"""
        # 날짜 변경 체크
        if datetime.now().date() != self.last_reset_date:
            self.traded_symbols.clear()
            self.last_reset_date = datetime.now().date()
            
        return symbol in self.traded_symbols

    def trigger_kill_switch(self, reason: str):
        self.logger.critical(f"🚨 KILL SWITCH: {reason}")
        self.set_state(SystemState.HALTED, reason=reason)