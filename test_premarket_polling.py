#!/usr/bin/env python3
# test_premarket_polling.py
import time
import logging
import yaml
from auth import TokenManager
from order import is_extended_hours, check_recent_executions

# 로깅 설정
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("PREMARKET_TEST")

def main():
    # 설정 로드
    with open("config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    tm = TokenManager(config)

    # 프리마켓 확인
    if not is_extended_hours():
        logger.error("현재 시간이 프리마켓(REST 폴링) 시간이 아닙니다.")
        return

    logger.info("✅ 프리마켓 모드: REST 폴링 테스트 시작")
    # 3회 폴링 테스트
    for i in range(3):
        logger.debug(f"🔄 폴링 시도 {i+1}/3")
        executions = check_recent_executions(tm, config)
        if executions:
            logger.info(f"✅ 체결감지: {len(executions)}건")
            for e in executions:
                logger.info(f"   • {e['ticker']} {e['quantity']}주 @ ${e['price']:.2f}")
        else:
            logger.warning("⚠️ 이번 폴링에서 체결 내역 없음")
        time.sleep(5)  # 5초 대기(설정에 맞춰 조정)

    logger.info("🔧 프리마켓 폴링 테스트 완료")

if __name__ == "__main__":
    main()
