#!/usr/bin/env python3
import os
import time
import logging
import dotenv
from auth import TokenManager
from order import OrderMonitor
from websocket_client import WebSocketClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("FULL_TEST")

def load_config_from_env():
    dotenv.load_dotenv(".env.production")
    return {
        "api_key": os.getenv("KIS_APP_KEY"),
        "api_secret": os.getenv("KIS_APP_SECRET"),
        "cano": os.getenv("KIS_ACCOUNT_NO"),
        "acnt_prdt_cd": os.getenv("KIS_PRODUCT_CODE"),
        "base_url": "https://openapi.koreainvestment.com:9443",
        "websocket_url": "ws://ops.koreainvestment.com:31000",
        "default_symbol": "AAPL",
        "mode": "development"  # 모의투자용
    }

def test_rest_polling(order_monitor, order_no):
    logger.info(f"🔍 REST 폴링 테스트 - 주문번호: {order_no}")
    for i in range(3):
        logger.info(f"⏱️ 폴링 {i+1}/3 ...")
        status = order_monitor.check_order_status(order_no)
        logger.info(f"➡️ 결과: {status}")
        time.sleep(5)

def test_websocket_connection(ws_client):
    logger.info("🔍 WebSocket 연결 테스트 시작")
    ws_client.start()

    for i in range(12):  # 1분 동안 모니터링
        time.sleep(5)
        status = ws_client.get_status()
        logger.info(f"▶ 상태: 연결={status['connected']} / 구독={status['subscribed']}")
        if status["connected"] and status["subscribed"]:
            logger.info("✅ 구독 성공 확인됨")
            break
    time.sleep(30)  # handshake 후 추가 대기
    ws_client.stop()
    logger.info("🛑 WebSocket 테스트 종료")

if __name__ == "__main__":
    config = load_config_from_env()
    tm = TokenManager({'api': {
        'app_key': config['api_key'],
        'app_secret': config['api_secret'],
        'base_url': config['base_url'],
        'websocket_url': config['websocket_url']
    }})
    order_monitor = OrderMonitor({
        'api_key': config['api_key'],
        'api_secret': config['api_secret'],
        'cano': config['cano'],
        'acnt_prdt_cd': config['acnt_prdt_cd'],
        'api': {'base_url': config['base_url']}
    }, tm)
    ws_client = WebSocketClient({
        'api': {'base_url': config['base_url'], 'websocket_url': config['websocket_url']},
        'trading': {'default_symbol': config['default_symbol']},
        'mode': config['mode']
    }, tm, message_handler=lambda data: logger.info(f"📈 실시간 체결 감지: {data}"))
    test_order_number = "30722955"
    test_rest_polling(order_monitor, test_order_number)
    test_websocket_connection(ws_client)
