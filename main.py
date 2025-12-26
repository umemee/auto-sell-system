import time
import os
import sys

# 1. Config 클래스 가져오기 (이게 빠져서 에러가 난 걸세!)
from config import Config
from kis_auth import TokenManager
from kis_api import KisApi
from strategy import GapZoneScalper
from utils import get_logger

# 로거 설정
logger = get_logger()

def main():
    logger.info("=== KIS US Scalper System Started ===")
    
    # 1. 토큰 관리자 초기화
    token_manager = TokenManager()
    
    # 2. API 래퍼 초기화
    api = KisApi(token_manager)
    
    # 3. 전략 엔진 초기화
    try:
        strategy = GapZoneScalper(api)
    except Exception as e:
        logger.error(f"전략 초기화 실패: {e}")
        return

    # [수정 포인트] 에러가 났던 부분 안전하게 변경
    # strategy.symbol 대신 Config.TARGET_SYMBOL을 직접 사용
    logger.info(f"Target: {Config.TARGET_SYMBOL} | Budget: ${Config.TOTAL_BUDGET_USD}")
    
    # 4. 메인 루프 시작
    while True:
        try:
            # 4-1. 킬 스위치 체크
            if os.path.exists("STOP.txt"):
                logger.info("⛔ Kill Switch Detected (STOP.txt). System Exit.")
                break

            # 4-2. 장 운영 시간 체크 (utils.py에 함수가 있다고 가정)
            # 개발 중에는 주석 처리하거나, utils.is_market_open() 구현 필요
            # if not is_market_open():
            #     time.sleep(60)
            #     continue

            # 4-3. 전략 실행 (데이터 갱신 -> 매수/매도 판별)
            # update_market_data가 True일 때만(데이터가 유효할 때만) 로직 수행
            if strategy.update_market_data():
                
                # 매수 신호 확인
                if strategy.check_entry_signal():
                    strategy.execute_buy()
                
                # 매도 신호 확인 (이미 보유 중인 경우 내부에서 처리)
                strategy.check_exit_signal()
            
            # 4-4. Rate Limit 준수 (1초 대기)
            time.sleep(Config.RATE_LIMIT_DELAY)

        except KeyboardInterrupt:
            logger.info("System stopped by user.")
            break
        except Exception as e:
            logger.error(f"Critical Error in Main Loop: {e}")
            time.sleep(5) # 에러 발생 시 5초 대기

if __name__ == "__main__":
    main()