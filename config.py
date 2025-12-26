# config.py
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env.production")

class Config:
    # 1. 계좌 및 인증
    APP_KEY = os.getenv("KIS_APP_KEY") or os.getenv("KIS_APPKEY")
    APP_SECRET = os.getenv("KIS_APP_SECRET") or os.getenv("KIS_APPSECRET")

    _ACC_NO = os.getenv("KIS_ACCOUNT_NO") or os.getenv("CANO")
    if _ACC_NO and "-" in _ACC_NO:
        CANO, ACNT_PRDT_CD = _ACC_NO.split("-")
    else:
        CANO = _ACC_NO
        ACNT_PRDT_CD = os.getenv("ACNT_PRDT_CD", "01")

    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

    URL_BASE = "https://openapi.koreainvestment.com:9443"
    BASE_URL = URL_BASE 

    # 2. [변경] 다중 종목 리스트 (여기가 식판일세!)
    # 나중에 스캐너가 이 리스트를 자동으로 채워줄 것이네.
    TARGET_SYMBOLS = ["SIDU", "PCLA"] 
    
    EXCHANGE_CD = "NASD"
    
    # 3. 전략 설정 (총 예산)
    TOTAL_BUDGET_USD = 30     # 전체 예산
    
    TIMEFRAME_1M = "1M"      
    TIMEFRAME_5M = "5M"      
    CANDLE_LIMIT = 100
    RATE_LIMIT_DELAY = 1.2   # 종목이 늘어나니 딜레이를 살짝 늘리겠네

    @classmethod
    def check_settings(cls):
        if not cls.APP_KEY or not cls.APP_SECRET:
            print(f"❌ [오류] .env 로드 실패")
            return False
        return True