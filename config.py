# config.py - v3.1 Integrated
import os
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv(dotenv_path=".env.production")

class Config:
    # 1. 계좌 및 인증 (필수)
    APP_KEY = os.getenv("KIS_APP_KEY")
    APP_SECRET = os.getenv("KIS_APP_SECRET")

    _ACC_NO = os.getenv("KIS_ACCOUNT_NO")
    if _ACC_NO and "-" in _ACC_NO:
        CANO, ACNT_PRDT_CD = _ACC_NO.split("-")
    else:
        CANO = _ACC_NO
        ACNT_PRDT_CD = os.getenv("ACNT_PRDT_CD", "01")

    # 텔레그램 설정
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

    # 2. 거래소 및 URL 설정
    URL_BASE = "https://openapi.koreainvestment.com:9443"
    BASE_URL = URL_BASE 
    EXCHANGE_CD = "NASD"

    # 3. 전략 및 타겟 설정
    TARGET_SYMBOLS = [] 
    
    # 차트 데이터 설정
    TIMEFRAME_1M = "1M"      
    TIMEFRAME_5M = "5M"      
    CANDLE_LIMIT = 100
    RATE_LIMIT_DELAY = 1.2

    # [V2 이식] 자금 관리 설정
    # 1회 매수 시 사용할 목표 금액 (USD)
    AVG_BUY_AMOUNT = 1000.0 

    @classmethod
    def check_settings(cls):
        if not cls.APP_KEY or not cls.APP_SECRET:
            print(f"❌ [오류] .env 로드 실패: API KEY가 없습니다.")
            return False
        return True

    @classmethod
    def get_order_qty(cls, current_price: float, balance: float) -> int:
        """
        [Phase 1: Guerrilla Mode]
        목표 금액($1000) 제한을 풀고, 현재 잔고의 98%를 '몰빵' 매수합니다.
        """
        if current_price <= 0:
            return 0
            
        # 잔고의 98% 사용 (2%는 수수료 및 슬리피지 버퍼)
        safe_balance = balance * 0.98
        
        # 수량 계산
        final_qty = int(safe_balance // current_price)
        
        return max(1, final_qty) if final_qty > 0 else 0