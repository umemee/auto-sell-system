# config.py
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env.production")

class Config:
    # --- [계좌 및 인증] ---
    APP_KEY = os.getenv("KIS_APP_KEY")
    APP_SECRET = os.getenv("KIS_APP_SECRET")
    _ACC_NO = os.getenv("KIS_ACCOUNT_NO")
    if _ACC_NO and "-" in _ACC_NO:
        CANO, ACNT_PRDT_CD = _ACC_NO.split("-")
    else:
        CANO = _ACC_NO
        ACNT_PRDT_CD = os.getenv("ACNT_PRDT_CD", "01")

    # --- [텔레그램] ---
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

    # --- [거래소 설정] ---
    URL_BASE = "https://openapi.koreainvestment.com:9443"
    BASE_URL = URL_BASE 
    EXCHANGE_CD = "NASD"

    # --- [데이터 설정] ---
    TIMEFRAME_1M = "1M"      
    CANDLE_LIMIT = 300       # [수정] SMA 200 계산을 위해 넉넉하게 300개 요청
    RATE_LIMIT_DELAY = 1.2

    # --- [자금 관리] ---
    # 1회 매수 시 All-in (98%) 로직은 get_order_qty에서 처리됨
    
    # --- [전략 파라미터 (ROD_B)] ---
    STRATEGY_NAME = "ROD_B"
    STOP_LOSS_PCT = 0.08      # [수정] 손절 -8%
    TAKE_PROFIT_PCT = 0.10    # [수정] 익절 +10%
    
    # --- [스캐닝 조건] ---
    SCAN_MIN_CHANGE = 0.40    # 40% 이상 급등
    SCAN_DELAY_MIN = 10       # 10분 지연

    @classmethod
    def check_settings(cls):
        if not cls.APP_KEY or not cls.APP_SECRET:
            print(f"❌ [오류] .env 로드 실패: API KEY가 없습니다.")
            return False
        return True

    @classmethod
    def get_order_qty(cls, current_price: float, balance: float) -> int:
        """All-in Mode: 잔고의 98% 투입"""
        if current_price <= 0: return 0
        safe_balance = balance * 0.98
        final_qty = int(safe_balance // current_price)
        return max(1, final_qty) if final_qty > 0 else 0