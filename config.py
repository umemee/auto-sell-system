import os
from dotenv import load_dotenv

# .env.production 파일 로드
load_dotenv(dotenv_path=".env.production")

class Config:
    # 1. 계좌 및 인증 정보 (자네의 .env 변수명에 맞게 자동 매핑)
    # KIS_APP_KEY로 저장되어 있어도 읽고, KIS_APPKEY로 저장되어 있어도 읽습니다.
    APP_KEY = os.getenv("KIS_APP_KEY") or os.getenv("KIS_APPKEY")
    APP_SECRET = os.getenv("KIS_APP_SECRET") or os.getenv("KIS_APPSECRET")

    # 계좌번호 처리 (KIS_ACCOUNT_NO="12345678-01" 형태 대응)
    _ACC_NO = os.getenv("KIS_ACCOUNT_NO") or os.getenv("CANO")
    
    if _ACC_NO and "-" in _ACC_NO:
        # 하이픈(-)을 기준으로 앞뒤를 자릅니다.
        CANO, ACNT_PRDT_CD = _ACC_NO.split("-")
    else:
        CANO = _ACC_NO
        ACNT_PRDT_CD = os.getenv("ACNT_PRDT_CD", "01")

    # 2. API URL (URL_BASE, BASE_URL 둘 다 지원)
    URL_BASE = "https://openapi.koreainvestment.com:9443"
    BASE_URL = URL_BASE 

    # 3. 전략 설정 (Gap-Zone Scalper)
    TOTAL_BUDGET_USD = 30     # 예산
    TARGET_SYMBOL = "TSLA"    # 타겟 종목
    EXCHANGE_CD = "NASD"      # 거래소 코드
    
    # 4. 타임프레임
    TIMEFRAME_1 = "1M"       
    TIMEFRAME_2 = "5M"       
    TIMEFRAME_1M = "1M"      
    TIMEFRAME_5M = "5M"      

    # 5. 데이터 처리 설정
    CANDLE_LIMIT = 100       
    RATE_LIMIT_DELAY = 1.0   

    @classmethod
    def check_settings(cls):
        # APP_KEY가 제대로 로드되었는지 확인
        if not cls.APP_KEY or not cls.APP_SECRET:
            print(f"❌ [오류] .env 파일 로드 실패 (KEY 못찾음)")
            print(f"   현재 인식된 키: {cls.APP_KEY}")
            return False
        return True