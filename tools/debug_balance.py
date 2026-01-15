import sys
import os
import logging

# [필수] 상위 폴더(config.py가 있는 곳)를 인식하도록 경로 강제 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from infra.kis_auth import KisAuth
from infra.kis_api import KisApi

# 로그 레벨 설정
logging.basicConfig(level=logging.INFO)

def debug_balance():
    print("🔍 [Balance Debugger] 계좌 잔고 재진단 (Fix Verification)")
    print("👉 수정된 KisApi가 정상 작동하는지 검증합니다.")
    
    try:
        # 1. 초기화 (Auth & API)
        auth = KisAuth()
        kis = KisApi(auth) 
    except Exception as e:
        print(f"❌ 초기화 실패: {e}")
        return

    print(f"📋 계좌 정보: {Config.CANO} - {Config.ACNT_PRDT_CD}")
    
    # 2. 잔고 조회 실행
    # (수정된 kis_api.py는 내부적으로 AAPL/0원 파라미터를 사용하여 Code 7 에러를 방지합니다)
    print("📡 API 요청 중... (get_buyable_cash)")
    
    cash = kis.get_buyable_cash()
    
    print("\n" + "="*40)
    print(f"💰 조회 결과 (주문 가능 외화)")
    print("="*40)
    
    if cash > 0:
        print(f"✅ 성공: ${cash:,.2f}")
        print("👉 시스템(RealPortfolio) 정상 가동 가능 확인 완료.")
    else:
        # 잔고가 정말 0원일 수도 있고, 에러일 수도 있음
        print(f"⚠️ 결과: ${cash:,.2f}")
        print("   (만약 실제 잔고가 있는데 0으로 뜬다면, 터미널 위쪽의 [KisApi] 에러 로그를 확인하세요)")
        print("   1. KIS 앱 > 해외주식 > 예수금 확인")
        print("   2. 해외주식 거래 신청 계좌인지 확인")

if __name__ == "__main__":
    debug_balance()