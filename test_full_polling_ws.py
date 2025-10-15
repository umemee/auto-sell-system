#!/usr/bin/env python3

import os
import time
import json
import logging
import dotenv

from auth import TokenManager
from order import OrderMonitor
from websocket_client import WebSocketClient

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("FULL_TEST")


def check_env_variables():
    required_vars = ["KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO", "KIS_PRODUCT_CODE"]
    missing = [v for v in required_vars if not os.getenv(v)]
    if missing:
        logger.error(f"❌ 환경변수 누락: {missing}")
        return False
    logger.info("✅ 모든 환경변수가 설정되었습니다.")
    return True


def load_config_from_env():
    dotenv.load_dotenv(".env.production")
    if not check_env_variables():
        return None

    acc = os.getenv("KIS_ACCOUNT_NO").split("-")
    cano, prdt = acc[0], acc[1] if len(acc) == 2 else ("", "")
    config = {
        "api_key": os.getenv("KIS_APP_KEY"),
        "api_secret": os.getenv("KIS_APP_SECRET"),
        "cano": cano,
        "acnt_prdt_cd": prdt,
        "base_url": "https://openapi.koreainvestment.com:9443",
        "websocket_url": "ws://ops.koreainvestment.com:21000",
        "websocket": {"default_symbol": "AAPL"},
        "mode": "development",
        "trading": {"profit_margin": 0.03, "exchange_code": "NASD", "default_order_type": "00"}
    }
    logger.info(f"📋 Config 로드 완료: APP_KEY={config['api_key'][:8]}***, CANO={cano}")
    return config


def test_rest_polling(order_monitor, order_no):
    logger.info(f"🔍 REST 폴링 주문 상태 조회 테스트 - 주문번호: {order_no}")
    for attempt in range(1, 4):
        logger.info(f"🔄 REST 폴링 시도 {attempt}/3 - 시작")
        try:
            data = order_monitor.check_order_status(order_no)
            logger.debug(f"📥 REST 응답 데이터: {data}")
            if data and data.get("filled_qty", 0) > 0:
                logger.info(f"✅ REST 폴링 체결 감지: {data}")
                return True
            else:
                logger.warning(f"⚠️ 조회 결과 체결 없음 (attempt={attempt})")
        except Exception as e:
            logger.error(f"❌ REST 예외 발생 (attempt={attempt}): {e}", exc_info=True)
        time.sleep(5)
    return False


def test_websocket_connection(ws_client, symbol=None):
    symbol = symbol or ws_client.default_symbol
    logger.info("🔍 WebSocket 연결 테스트 시작")

    # WebSocket 메시지 핸들러에 상세 로그 추가
    def detailed_handler(raw_msg):
        logger.debug(f"📡 WebSocket 수신 원본 메시지: {raw_msg}")
        try:
            parsed = json.loads(raw_msg)
            logger.debug(f"📑 WebSocket 파싱 데이터: {parsed}")
        except Exception as e:
            logger.error(f"❌ WebSocket 메시지 처리 중 예외: {e}", exc_info=True)
        original_handler(parsed)

    original_handler = ws_client.message_handler
    ws_client.message_handler = detailed_handler

    # WebSocket 시작
    ws_client.start()

    # 연결될 때까지 대기 (최대 10초)
    start = time.time()
    while time.time() - start < 10:
        if ws_client.connected:
            logger.info(f"▶ WebSocket 연결됨 (connected={ws_client.connected})")
            break
        time.sleep(0.5)
    else:
        logger.error("❌ WebSocket 연결 실패")
        ws_client.stop()
        return False

    # 구독 요청 전송
    ws_client.subscribe(symbol)

    # 구독될 때까지 대기 (최대 10초)
    start = time.time()
    while time.time() - start < 10:
        if ws_client.subscribed:
            logger.info(f"▶ WebSocket 구독 확인: subscribed={ws_client.subscribed}")
            break
        time.sleep(0.5)
    else:
        logger.error("❌ WebSocket 구독 실패")
        ws_client.stop()
        return False

    # 추가 메시지 수신 대기 (30초)
    time.sleep(30)

    ws_client.stop()
    logger.info("🔍 WebSocket 테스트 완료")
    return True


if __name__ == "__main__":
    config = load_config_from_env()
    if not config:
        exit(1)

    tm = TokenManager({
        'api_key': config['api_key'],
        'api_secret': config['api_secret'],
        'api': {'base_url': config['base_url']},
        'websocket_url': config['websocket_url']
    })

    order_monitor = OrderMonitor(
        config={'api_key': config['api_key'], 'api_secret': config['api_secret'],
                'cano': config['cano'], 'acnt_prdt_cd': config['acnt_prdt_cd'],
                'api': {'base_url': config['base_url']}},
        token_manager=tm
    )

    ws_client = WebSocketClient(
        config={'api': {'base_url': config['base_url'], 'websocket_url': config['websocket_url']},
                'trading': config['trading'], 'mode': config['mode']},
        token_manager=tm,
        message_handler=lambda data: logger.info(f"📨 WebSocket 메시지: {data}")
    )

    test_order_number = "31083824"
    test_rest_polling(order_monitor, test_order_number)
    test_websocket_connection(ws_client)
