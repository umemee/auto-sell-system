import sys
import os
import json
import logging

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from infra.kis_auth import KisAuth
from infra.kis_api import KisApi

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("BalanceDebugger")

def debug_balance():
    print("🔍 [Balance Debugger] 계좌 잔고 정밀 분석 시작...")
    
    # 1. 인프라 초기화
    try:
        auth = KisAuth()
        kis = KisApi(auth)
    except Exception as e:
        print(f"❌ 초기화 실패: {e}")
        return

    # 2. 계좌 정보 출력
    print(f"📋 설정된 계좌 정보: {Config.CANO} - {Config.ACNT_PRDT_CD}")
    
    # 3. API 호출 (원본 데이터 확인)
    path = "/uapi/overseas-stock/v1/trading/inquire-present-balance"
    # 실전/모의 구분
    tr_id = "VTRP6504R" if "vts" in kis.base_url else "CTRP6504R"
    
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
        "WCRC_FRCR_DVSN_CD": "02", # 01: 원화, 02: 외화
        "NATN_CD": "840", # 미국
        "TR_MK": "00",
        "INQR_DVSN_CD": "00"
    }
    
    import requests
    print(f"📡 API 요청 중... (URL: {kis.base_url})")
    try:
        res = requests.get(f"{kis.base_url}{path}", headers=headers, params=params)
        data = res.json()
        
        print("\n" + "="*40)
        print("📊 [API 응답 원본 분석]")
        print("="*40)
        
        if data['rt_cd'] != '0':
            print(f"❌ 조회 실패: {data['msg1']} (Code: {data['rt_cd']})")
            print("👉 힌트: 계좌번호나 API KEY 권한을 확인하세요.")
            return

        output2 = data.get('output2', [])
        if not output2:
            print("⚠️ 잔고 데이터 리스트(output2)가 비어있습니다.")
        else:
            balance_info = output2[0]
            # 주요 필드 출력
            print(f"💰 외화예수금 (frcr_dncl_amt_2):   ${balance_info.get('frcr_dncl_amt_2')}")
            print(f"💰 출금가능액 (frcr_drwg_psbl_amt_1): ${balance_info.get('frcr_drwg_psbl_amt_1')}")
            print(f"📅 결제잔액 (frcr_evlu_amt2):      ${balance_info.get('frcr_evlu_amt2')}")
            print("-" * 30)
            print("💡 해석:")
            print("   - 외화예수금/출금가능액이 0이면, 현재 '달러(USD)'가 없는 것입니다.")
            print("   - 만약 원화(KRW)로 입금하셨다면 '통합증거금' 상태일 수 있습니다.")
            print("   - 통합증거금을 사용하려면 원화를 달러로 환전 신청하거나,")
            print("     봇 코드를 수정하여 KRW 주문 가능액을 조회해야 합니다.")

        # 보유 주식 확인 (output1)
        output1 = data.get('output1', [])
        print(f"\n📦 보유 중인 종목 수: {len(output1)}")
        for item in output1:
            print(f"   - {item['ovrs_pdno']} ({item['ovrs_item_name']}): {item['ovrs_cblc_qty']}주")

    except Exception as e:
        print(f"❌ 에러 발생: {e}")

if __name__ == "__main__":
    debug_balance()
