# config.py
import os
import sys
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env.production")
load_dotenv()

class Config:
    # ==========================================
    # 🕒 [시간 설정] (중요!)
    # ==========================================
    # 서버가 한국 시간이든 미국 시간이든 상관없이, 
    # 아래 설정은 무조건 '미국 현지 시간(EST/EDT)' 기준입니다.
    
    # [활동 시간 설정]
    # 4  = 프리마켓 시작 (한국 시간 18:00 겨울 / 17:00 여름)
    # 9  = 정규장 시작 1시간 전
    # 20 = 애프터마켓 종료 (한국 시간 10:00 겨울 / 09:00 여름)
    ACTIVE_START_HOUR = 4  
    ACTIVE_END_HOUR = 20   
    
    # ==========================================
    # ⚙️ [전략 파라미터 고도화] (v6.0 Update)
    # ==========================================
    # [1] 진입 제한 (Entry Limit)
    # 오전 10시(ET) 이후에는 신규 진입 금지 (승률 하락 구간)
    ENTRY_DEADLINE_HOUR_ET = 10 

    # [2] 타임 컷 (Time Cut)
    # 진입 후 240분(4시간) 경과 시 강제 청산 (오후 반등 노림수)
    MAX_HOLDING_MINUTES = 240

    # ==========================================
    # 🏦 [계좌 및 인증]
    # ==========================================
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

    # ==========================================
    # 🔍 [스캐닝 설정]
    # ==========================================
    MIN_CHANGE_PCT = 42.0           # 급등주 필터 (42% 이상)
    
    # [실전 필터링 기준]
    FILTER_MIN_PRICE = 0.5          # 최소 주가 $0.5 (동전주 제외)
    FILTER_MAX_PRICE = 50.0         # 최대 주가 $50.0 (너무 비싼 주식 제외)
    # 💡 새벽 4시(프리마켓 초기)에는 거래량이 적으므로, 이 기준에 못 미쳐 종목이 안 잡힐 수 있습니다.
    FILTER_MIN_TX_VALUE = 50000   # 최소 거래대금 $50,000 (약 7천만원)

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
    HEARTBEAT_INTERVAL_SEC = 1800     # 30분마다 생존 신고

    # ==========================================
    # ⚙️ [전략 파라미터] (Double Engine)
    # ==========================================
    ACTIVE_STRATEGY = "EMA_ZONE1"
    
    # [자금 관리]
    MAX_SLOTS = 2             # 2종목 동시 보유

    # [진입 설정]
    EMA_LENGTH = 10           
    DIP_TOLERANCE = 0.005    # 눌림목 인정 오차 (0.5%)
    HOVER_TOLERANCE = 0.002  # 반등 인정 오차 (0.2%)

    # [청산 설정]
    STOP_LOSS_PCT = 0.40      # -40% 손절
    TARGET_PROFIT_PCT = 0.10  # +10% 목표 수익률 (TP)
    TP_PCT = TARGET_PROFIT_PCT # (호환성 유지)

