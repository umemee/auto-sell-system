import sys
import os
import time
import json
import logging

# 상위 폴더 경로 추가 (config.py 인식용)
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from infra.kis_auth import KisAuth
from infra.kis_api import KisApi

# 로거 설정 (콘솔 출력용)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SellDebugger")

def debug_sell_logic():
    print("\n" + "="*60)
    print("🕵️‍♂️ [Manual Sell Debugger] 자동 매도 실패 원인 규명")
    print("="*60)

    # 1. 초기화
    try:
        auth = KisAuth()
        kis = KisApi(auth)
        print("✅ API 초기화 성공")
    except Exception as e:
        print(f"❌ 초기화 실패: {e}")
        return

    # 2. 보유 종목 확인
    print("\n📡 보유 종목 조회 중...")
    holdings = kis.get_balance()
    
    if not holdings:
        print("❌ 보유 중인 종목이 없습니다. 테스트 불가.")
        return

    print(f"📋 보유 리스트: {[h['symbol'] for h in holdings]}")
    
    # 3. 테스트할 종목 선택
    target_symbol = input("\n👉 테스트할 종목 코드를 입력하세요 (예: TNMG): vero").strip().upper()
    
    # 해당 종목 보유 확인
    target_holding = next((h for h in holdings if h['symbol'] == target_symbol), None)
    if not target_holding:
        print(f"❌ {target_symbol} 종목을 보유하고 있지 않습니다.")
        return

    print(f"\n✅ {target_symbol} 선택됨. (보유수량: {target_holding['qty']})")
    print("⚠️ 주의: 실제 주문이 전송됩니다. 테스트를 위해 '1주'만 매도합니다.")
    
    confirm = input("👉 진행하시겠습니까? (yes 입력): ")
    if confirm.lower() != 'yes':
        print("🛑 취소되었습니다.")
        return

    # 4. 현재가 조회
    price_data = kis.get_current_price("NASD", target_symbol)
    if not price_data: # 여기서 실패하면 get_current_price가 리턴값이 없는 것(이전 코드 이슈)
        # get_current_price가 float만 리턴하도록 수정되었는지 확인 필요
        # 만약 None이 리턴된다면 직접 조회 시도
        print("⚠️ 현재가 조회 함수 실패 -> 로우 데이터 조회 시도")
        pass # 아래 로직에서 처리

    # kis_api.py의 get_current_price가 float를 반환한다고 가정 (수정된 버전)
    # 만약 수정 전이라면 딕셔너리일 수 있음.
    current_price = 0.0
    if isinstance(price_data, float) or isinstance(price_data, int):
        current_price = float(price_data)
    elif isinstance(price_data, dict):
        current_price = float(price_data.get('last', 0))
    
    if current_price <= 0:
        print(f"❌ 현재가 조회 실패. 테스트 불가.")
        return

    print(f"\n💵 현재 시장가: ${current_price}")

    # =========================================================
    # 🧪 [실험 A] 정상적인 -2% 매도 (코드 무결성 검증)
    # =========================================================
    price_a = current_price * 0.98
    print(f"\n[실험 A] 정상 범위 매도 시도 (현재가 -2%: ${price_a:.2f})")
    
    ord_no_a = kis.sell_market(target_symbol, 1, price_hint=current_price) 
    # 주의: 위 함수는 내부적으로 로직이 캡슐화되어 있어, 
    # 정확한 디버깅을 위해 아래처럼 '직접' API를 쏘는 코드를 사용합니다.
    
    # 직접 구현한 주문 로직 (kis_api.py 로직 흉내 + 로그 강화)
    _manual_order(kis, target_symbol, 1, price_a, "실험_A_정상범위")


    # =========================================================
    # 🧪 [실험 B] 과격한 -15% 매도 (IGW00009 재현 검증)
    # =========================================================
    price_b = current_price * 0.85
    print(f"\n[실험 B] 과격한 할인 매도 시도 (현재가 -15%: ${price_b:.2f})")
    print("👉 이 실험에서 에러가 나면 '가격 괴리'가 원인입니다.")
    
    _manual_order(kis, target_symbol, 1, price_b, "실험_B_과격할인")


def _manual_order(kis, symbol, qty, price, label):
    """API 로직을 우회하여 직접 요청을 쏘고 원본 응답을 확인"""
    import requests
    import json
    
    path = "/uapi/overseas-stock/v1/trading/order"
    kis._update_headers("TTTT1006U") # 매도 TR

    # 가격 포맷팅 (소수점 처리 로직 검증)
    if price < 1.0:
        formatted_price = f"{price:.4f}"
    else:
        formatted_price = f"{price:.2f}"
    
    print(f"   📤 전송 가격 포맷: {formatted_price}")

    data = {
        "CANO": Config.CANO,
        "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
        "OVRS_EXCG_CD": "NASD",
        "PDNO": symbol,
        "ORD_DVSN": "00", 
        "ORD_QTY": str(int(qty)),
        "OVRS_ORD_UNPR": formatted_price, 
        "ORD_SVR_DVSN_CD": "0"
    }
    
    print(f"   📦 JSON Body: {json.dumps(data)}")

    try:
        res = requests.post(f"{kis.base_url}{path}", headers=kis.headers, data=json.dumps(data))
        resp_json = res.json()
        
        print(f"   📥 응답 코드: {resp_json.get('rt_cd')}")
        print(f"   📥 응답 메시지: {resp_json.get('msg1')}")
        print(f"   📥 상세 코드: {resp_json.get('msg_cd')}")
        
        if resp_json.get('rt_cd') == '0':
            print(f"   ✅ {label} 주문 성공! (주문번호: {resp_json['output']['ODNO']})")
            print("   👉 HTS/MTS에서 바로 주문 취소하세요!")
        else:
            print(f"   ❌ {label} 주문 실패!")
            if resp_json.get('msg_cd') == 'IGW00009':
                print("   🚨 [결론] 가격 괴리(Fat Finger) 에러 확인됨.")
            else:
                print("   🚨 [결론] 파라미터 또는 다른 로직 에러.")

    except Exception as e:
        print(f"   ❌ 통신 에러: {e}")

if __name__ == "__main__":
    debug_sell_logic()