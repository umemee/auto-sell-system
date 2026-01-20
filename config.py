# config.py (중복 제거 및 최적화 완료 버전)

import os
import sys
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env.production")
load_dotenv()

class Config:
    # === [계좌 및 인증] ===
    APP_KEY = os.getenv("KIS_APP_KEY")
    APP_SECRET = os.getenv("KIS_APP_SECRET")
    _ACC_NO = os.getenv("KIS_ACCOUNT_NO")
    
    if _ACC_NO and "-" in _ACC_NO:
        CANO, ACNT_PRDT_CD = _ACC_NO.split("-")
    else:
        CANO = _ACC_NO
        ACNT_PRDT_CD = os.getenv("ACNT_PRDT_CD", "01")

    # === [텔레그램] ===
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

    # === [KIS API] ===
    BASE_URL = "https://openapi.koreainvestment.com:9443"
    
    # === [스캐닝 설정] ===
    MIN_CHANGE_PCT = 42.0           # 급등주 필터 (42% 이상) - 유지!
    
    # === [리스크 관리] ===
    MAX_DAILY_LOSS_PCT = 6.0          # 일일 허용 손실 (-6%)
    MARKET_SELL_BUFFER_PCT = 0.95     # 시장가 매도 버퍼
    
    # === [안전장치 설정] ===
    PRICE_RECHECK_ENABLED = True      
    MAX_PRICE_DEVIATION_PCT = 2.0     
    BALANCE_RECHECK_ENABLED = True    
    TOKEN_AUTO_REFRESH = True         
    
    # === [모니터링 설정] ===
    ENABLE_DETAILED_LOGGING = True    
    LOG_PRICE_CHECKS = True           
    LOG_BALANCE_CHECKS = True         

    # ==========================================
    # ⚙️ STRATEGY PARAMETERS (Double Engine)
    # ==========================================
    ACTIVE_STRATEGY = "EMA_ZONE1"
    
    # [자금 관리]
    MAX_SLOTS = 2             # ✅ 2종목 동시 보유 (MAX_POSITIONS 삭제됨)

    # [진입 설정: EMA 10]
    EMA_LENGTH = 10           
    
    # [청산 설정: 백테스팅 Golden Set]
    STOP_LOSS_PCT = 0.45      # -45% 손절
    
    # [Trailing Stop]
    TS_ENABLE = True
    TS_ACTIVATION_PCT = 0.06  # 6% 수익 시 발동
    TS_CALLBACK_PCT = 0.01    # 1% 반납 시 익절
    
    # (고정 익절은 사용 안 함)
    TP_PCT = 1.0