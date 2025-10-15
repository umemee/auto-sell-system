#!/usr/bin/env python3

# test_full_polling_ws.py - REST 폴링 및 WebSocket 구독/수신 상세 로그 추가 버전

import os
import time
import json
import logging
import dotenv

from auth import TokenManager
from order import OrderMonitor
from websocket_client import WebSocketClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("FULL_TEST")


def check_env_variables():
    """환경변수 확인 및 누락된 변수 알림"""
    required_vars = ["KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO", "KIS_PRODUCT_CODE"]
    missing_vars = []
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)
    if missing_vars:
        logger.error(f"❌ 환경변수 누락: {missing_vars}")
        logger.error("💡 .env.production 파일에 다음 변수들을 설정하세요:")
        for var in missing_vars:
            logger.error(f" {var}=your_value_here")
        return False
    logger.info("✅ 모든 환경변수가 설정되었습니다.")
    return True


def load_config_from_env():
    """환경변수에서 설정 로드 - CANO 처리 수정"""
    dotenv.load_dotenv(".env.production")
    if not check_env_variables():
        return None

    # 계좌번호 파싱 (예: "12345678-01")
    account_no = os.getenv("KIS_ACCOUNT_NO")
    account_parts = account_no.split('-')
    if len(account_parts) != 2:
        logger.error(f"❌ 계좌번호 형식 오류: {account_no} (올바른 형식: 12345678-01)")
        return None
    cano = account_parts[0]       # "12345678"
    acnt_prdt_cd = account_parts[1]  # "01"

    config = {
        'api_key': os.getenv("KIS_APP_KEY"),
        'api_secret': os.getenv("KIS_APP_SECRET"),
        'cano': cano,
        'acnt_prdt_cd': acnt_prdt_cd,
        'account_no': account_no,
        'base_url': "https://openapi.koreainvestment.com:9443",
        'websocket_url': "ws://ops.koreainvestment.com:21000",
        'websocket': {
            'default_symbol': "AAPL"
        },
        'mode': "development"
    }
    logger.info(f"📋 Config 로드 완료: APP_KEY={config['api_key'][:8]}***, CANO={config['cano']}")
    return config


def test_rest_polling(order_monitor, order_no):
    """REST 폴링 주문 상태 조회 테스트"""
    logger.info(f"🔍 REST 폴링 주문 상태 조회 테스트 - 주문번호: {order_no}")
    for attempt in range(1, 4):
        logger.info(f"🔄 REST 폴링 시도 {attempt}/3 - 시작")
        try:
            data = order_monitor.check_order_status(order_no)
            logger.debug(f"📥 REST 응답 데이터: {data}")
            if data and data.get("체결수량", 0) > 0:
                logger.info(f"✅ REST 폴링 체결 감지: {data}")
                return True
            else:
                logger.warning(f"⚠️ 조회 결과 체결 없음 (attempt={attempt})")
        except Exception as e:
            logger.error(f"❌ REST 예외 발생 (attempt={attempt}): {e}", exc_info=True)
        time.sleep(5)
    return False


def test_websocket_connection(ws_client, symbol=None):
    """WebSocket 연결 및 메시지 수신 테스트"""
    symbol = symbol or ws_client.config['trading']['default_symbol']
    logger.info("🔍 WebSocket 연결 테스트 시작")

    # WebSocket 메시지 핸들러에 상세 로그 삽입
    def detailed_message_handler(raw_msg):
        logger.debug(f"📡 WebSocket 수신 원본 메시지: {raw_msg}")
        try:
            parsed = json.loads(raw_msg)
            logger.debug(f"📑 WebSocket 파싱 데이터: {parsed}")
        except Exception as e:
            logger.error(f"❌ WebSocket 메시지 처리 중 예외: {e}", exc_info=True)
        # 기존 핸들러 로직 호출
        original_handler(parsed)

    # 기존 핸들러 보존 및 교체
    original_handler = ws_client.message_handler
    ws_client.message_handler = detailed_message_handler

    # WebSocket 클라이언트 시작 및 구독 요청
    ws_client.start()
    ws_client.subscribe(symbol)
    logger.info(f"▶ WebSocket 구독 요청 전송: 종목={symbol}")

    # 60초 동안 연결 및 구독 상태 확인
    start = time.time()
    while time.time() - start < 60:
        if ws_client._connected and ws_client._subscribed:
            logger.info(f"▶ WebSocket 상태 확인: 연결={ws_client._connected}, 구독={ws_client._subscribed}")
        time.sleep(1)

    # 추가로 30초간 메시지 수신 대기
    time.sleep(30)

    # WebSocket 중지
    ws_client.stop()
    logger.info("🔍 WebSocket 테스트 완료")
    return False


if __name__ == "__main__":
    # 설정 로드
    config = load_config_from_env()
    if not config:
        logger.error("설정 로드 실패")
        exit(1)

    # TokenManager 초기화
    tm = TokenManager({
        'api_key': config['api_key'],
        'api_secret': config['api_secret'],
        'api': {'base_url': config['base_url']},
        'websocket_url': config['websocket_url']
    })

    # OrderMonitor 초기화
    order_monitor = OrderMonitor(
        config={
            'api_key': config['api_key'],
            'api_secret': config['api_secret'],
            'cano': config['cano'],
            'acnt_prdt_cd': config['acnt_prdt_cd'],
            'api': {'base_url': config['base_url']}
        },
        token_manager=tm
    )

    # WebSocketClient 초기화
    ws_client = WebSocketClient(
        config={
            'api': {'base_url': config['base_url'], 'websocket_url': config['websocket_url']},
            'trading': {'default_symbol': config['websocket']['default_symbol']},
            'mode': config['mode']
        },
        token_manager=tm,
        message_handler=lambda data: logger.info(f"📨 WebSocket 메시지: {data}")
    )

    # 테스트할 주문번호 (실제 주문번호로 변경하세요)
    test_order_number = "31083824"

    # REST 폴링 테스트
    test_rest_polling(order_monitor, test_order_number)

    # WebSocket 연결 및 메시지 수신 테스트
    test_websocket_connection(ws_client)
