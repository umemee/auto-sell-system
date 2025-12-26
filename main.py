# main.py
import time
import os
from kis_auth import TokenManager
from kis_api import KisApi
from strategy import GapZoneScalper
from utils import get_logger, is_market_open

logger = get_logger()

def main():
    logger.info("=== KIS US Scalper System Started ===")
    
    # 1. 인프라 초기화
    token_manager = TokenManager()
    api = KisApi(token_manager)
    strategy = GapZoneScalper(api)
    
    logger.info(f"Target: {strategy.symbol} | Budget: ${Config.TOTAL_BUDGET_USD}")

    # 2. 메인 루프
    while True:
        try:
            # [Kill Switch] STOP.txt 파일이 있으면 종료
            if os.path.exists("STOP.txt"):
                logger.info("Kill Switch Activated (STOP.txt found). Exiting...")
                break

            # [Scheduler] 장 운영 시간 체크
            # if not is_market_open():
            #     logger.info("Market is Closed. Waiting... (Check every 60s)")
            #     time.sleep(60)
            #     continue
            # (개발 중 테스트를 위해 주석 처리, 실전 시 주석 해제)

            # 3. 데이터 갱신 및 전략 실행
            if strategy.update_market_data():
                
                # 매수 로직
                if strategy.check_entry_signal():
                    strategy.execute_buy()
                
                # 매도 로직 (보유 시)
                strategy.check_exit_signal()
            
            # [Smart Polling] 1초 대기 (API Rate Limit 보호)
            time.sleep(1.0)

        except Exception as e:
            logger.error(f"Critical Error in Main Loop: {e}")
            time.sleep(5) # 에러 발생 시 잠시 대기 후 재시도

if __name__ == "__main__":
    main()