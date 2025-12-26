import os
import logging
from dotenv import load_dotenv

# 1. .env 파일 로드 (서버 환경변수 우선 적용)
# 현재 디렉토리의 .env.production 파일을 찾아서 로드합니다.
load_dotenv(dotenv_path=".env.production")

class Config:
    # 2. 계좌 및 인증 정보 (.env에서 가져옴)
    APP_KEY = os.getenv("KIS_APPKEY")
    APP_SECRET = os.getenv("KIS_APPSECRET")
    
    # 계좌번호 (하이픈 제외 8자리 / 뒤 2자리)
    CANO = os.getenv("CANO")
    ACNT_PRDT_CD = os.getenv("ACNT_PRDT_CD", "01")

    # 3. 실전 투자용 URL (모의투자 아님!)
    URL_BASE = "https://openapi.koreainvestment.com:9443"

    # 4. 전략 파라미터 (Gap-Zone Scalper)
    TOTAL_BUDGET_USD = 30     # 예산 ($30)
    TARGET_SYMBOL = "TSLA"    # 타겟 종목
    TARGET_EXCHANGE = "NASD"  # 거래소 (나스닥)

    # 5. 시스템 설정
    CANDLE_LIMIT = 200        # 이평선 계산용 데이터 개수
    RATE_LIMIT_DELAY = 1.0    # API 호출 간격 (1초)

    @classmethod
    def check_settings(cls):
        """설정 로드 확인용"""
        if not cls.APP_KEY or not cls.APP_SECRET:
            print("❌ [오류] .env.production 파일을 찾을 수 없거나 키가 없습니다.")
            return False
        return True