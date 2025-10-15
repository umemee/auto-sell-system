#!/usr/bin/env python3
# test_full_polling_ws.py - 환경변수 누락 및 config 키 구조 수정된 버전

import os
import time
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
            logger.error(f"   {var}=your_value_here")
        return False
    
    logger.info("✅ 모든 환경변수가 설정되었습니다.")
    return True

def load_config_from_env():
    dotenv.load_dotenv(".env.production")
    
    if not check_env_variables():
        return None
        
    config = {
        "api_key": os.getenv("KIS_APP_KEY"),
        "api_secret": os.getenv("KIS_APP_SECRET"),
        "cano": os.getenv("KIS_ACCOUNT_NO"),
        "acnt_prdt_cd": os.getenv("KIS_PRODUCT_CODE"),
        "base_url": "https://openapi.koreainvestment.com:9443",
        "websocket_url": "ws://ops.koreainvestment.com:31000",  # /websocket 제거
        "default_symbol": "AAPL",
        "mode": "development"  # 모의투자용
    }
    
    logger.info(f"📋 Config 로드 완료: APP_KEY={config['api_key'][:8]}***")
    return config

def test_rest_polling(order_monitor, order_no):
    logger.info(f"🔍 REST 폴링 주문 상태 조회 테스트 - 주문번호: {order_no}")
    for i in range(3):
        logger.info(f"🔄 REST 폴링 시도 {i+1}/3")
        status = order_monitor.check_order_status(order_no)
        if status:
            logger.info(f"✅ 상태 조회 성공: {status}")
        else:
            logger.warning("⚠️ 조회 실패 또는 체결 내역 없음")
        time.sleep(5)

def test_websocket_connection(ws_client):
    logger.info("🔍 WebSocket 연결 테스트 시작")
    ws_client.start()
    
    # 60초간 상태 출력 (12 * 5초)
    for i in range(12):
        time.sleep(5)
        status = ws_client.get_status()
        logger.info(f"▶ WebSocket 상태: 연결={status['connected']}, 구독={status['subscribed']}")
        if status["connected"] and status["subscribed"]:
            logger.info("✅ WebSocket 구독 성공!")
            break
    
    # 추가 30초 대기
    time.sleep(30)
    ws_client.stop()
    logger.info("🛑 WebSocket 연결 테스트 종료")

if __name__ == "__main__":
    config = load_config_from_env()
    
    if not config:
        logger.error("❌ 환경변수 설정 후 다시 실행하세요.")
        exit(1)
    
    # TokenManager 인스턴스 생성 - 올바른 키 구조 사용
    tm = TokenManager({
        'api_key': config['api_key'],
        'api_secret': config['api_secret'],
        'api': {
            'base_url': config['base_url'],
            'websocket_url': config['websocket_url']
        }
    })
    
    # OrderMonitor (REST 폴링) 생성
    order_monitor = OrderMonitor({
        'api_key': config['api_key'],
        'api_secret': config['api_secret'],
        'cano': config['cano'],
        'acnt_prdt_cd': config['acnt_prdt_cd'],
        'api': {
            'base_url': config['base_url']
        }
    }, tm)
    
    # WebSocketClient (본장 실시간 감시) 생성
    ws_client = WebSocketClient({
        'api': {
            'base_url': config['base_url'],
            'websocket_url': config['websocket_url']
        },
        'trading': {'default_symbol': config['default_symbol']},
        'mode': config['mode']
    }, tm, message_handler=lambda data: logger.info(f"📈 실시간 체결 감지: {data}"))
    
    # 테스트 주문번호 지정 (실제 주문번호로 변경)
    test_order_number = "31083824"
    
    # REST 폴링 테스트 - 프리마켓 환경
    test_rest_polling(order_monitor, test_order_number)
    
    # WebSocket 테스트 - 본장 환경
    test_websocket_connection(ws_client)

