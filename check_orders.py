# check_orders.py
import os
from dotenv import load_dotenv
from config import load_config
from auth import TokenManager
import requests
from datetime import datetime
import json

# 1. 환경 변수 로드
load_dotenv()

try:
    # 2. 설정 로드 (강제 production 모드)
    config = load_config('production')
    print("✅ 설정 로드 성공")

    # 3. 토큰 발급
    tm = TokenManager(config)
    token = tm.get_access_token()
    print(f"✅ 토큰 발급 성공: {token[:10]}...")

    # 4. 주문 체결 내역 조회 (오늘 날짜)
    url = f"{config['api']['base_url']}/uapi/overseas-stock/v1/trading/inquire-ccnl"
    today = datetime.now().strftime("%Y%m%d") # 올바른 날짜 형식

    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": config['api_key'],
        "appsecret": config['api_secret'],
        "tr_id": "TTTS3035R",
        "custtype": "P"
    }

    params = {
        "CANO": config['cano'],
        "ACNT_PRDT_CD": config['acnt_prdt_cd'],
        "PDNO": "",
        "ORD_STRT_DT": today,
        "ORD_END_DT": today,
        "SLL_BUY_DVSN": "02", # 매수
        "CCLD_NCCS_DVSN": "01", # 체결
        "OVRS_EXCG_CD": "NASD",
        "CTX_AREA_NK200": "",
        "CTX_AREA_FK200": ""
    }

    print(f"🚀 API 요청 날짜: {today}")
    resp = requests.get(url, headers=headers, params=params)
    data = resp.json()

    print("\n=== 응답 결과 ===")
    print(json.dumps(data, indent=2, ensure_ascii=False))

except Exception as e:
    print(f"❌ 오류 발생: {e}")