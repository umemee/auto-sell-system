import os
from dotenv import load_dotenv

# .env.production 파일 로드
load_dotenv(dotenv_path=".env.production")

class Config:
    # 1. 계좌 및 인증 정보 (자동 매핑: 언더바 유무 상관없이 다 읽음)
    APP_KEY = os.getenv("KIS_APP_KEY") or os.getenv("KIS_APPKEY")
    APP_SECRET = os.getenv("KIS_APP_SECRET") or os.getenv("KIS_APPSECRET")

    # 계좌번호 (하이픈 있어도 되고 없어도 됨)
    _ACC_NO = os.getenv("KIS_ACCOUNT_NO") or os.getenv("CANO")
    if _ACC_NO and "-" in _ACC_NO:
        CANO, ACNT_PRDT_CD = _ACC_NO.split("-")
    else:
        CANO = _ACC_NO
        ACNT_PRDT_CD = os.getenv("ACNT_PRDT_CD", "01")

    # [NEW] 텔레그램 설정 (여기가 빠져서 에러가 난 걸세!)
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

    # 2. API URL (NASD -> NAS 변환 이슈 해결용)
    URL_BASE = "https://openapi.koreainvestment.com:9443"
    BASE_URL = URL_BASE 

    # 3. 전략 설정 (Gap-Zone Scalper)
    TOTAL_BUDGET_USD = 30     # 예산
    TARGET_SYMBOL = "SIDU"    # 타겟 종목 (SIDU로 유지)
    EXCHANGE_CD = "NASD"      # 거래소 코드
    
    # 4. 타임프레임
    TIMEFRAME_1 = "1M"       
    TIMEFRAME_2 = "5M"       
    TIMEFRAME_1M = "1M"      
    TIMEFRAME_5M = "5M"      

    # 5. 데이터 처리 설정
    CANDLE_LIMIT = 100       # 데이터 개수 제한 (API 맞춰서 100)
    RATE_LIMIT_DELAY = 1.0   

    @classmethod
    def check_settings(cls):
        if not cls.APP_KEY or not cls.APP_SECRET:
            print(f"❌ [오류] .env 파일 로드 실패 (KEY 못찾음)")
            return False
        if not cls.TELEGRAM_BOT_TOKEN:
            print(f"⚠️ [경고] 텔레그램 토큰이 없습니다.")
        return True