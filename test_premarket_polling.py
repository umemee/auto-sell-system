#!/usr/bin/env python3
# test_premarket_polling.py

import time
import logging
import yaml
from auth import TokenManager
from order import OrderMonitor

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
    om = OrderMonitor(config, tm)

    # 테스트용 주문번호를 실제 미체결 주문번호로 교체하세요
    test_order_no = "30722955"

    logger.info("✅ 프리마켓 모드: REST 폴링 주문상태 조회 테스트 시작")
    for i in range(3):
        logger.debug(f"🔄 조회 시도 {i+1}/3 – 주문번호: {test_order_no}")
        status = om.check_order_status(test_order_no)
        if status:
            logger.info(f"✅ 조회 성공: 상태={status['status']}, 체결수량={status['filled_qty']}, 체결가={status['filled_price']}")
        else:
            logger.warning("⚠️ 조회 실패 또는 체결 내역 없음")
        time.sleep(5)  # config.yaml polling.smart.initial_interval과 맞춰 조정

    logger.info("🔧 프리마켓 REST 폴링 테스트 완료")

if __name__ == "__main__":
    main()
