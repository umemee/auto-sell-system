import sys
import os
import time
import requests
import json

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from infra.kis_auth import KisAuth
from infra.utils import get_logger

logger = get_logger("DIAGNOSIS")

def diagnose():
    print("\n🚀 [정밀 진단 시작] 잔고와 시세 데이터를 해부합니다.\n")
    
    # 1. 인증 초기화
    auth = KisAuth()
    token = auth.get_token()
    base_url = Config.BASE_URL
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": Config.APP_KEY,
        "appsecret": Config.APP_SECRET,
        "tr_id": "",
        "custtype": "P"
    }
    
    # ---------------------------------------------------------
    # 2. 잔고 진단 (숨겨진 $300 찾기)
    # ---------------------------------------------------------
    print("🔹 [진단 1] '매수가능금액조회(TTTS3007R)' API 테스트")
    headers["tr_id"] = "TTTS3007R" # 실전용 (모의는 VTTS3007R)
    
    params = {
        "CANO": Config.CANO,
        "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
        "OVRS_EXCG_CD": "NASD",
        "OVRS_ORD_UNPR": "0", # 시장가 기준
        "ITEM_CD": "AAPL"     # 기준 종목 (애플)
    }
    
    try:
        res = requests.get(f"{base_url}/uapi/overseas-stock/v1/trading/inquire-psamount", headers=headers, params=params)
        data = res.json()
        
        if data['rt_cd'] == '0':
            # 여기서 'ord_psbl_qty'(수량)와 'frcr_ord_psbl_amt1'(금액)이 나옵니다.
            result = data['output']
            cash_amt = result.get('frcr_ord_psbl_amt1', '0')
            qty = result.get('ord_psbl_qty', '0')
            print(f"✅ 성공! 찾은 주문가능금액: ${cash_amt} (애플 기준 주문가능수량: {qty}주)")
            print(f"   👉 이 API로 get_buyable_cash 함수를 교체해야 합니다.")
        else:
            print(f"❌ 실패: {data.get('msg1')}")
            print(f"   상세: {data}")
    except Exception as e:
        print(f"❌ 에러 발생: {e}")

    print("-" * 50)

    # ---------------------------------------------------------
    # 3. AMD 시세 조회 진단
    # ---------------------------------------------------------
    print("🔹 [진단 2] AMD 시세 조회 실패 원인 분석")
    headers["tr_id"] = "HHDFS00000300" # 현재가 조회
    
    params_price = {
        "AUTH": "",
        "EXCD": "NAS", # 나스닥은 NAS
        "SYMB": "AMD"
    }
    
    try:
        res = requests.get(f"{base_url}/uapi/overseas-price/v1/quotations/price", headers=headers, params=params_price)
        data = res.json()
        
        if data['rt_cd'] == '0':
            price = data['output']['last']
            print(f"✅ AMD 시세 조회 성공: ${price}")
        else:
            print(f"❌ AMD 시세 조회 실패: {data.get('msg1')}")
            print(f"   응답 코드: {data.get('msg_cd')}")
            # 흔한 원인: 장 시작 전, 지연 시세 신청 안함, 토큰 권한 문제 등
    except Exception as e:
        print(f"❌ 에러 발생: {e}")

if __name__ == "__main__":
    diagnose()
