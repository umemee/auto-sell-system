import sys
import os
import json
import logging

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from infra.kis_auth import KisAuth
from infra.kis_api import KisApi

logging.basicConfig(level=logging.INFO)

def debug_balance():
    print("🔍 [Balance Debugger] 계좌 '주문 가능 금액(Buying Power)' 정밀 분석")
    
    try:
        auth = KisAuth()
        kis = KisApi(auth)
    except Exception as e:
        print(f"❌ 초기화 실패: {e}")
        return

    print(f"📋 계좌 정보: {Config.CANO} - {Config.ACNT_PRDT_CD}")
    
    # ---------------------------------------------------------
    # [Target API] inquire-psamount (실제 주문 가능 금액)
    # ---------------------------------------------------------
    path = "/uapi/overseas-stock/v1/trading/inquire-psamount"
    tr_id = "TTTS3007R" # 실전 투자용 ID
    
    # 모의투자인 경우 URL/TR_ID 변경 필요 (Config 확인)
    if "vts" in kis.base_url:
        tr_id = "VTTS3007R"

    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {auth.get_token()}",
        "appkey": Config.APP_KEY,
        "appsecret": Config.APP_SECRET,
        "tr_id": tr_id,
        "custtype": "P"
    }
    
    params = {
        "CANO": Config.CANO,
        "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
        "OVRS_EXCG_CD": "NASD",
        "OVRS_ORD_UNPR": "",
        "ITEM_CD": ""
    }
    
    print(f"📡 API 요청 중... (TR_ID: {tr_id})")
    try:
        import requests
        res = requests.get(f"{kis.base_url}{path}", headers=headers, params=params)
        data = res.json()
        
        print("\n" + "="*40)
        print("📊 [API 응답 결과]")
        print("="*40)
        
        if data.get('rt_cd') != '0':
            print(f"❌ 조회 실패: {data.get('msg1')} (Code: {data.get('rt_cd')})")
            return

        output = data.get('output', {})
        
        # [중요] 실제 주문에 사용되는 필드
        buying_power = output.get('frcr_ord_psbl_amt1', '0')
        
        print(f"💰 주문 가능 외화(USD): ${buying_power}")
        print(f"👉 이 금액이 RealPortfolio에서 'self.balance'로 사용됩니다.")
        print("-" * 40)
        print(f"Raw Output: {json.dumps(output, indent=2, ensure_ascii=False)}")

    except Exception as e:
        print(f"❌ 실행 중 에러 발생: {e}")

if __name__ == "__main__":
    debug_balance()