# check_account.py
import os
import requests
import json
from datetime import datetime
from config import load_config
from auth import TokenManager

def check_api_status():
    print("🔍 [진단] API 및 계좌 상태 점검 시작...")
    
    # 1. 설정 로드
    try:
        config = load_config('production')
        print("✅ 설정 로드 완료")
    except Exception as e:
        print(f"❌ 설정 로드 실패: {e}")
        return

    # 2. 토큰 발급
    try:
        tm = TokenManager(config)
        token = tm.get_access_token()
        if not token:
            print("❌ 토큰 발급 실패")
            return
        print(f"✅ 토큰 발급 성공: {token[:10]}...")
    except Exception as e:
        print(f"❌ 토큰 발급 중 오류: {e}")
        return

    # 공통 헤더
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": config['api_key'],
        "appsecret": config['api_secret'],
        "tr_id": "TTTS3012R",  # 주식 잔고 조회
        "custtype": "P"
    }

    # 3. 현재 보유 잔고 조회 (Balance)
    print("\n📋 [1] 현재 보유 주식(잔고) 조회 중...")
    url_bal = f"{config['api']['base_url']}/uapi/overseas-stock/v1/trading/inquire-balance"
    params_bal = {
        "CANO": config['cano'],
        "ACNT_PRDT_CD": config['acnt_prdt_cd'],
        "OVRS_EXCG_CD": "NASD",
        "TR_CRCY_CD": "USD",
        "CTX_AREA_FK200": "",
        "CTX_AREA_NK200": ""
    }

    res_bal = requests.get(url_bal, headers=headers, params=params_bal)
    if res_bal.status_code == 200:
        data = res_bal.json()
        holdings = data.get('output2', []) # output2가 종목 리스트
        print(f"✅ 조회 성공! (총 {len(holdings)}개 종목 보유 중)")
        
        if not holdings:
            print("   ⚠️ 보유 중인 주식이 없습니다.")
        
        for stock in holdings:
            name = stock.get('ovrs_item_name')
            code = stock.get('ovrs_pdno')
            qty = stock.get('ovrs_cblc_qty')
            price = stock.get('now_pric2')
            print(f"   👉 {name}({code}): {qty}주 (현재가: ${price})")
    else:
        print(f"❌ 잔고 조회 실패: {res_bal.text}")

    # 4. 당일 체결 내역 조회 (History)
    print("\n📋 [2] 오늘(당일) 매수 체결 내역 조회 중...")
    headers['tr_id'] = "TTTS3035R" # 체결 내역 조회 TR
    url_hist = f"{config['api']['base_url']}/uapi/overseas-stock/v1/trading/inquire-ccnl"
    today = datetime.now().strftime("%Y%m%d")
    
    params_hist = {
        "CANO": config['cano'],
        "ACNT_PRDT_CD": config['acnt_prdt_cd'],
        "PDNO": "",
        "ORD_STRT_DT": today,
        "ORD_END_DT": today,
        "SLL_BUY_DVSN": "02", # 매수만
        "CCLD_NCCS_DVSN": "01", # 체결만
        "OVRS_EXCG_CD": "NASD",
        "CTX_AREA_NK200": "",
        "CTX_AREA_FK200": ""
    }
    
    res_hist = requests.get(url_hist, headers=headers, params=params_hist)
    if res_hist.status_code == 200:
        data = res_hist.json()
        orders = data.get('output', [])
        print(f"✅ 조회 성공! (오늘 체결된 매수: {len(orders)}건)")
        
        if not orders:
            print("   ⚠️ 오늘 체결된 매수 주문이 없습니다. (그래서 자동매도가 안 걸린 것입니다)")
        
        for order in orders:
            name = order.get('prdt_name')
            qty = order.get('ft_ccld_qty')
            price = order.get('ft_ccld_unpr3')
            print(f"   👉 {name}: {qty}주 @ ${price}")
    else:
        print(f"❌ 체결 내역 조회 실패: {res_hist.text}")

if __name__ == "__main__":
    check_api_status()