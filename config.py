import os
import logging
from dotenv import load_dotenv

# .env.production 파일 로드 (없으면 시스템 환경변수 사용)
# 같은 폴더에 .env.production이 있다고 가정합니다.
load_dotenv(dotenv_path=".env.production")

class Config:
    # 1. 계좌 및 인증 정보 (환경변수에서 로드)
    # .env 파일 안의 변수명(KIS_APPKEY 등)을 정확히 맞춰야 합니다.
    APP_KEY = os.getenv("KIS_APPKEY")
    APP_SECRET = os.getenv("KIS_APPSECRET")
    
    # 계좌번호 (하이픈 제외 8자리 / 뒤 2자리)
    # .env에 CANO, ACNT_PRDT_CD로 저장되어 있다고 가정
    CANO = os.getenv("CANO")
    ACNT_PRDT_CD = os.getenv("ACNT_PRDT_CD", "01") # 기본값 01

    # URL 설정 (실전 투자용)
    URL_BASE = "https://openapi.koreainvestment.com:9443"

    # 2. 시스템 설정
    TOTAL_BUDGET_USD = 30     # 예산 제한 ($30)
    TARGET_SYMBOL = "TSLA"    # 타겟 종목
    
    # 이평선 계산용 데이터 개수
    CANDLE_LIMIT = 200

    @classmethod
    def check_settings(cls):
        """설정 로드 확인용 (비밀키 일부 마스킹 출력)"""
        if not cls.APP_KEY or not cls.APP_SECRET:
            print("❌ [오류] .env.production 파일을 찾을 수 없거나 키가 없습니다.")
            return False
        print(f"✅ 설정 로드 완료: APP_KEY={cls.APP_KEY[:4]}****, 계좌={cls.CANO}")
        return True