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

    # === [Zone 1 전략 설정] ===
    ACTIVE_STRATEGY = "NEW_PRE"
    
    # === [KIS API] ===
    BASE_URL = "https://openapi.koreainvestment.com:9443"
    
    # === [스캐닝 설정] ===
    MIN_CHANGE_PCT = 40.0           # 급등주 필터 (40% 이상)
    
    # === [리스크 관리] ===
    MAX_POSITIONS = 1               # 동시 보유 종목 수
    MAX_DAILY_LOSS_PCT = 6.0        # 하루 최대 손실률
    ALL_IN_RATIO = 0.98             # 예수금의 98% 사용
    
    # === [시간 설정] ===
    ACTIVE_START_HOUR = 4           # 미국 동부시간 04:00 (프리마켓)
    ACTIVE_END_HOUR = 16            # 미국 동부시간 16:00 (정규장 종료)
    
    # === [루프 간격] ===
    MAIN_LOOP_INTERVAL_SEC = 60     # 메인 루프 실행 간격 (1분)
    HEARTBEAT_INTERVAL_SEC = 1800   # 생존신고 간격 (30분)
    
    # === [주문 설정] ===
    BUY_TOLERANCE = 1.005           # 매수 허용 범위 (지정가 대비 +0.5%)
    SELL_BUFFER = 0.95              # 시장가 매도 버퍼 (현재가 대비 -5%)