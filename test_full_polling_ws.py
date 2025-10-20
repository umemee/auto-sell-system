#!/usr/bin/env python3
"""
기본 연결 테스트 - 장 시작 전에도 실행 가능
"""

import logging
from config import load_config
from auth import TokenManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def test_config_loading():
    """설정 파일 로드 테스트"""
    print("\n" + "="*60)
    print("1️⃣ 설정 파일 로드 테스트")
    print("="*60)
    
    try:
        config = load_config('production')
        print(f"✅ 설정 로드 성공")
        print(f"   - API Key: {config['api_key'][:10]}...")
        print(f"   - 계좌번호: {config['cano']}-{config['acnt_prdt_cd']}")
        print(f"   - 거래소: {config['trading']['exchange_code']}")
        return config
    except Exception as e:
        print(f"❌ 설정 로드 실패: {e}")
        return None


def test_token_generation(config):
    """토큰 발급 테스트"""
    print("\n" + "="*60)
    print("2️⃣ Access Token 발급 테스트")
    print("="*60)
    
    try:
        token_manager = TokenManager(config)
        
        # Access Token 발급
        access_token = token_manager.get_access_token()
        if access_token:
            print(f"✅ Access Token 발급 성공")
            print(f"   - Token: {access_token[:20]}...")
            print(f"   - 만료시간: {token_manager.token_expires_at}")
        else:
            print(f"❌ Access Token 발급 실패")
            return None
        
        # WebSocket Approval Key 발급
        approval_key = token_manager.get_approval_key()
        if approval_key:
            print(f"✅ Approval Key 발급 성공")
            print(f"   - Key: {approval_key[:20]}...")
        else:
            print(f"❌ Approval Key 발급 실패")
        
        return token_manager
        
    except Exception as e:
        print(f"❌ 토큰 발급 오류: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_api_connection(config, token_manager):
    """API 연결 테스트 (주문조회)"""
    print("\n" + "="*60)
    print("3️⃣ API 연결 테스트 (주문조회)")
    print("="*60)
    
    import requests
    from datetime import datetime
    
    try:
        url = f"{config['api']['base_url']}/uapi/overseas-stock/v1/trading/inquire-nccs"
        token = token_manager.get_access_token()
        
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": config['api_key'],
            "appsecret": config['api_secret'],
            "tr_id": "TTTS3035R"
        }
        
        today = datetime.now().strftime("%Y%m%d")
        params = {
            "CANO": config['cano'],
            "ACNT_PRDT_CD": config['acnt_prdt_cd'],
            "OVRS_EXCG_CD": "NASD",
            "ORD_STRT_DT": today,
            "ORD_END_DT": today,
            "SLL_BUY_DVSN_CD": "02",
            "CCLD_DVSN": "01",
            "PDNO": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }
        
        response = requests.get(url, headers=headers, params=params, timeout=15)
        
        print(f"   - HTTP 상태: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            rt_cd = data.get("rt_cd")
            msg1 = data.get("msg1", "")
            
            if rt_cd == "0":
                orders = data.get("output", [])
                print(f"✅ API 연결 성공")
                print(f"   - 오늘 체결된 주문: {len(orders)}건")
                
                if orders:
                    print(f"\n📋 최근 주문 내역:")
                    for i, order in enumerate(orders[:3], 1):
                        print(f"   {i}. {order.get('pdno')} - {order.get('ccld_qty')}주")
            else:
                print(f"❌ API 오류: {msg1}")
        else:
            print(f"❌ HTTP 오류: {response.text}")
            
    except Exception as e:
        print(f"❌ API 연결 오류: {e}")
        import traceback
        traceback.print_exc()


def test_websocket_connection(config, token_manager):
    """WebSocket 연결 테스트 (간단 버전)"""
    print("\n" + "="*60)
    print("4️⃣ WebSocket 연결 테스트")
    print("="*60)
    
    try:
        from websocket_client import WebSocketClient
        import time
        
        # 간단한 메시지 핸들러
        def simple_handler(data):
            print(f"📨 WebSocket 메시지 수신: {data}")
        
        ws_client = WebSocketClient(config, token_manager, simple_handler)
        
        print("   - WebSocket URL:", config['api'].get('websocket_url'))
        print("   - 연결 시도 중...")
        
        # 연결 시작
        ws_client.start()
        
        # 10초 대기
        time.sleep(10)
        
        # 상태 확인
        status = ws_client.get_status()
        print(f"\n📊 WebSocket 상태:")
        print(f"   - 연결됨: {status['connected']}")
        print(f"   - 구독됨: {status['subscribed']}")
        print(f"   - 실행중: {status['running']}")
        print(f"   - 재연결 횟수: {status['reconnect_count']}")
        
        if status['connected'] and status['subscribed']:
            print(f"✅ WebSocket 연결 및 구독 성공")
        else:
            print(f"⚠️ WebSocket 연결/구독 미완료")
            print(f"💡 정규장 시간(ET 09:30-16:00)에 다시 시도하세요")
        
        # 연결 종료
        ws_client.stop()
        
    except Exception as e:
        print(f"❌ WebSocket 테스트 오류: {e}")
        import traceback
        traceback.print_exc()


def main():
    print("\n" + "#"*60)
    print("# 한국투자증권 자동매매 시스템 - 기본 연결 테스트")
    print("#"*60)
    
    # 1. 설정 로드
    config = test_config_loading()
    if not config:
        print("\n❌ 설정 로드 실패로 테스트 중단")
        return
    
    # 2. 토큰 발급
    token_manager = test_token_generation(config)
    if not token_manager:
        print("\n❌ 토큰 발급 실패로 테스트 중단")
        return
    
    # 3. API 연결 테스트
    test_api_connection(config, token_manager)
    
    # 4. WebSocket 연결 테스트
    test_websocket_connection(config, token_manager)
    
    print("\n" + "="*60)
    print("✅ 모든 기본 테스트 완료")
    print("="*60)
    print("\n💡 다음 단계:")
    print("   1. 프리마켓 시작(ET 04:00, KST 18:00) 후 실제 동작 확인")
    print("   2. 소액으로 실제 매수 후 자동 매도 테스트")
    print("   3. 텔레그램 알림 수신 확인")


if __name__ == "__main__":
    main()