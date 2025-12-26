import time
import os
import sys
from datetime import datetime

# 모듈 임포트
from config import Config
from kis_auth import TokenManager
from kis_api import KisApi
from strategy import GapZoneScalper
from utils import get_logger

# 로거 설정
logger = get_logger()

def main():
    logger.info("=== KIS US Scalper System Started ===")
    
    # 1. 초기화
    token_manager = TokenManager()
    api = KisApi(token_manager)
    
    try:
        strategy = GapZoneScalper(api)
    except Exception as e:
        logger.error(f"전략 초기화 실패: {e}")
        return

    logger.info(f"Target: {Config.TARGET_SYMBOL} | Budget: ${Config.TOTAL_BUDGET_USD}")
    
    # [추가] 마지막 생존신고 시간 기록용 변수
    last_heartbeat = 0
    
    # 2. 메인 루프
    while True:
        try:
            # 2-1. 킬 스위치
            if os.path.exists("STOP.txt"):
                logger.info("⛔ Kill Switch Detected. Exiting.")
                break

            # 2-2. 데이터 갱신 및 전략 실행
            # (데이터가 갱신되지 않아도 루프는 돕니다)
            has_data = strategy.update_market_data()
            
            if has_data:
                # 매수 신호 확인
                if strategy.check_entry_signal():
                    strategy.execute_buy()
                
                # 매도 신호 확인
                strategy.check_exit_signal()
            
            # 2-3. [NEW] 10초마다 생존 신고 (Heartbeat)
            # 현재가가 0이 아닐 때만 출력
            current_ts = time.time()
            if current_ts - last_heartbeat > 10:
                price = getattr(strategy, 'current_price', 0)
                if price > 0:
                    logger.info(f"💓 [생존신고] 감시중... 현재가: ${price} (Target: {Config.TARGET_SYMBOL})")
                else:
                    logger.info(f"💓 [생존신고] 데이터 수신 대기중... (Target: {Config.TARGET_SYMBOL})")
                last_heartbeat = current_ts

            # 2-4. Rate Limit 준수
            time.sleep(Config.RATE_LIMIT_DELAY)

        except KeyboardInterrupt:
            logger.info("System stopped by user.")
            break
        except Exception as e:
            logger.error(f"Critical Error in Main Loop: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()