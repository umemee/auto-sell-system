import os
import sys
from dotenv import load_dotenv

# .env.production 파일 우선 로드 (없으면 .env)
load_dotenv(dotenv_path=".env.production")
load_dotenv()

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

    # --- [Zone 1 전략 설정] ---
    ACTIVE_STRATEGY = "ATOM_SUP_EMA200"
    
    # KIS 실전 서버 URL
    URL_BASE = "https://openapi.koreainvestment.com:9443"
    
    # 40% 급등주 필터
    MIN_CHANGE_PCT = 40.0 
    
    # 리스크 관리
    MAX_POSITIONS = 1           # 1 Slot
    MAX_DAILY_LOSS = 15.0       # 하루 $15 손실 시 셧다운
    ALL_IN_RATIO = 0.98         # 가용 현금의 98% 베팅
    
    # 시스템 설정
    CHECK_INTERVAL_SEC = 60     # 1분 주기 체크