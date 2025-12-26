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

    # 2. API URL (실전 투자)
    URL_BASE = "https://openapi.koreainvestment.com:9443"

    # 3. 전략 설정 (Gap-Zone Scalper)
    TOTAL_BUDGET_USD = 30     # 예산
    TARGET_SYMBOL = "TSLA"    # 타겟 종목
    EXCHANGE_CD = "NASD"      # 거래소 코드 (나스닥)
    
    # 4. [중요] 타임프레임 설정 (변수명 불일치 방지용으로 다 넣음)
    TIMEFRAME_1 = "1M"       # 1분봉
    TIMEFRAME_2 = "5M"       # 5분봉
    TIMEFRAME_1M = "1M"      # Strategy가 이걸 찾아서 에러가 났었음! (추가됨)
    TIMEFRAME_5M = "5M"      # 혹시 몰라 추가함

    # 5. 데이터 처리 설정
    CANDLE_LIMIT = 200       # 이평선 계산용 데이터 개수
    RATE_LIMIT_DELAY = 1.0   # 1초 딜레이

    @classmethod
    def check_settings(cls):
        if not cls.APP_KEY or not cls.APP_SECRET:
            print("❌ [오류] .env 파일 로드 실패")
            return False
        return True