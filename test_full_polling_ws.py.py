#!/usr/bin/env python3

# test_full_polling_ws.py - 수정된 버전 (CANO 필드 오류 해결)

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
            logger.error(f"  {var}=your_value_here")
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
    
    cano = account_parts[0]  # "12345678"
    acnt_prdt_cd = account_parts[1]  # "01"
    
    config = {
        'api_key': os.getenv("KIS_APP_KEY"),
        'api_secret': os.getenv("KIS_APP_SECRET"),
        'cano': cano,
        'acnt_prdt_cd': acnt_prdt_cd,
        'account_no': account_no,
        'base_url': "https://openapi.koreainvestment.com:9443",
        'websocket_url': "ws://ops.koreainvestment.com:31000",
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
    
    for i in range(3):
        logger.info(f"🔄 REST 폴링 시도 {i+1}/3")
        
        status = order_monitor.check_order_status(order_no)
        if status:
            logger.info(f"✅ 조회 성공: {status}")
        else:
            logger.warning("⚠️ 조회 실패 또는 체결 내역 없음")
        
        if i < 2:  # 마지막 시도가 아니면 대기
            time.sleep(5)

def test_websocket_connection(ws_client):
    """WebSocket 연결 테스트"""
    logger.info("🔍 WebSocket 연결 테스트 시작")
    
    # WebSocket 클라이언트 시작
    ws_client.start()
    
    # 60초 동안 연결 상태 모니터링 (12회 × 5초)  
    for i in range(12):
        time.sleep(5)
        status = ws_client.get_status()
        logger.info(f"▶ WebSocket 상태: 연결={status.get('connected', False)}, 구독={status.get('subscribed', False)}")
        
        if status.get('connected', False) and status.get('subscribed', False):
            logger.info("🎉 WebSocket 연결 및 구독 성공!")
            break
    
    # 추가로 30초 더 대기하여 실제 메시지 수신 여부 확인
    time.sleep(30)
    
    # WebSocket 중지
    ws_client.stop()
    logger.info("🔍 WebSocket 테스트 완료")

if __name__ == "__main__":
    # 설정 로드
    config = load_config_from_env()
    if not config:
        logger.error("설정 로드 실패")
        exit(1)
    
    # TokenManager 초기화
    tm = TokenManager(
        api_key=config['api_key'],
        api_secret=config['api_secret'],
        api={'base_url': config['base_url']},
        websocket_url=config['websocket_url']
    )
    
    # OrderMonitor 초기화 - 올바른 config 구조로 수정
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
    
    # WebSocket 연결 테스트  
    test_websocket_connection(ws_client)