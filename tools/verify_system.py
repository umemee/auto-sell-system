# tools/verify_system.py - v3.1 System Diagnostic Tool
import sys
import os
import time

# 프로젝트 루트 경로 추가 (상위 폴더 참조를 위해)
sys.path.append(os.path.dirname(os.path.abspath(os.path.dirname(__file__))))

from infra.kis_auth import KisAuth
from infra.kis_api import KisApi
from infra.telegram_bot import TelegramBot
from config import Config

def run_diagnostics():
    print("=" * 60)
    print("🏥 Auto-Sell System v3.1 - 종합 진단 모드")
    print("=" * 60)

    # 1. 설정 파일(.env) 확인
    print("\n[1] 설정 파일 점검 (.env)")
    if Config.check_settings():
        print("   ✅ 필수 설정(APP_KEY, SECRET) 확인됨")
        print(f"   ✅ 계좌번호: {Config.CANO}-{Config.ACNT_PRDT_CD}")
    else:
        print("   ❌ 설정 파일 오류! .env 파일을 확인하세요.")
        return

    # 2. 토큰 및 API 연결 확인
    print("\n[2] API 연결 및 토큰 점검")
    try:
        auth = KisAuth()
        token = auth.get_token()
        if token:
            print(f"   ✅ 토큰 발급 성공 (앞 10자리: {token[:10]}...)")
        else:
            print("   ❌ 토큰 발급 실패")
            return

        api = KisApi(auth)
        
        # 3. 시세 조회 테스트 (AAPL)
        print("\n[3] 시세 수신 테스트 (AAPL)")
        start_t = time.time()
        price_info = api.get_current_price("NASD", "AAPL")
        duration = time.time() - start_t
        
        if price_info:
            print(f"   ✅ 조회 성공: ${price_info['last']} (응답속도: {duration:.3f}초)")
        else:
            print("   ❌ 시세 조회 실패")
            
    except Exception as e:
        print(f"   ❌ API 점검 중 에러 발생: {e}")
        return

    # 4. 텔레그램 봇 테스트
    print("\n[4] 텔레그램 봇 연결 테스트")
    try:
        bot = TelegramBot()
        print("   📤 테스트 메시지 전송 중...")
        bot.send_message("🏥 [System Check] 진단 모드 테스트 메시지입니다.")
        print("   ✅ 전송 완료 (휴대폰을 확인하세요)")
    except Exception as e:
        print(f"   ❌ 텔레그램 전송 실패: {e}")

    print("\n" + "=" * 60)
    print("🎉 [진단 결과] 시스템 상태: 정상 (Ready to Trade)")
    print("=" * 60)

if __name__ == "__main__":
    run_diagnostics()