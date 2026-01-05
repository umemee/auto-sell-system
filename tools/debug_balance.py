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
    print("🔍 [Balance Debugger] 계좌 잔고 정밀 분석 시작...")
    
    try:
        auth = KisAuth()
        kis = KisApi(auth)
    except Exception as e:
        print(f"❌ 초기화 실패: {e}")
        return

    print(f"📋 설정된 계좌 정보: {Config.CANO} - {Config.ACNT_PRDT_CD}")
    
    path = "/uapi/overseas-stock/v1/trading/inquire-present-balance"
    tr_id = "VTRP6504R" if "vts" in kis.base_url else "CTRP6504R"
    
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {auth.get_token()}",
        "appkey": Config.APP_KEY,
        "appsecret": Config.APP_SECRET,
        "tr_id": tr_id,
        "custtype": "P"
    }
    
    # [Fix] TR_MK 제거 (분석 내용 반영)
    params = {
        "CANO": Config.CANO,
        "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
        "WCRC_FRCR_DVSN_CD": "02",
        "NATN_CD": "840",
        "INQR_DVSN_CD": "00"
    }
    
    import requests
    print(f"📡 API 요청 중... (TR_ID: {tr_id})")
    try:
        res = requests.get(f"{kis.base_url}{path}", headers=headers, params=params)
        data = res.json()
        
        print("\n" + "="*40)
        print("📊 [API 응답 결과]")
        print("="*40)
        
        if data.get('rt_cd') != '0':
            print(f"❌ 조회 실패: {data.get('msg1')} (Code: {data.get('rt_cd')})")
            print("👉 힌트: 계좌번호 확인, .env 파일 확인")
            return

        output2 = data.get('output2', [])
        if output2:
            balance_info = output2[0]
            usd_cash = balance_info.get('frcr_dncl_amt_2')
            withdrawable = balance_info.get('frcr_drwg_psbl_amt_1')
            print(f"💰 외화예수금 (USD): ${usd_cash}")
            print(f"💰 출금가능액 (USD): ${withdrawable}")
        else:
            print("⚠️ 잔고 데이터가 비어있습니다.")

        print("-" * 30)
        output1 = data.get('output1', [])
        print(f"📦 보유 종목 수: {len(output1)}")
        for item in output1:
            sym = item.get('ovrs_pdno')
            name = item.get('ovrs_item_name')
            qty = item.get('ovrs_cblc_qty')
            print(f"   - {sym} ({name}): {qty}주")

    except Exception as e:
        print(f"❌ 에러 발생: {e}")

if __name__ == "__main__":
    debug_balance()
