# main.py (Final Orchestrator)
import time
import logging
from datetime import datetime
import pytz

# --- Core Modules (Brain & Police) ---
from core.state_manager import StateManager, SystemState
from core.risk_manager import RiskManager
from core.signal_engine import SignalEngine
from core.action_plan import ActionPlan

# --- Infra & Data (Hands & Eyes) ---
from infra.kis_auth import KisAuth
from infra.kis_api import KisApi
from infra.telegram_bot import TelegramBot
from data.market_listener import MarketListener
import config  # 설정 파일

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("system.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)

def get_current_time_str():
    """현재 뉴욕 시간 반환 (시:분)"""
    ny_tz = pytz.timezone('America/New_York')
    now = datetime.now(ny_tz)
    return now.strftime("%H%M")

def main():
    print("🚀 NEW_PRE System Booting Up...")
    logger = logging.getLogger("Main")

    # 1. 인프라 초기화 (Auth, API, Bot)
    try:
        # kis_auth = KisAuth() # (필요시 활성화)
        # kis_auth.refresh_token() 
        # api = KisApi(token=kis_auth.get_token())
        
        # [테스트용 Mock API] 실제 API 연결 전 테스트를 위해 가짜 객체 사용 가능
        # 실전에서는 위 주석 해제하고 아래 api = ... 삭제
        api = KisApi(token="TEST_TOKEN") 
        
        bot = TelegramBot()
        bot.send_message("🤖 NEW_PRE 시스템이 시작되었습니다.")
    except Exception as e:
        logger.critical(f"❌ 인프라 초기화 실패: {e}")
        return

    # 2. 코어 모듈 초기화 (순서 중요)
    state_manager = StateManager()
    risk_manager = RiskManager(state_manager)
    signal_engine = SignalEngine()
    market_listener = MarketListener(api)

    # 3. 감시 대상 설정 (가정)
    # 실제로는 scanner.py의 로직을 통해 추출된 종목을 넣거나, 고정 리스트 사용
    target_symbols = [] 
    
    last_scan_time = 0
    SCAN_INTERVAL = 600 # 10분마다 재탐색

    logger.info("✅ System Initialized. Entering Main Loop.")

    try:
        while True:
            current_state = state_manager.get_state()
            current_time = int(get_current_time_str())
            now_ts = time.time()
            # =========================================================
            # [Phase 0] Gatekeeper: 시스템 상태 확인
            # =========================================================
            current_state = state_manager.get_state()
            
            # 🛑 킬 스위치 발동 상태
            if current_state == SystemState.HALTED:
                logger.warning("⛔ SYSTEM HALTED. Waiting for manual reset.")
                time.sleep(10)
                continue

            # =========================================================
            # [Phase 1] Time & State Transition (시간 관리)
            # =========================================================
            current_time = int(get_current_time_str()) # 예: 0930
            
            # [규칙] 04:00 이전에는 IDLE
            if current_state == SystemState.IDLE:
                if current_time >= 400 and current_time < 930:
                    state_manager.set_state(SystemState.SCANNING, "프리마켓 시작 시간 도달")
                else:
                    # 장 시작 전 대기
                    if int(time.time()) % 60 == 0: # 1분마다 로그
                        print(f"⏳ Waiting for Pre-market... (Current: {current_time})")
                    time.sleep(1)
                    continue

            # [규칙] 09:30 정규장 시작 시 강제 종료 (NEW_PRE 전략 종료)
            if current_time >= 930:
                if current_state != SystemState.HALTED:
                    state_manager.trigger_kill_switch("정규장 시작 (프리마켓 전략 종료)")
                    bot.send_message("🔔 정규장이 시작되어 시스템을 종료합니다.")
                continue

            # =========================================================
            # [Phase 2] Data & Signal (SCANNING 상태일 때만)
            # =========================================================
            if current_state == SystemState.SCANNING:
                
                # 🔄 [추가된 로직] 주기적 종목 탐색 (Discovery)
                # 타겟이 없거나, 마지막 스캔 후 10분이 지났으면 다시 스캔
                if not market_listener.target_symbols or (now_ts - last_scan_time > SCAN_INTERVAL):
                    logger.info("📡 Scanning market for new opportunities...")
                    found_symbols = market_listener.scan_for_candidates()
                    last_scan_time = now_ts
                    
                    if not found_symbols:
                        # 종목을 못 찾았으면 잠시 대기
                        time.sleep(5)
                        continue

                # 1. 데이터 수집 (현재 보고 있는 종목들)
                market_data = market_listener.get_market_data()
                
                for symbol, data in market_data.items():
                    # 2. 신호 분석 (Brain)
                    action_plan = signal_engine.analyze(
                        symbol=symbol,
                        current_price=data.get('price'),
                        open_price=data.get('open'),
                        pm_volume=data.get('vol')
                    )

                    if action_plan:
                        state_manager.set_state(SystemState.SIGNAL_LOCKED, f"Signal on {symbol}")
                        
                        # 3. 리스크 검증
                        # 자금 관리: Config에서 설정된 금액 사용
                        # 실전에서는 api.get_balance()로 예수금 조회 필요
                        account_balance = 10000.0 
                        
                        if risk_manager.check_entry_permit(action_plan, account_balance):
                            logger.info(f"🚀 Executing: {symbol}")
                            bot.send_message(f"🚀 진입: {symbol} @ {action_plan.entry_price}")
                            
                            # [주문 실행]
                            # success = api.send_order(...)
                            success = True # Mock
                            
                            if success:
                                state_manager.set_state(SystemState.IN_POSITION)
                                # 간단히 처리 후 쿨다운
                                state_manager.set_state(SystemState.COOLDOWN, "Entry Done")
                            else:
                                state_manager.set_state(SystemState.SCANNING, "Order Fail")
                        else:
                            state_manager.set_state(SystemState.SCANNING, "Risk Check Fail")

            # =========================================================
            # [Phase 4] Post-Trade / Cooldown 관리
            # =========================================================
            elif current_state == SystemState.COOLDOWN:
                # 쿨다운 로직 (예: 5초 후 다시 스캔 재개)
                # 실제로는 포지션 청산 여부 등을 확인해야 함
                logger.info("🧊 Cooldown... Resetting to SCANNING")
                time.sleep(5)
                state_manager.set_state(SystemState.SCANNING, "Cooldown finished")

            # 루프 속도 제어
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("👋 Manual Shutdown Initiated.")
        bot.send_message("👋 시스템이 수동으로 종료되었습니다.")
    except Exception as e:
        logger.critical(f"🔥 Critical Error: {e}")
        bot.send_message(f"🔥 시스템 크리티컬 에러 발생: {e}")
        # 크리티컬 에러 시 비상 정지
        state_manager.trigger_kill_switch("Uncaught Exception in Main Loop")

if __name__ == "__main__":
    main()