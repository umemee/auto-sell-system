import os
from dotenv import load_dotenv

# .env.production 파일 로드
load_dotenv(dotenv_path=".env.production")

class Config:
    # 1. 계좌 및 인증 정보
    APP_KEY = os.getenv("KIS_APPKEY")
    APP_SECRET = os.getenv("KIS_APPSECRET")
    CANO = os.getenv("CANO")
    ACNT_PRDT_CD = os.getenv("ACNT_PRDT_CD", "01")

    # 2. API URL (여기가 문제의 핵심이었네!)
    # 어떤 파일은 URL_BASE를 찾고, 어떤 파일은 BASE_URL을 찾으니 둘 다 정의해버리는 것일세.
    URL_BASE = "https://openapi.koreainvestment.com:9443"
    BASE_URL = URL_BASE  # [핵심] BASE_URL을 요청해도 URL_BASE를 주도록 별칭(Alias) 설정

    # 3. 전략 설정 (Gap-Zone Scalper)
    TOTAL_BUDGET_USD = 30     # 예산
    TARGET_SYMBOL = "TSLA"    # 타겟 종목
    EXCHANGE_CD = "NASD"      # 거래소 코드
    
    # 4. 타임프레임 (여기도 안전하게 둘 다 설정)
    TIMEFRAME_1 = "1M"       
    TIMEFRAME_2 = "5M"       
    TIMEFRAME_1M = "1M"      
    TIMEFRAME_5M = "5M"      

    # 5. 데이터 처리 설정
    CANDLE_LIMIT = 200       
    RATE_LIMIT_DELAY = 1.0   

    @classmethod
    def check_settings(cls):
        if not cls.APP_KEY or not cls.APP_SECRET:
            print("❌ [오류] .env 파일 로드 실패")
            return False
        return True