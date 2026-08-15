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
    ACTIVE_START_HOUR = 4  
    ACTIVE_END_HOUR = 16   
    
    # ==========================================
    # 🛡️ [3중 리스크 차단 필터] (PRD-202608-TRADING-01)
    # ==========================================
    USE_RISK_FILTER = True
    # 1. 시간대 필터 (미국 현지시간 ET 기준 09:15 ~ 09:30 / KST 22:15 ~ 22:30)
    RISK_TIME_BLOCK_START_ET = "09:15"
    RISK_TIME_BLOCK_END_ET = "09:30"
    
    # 2. 주가대 필터 ($5.00 <= price < $10.00)
    RISK_PRICE_BAND_MIN = 5.0
    RISK_PRICE_BAND_MAX = 10.0
    
    # 3. 손절 종목 재진입 차단 활성화
    BLOCK_PREVIOUS_LOSS_TICKERS = True

    # ==========================================
    # ⚙️ [전략 파라미터 고도화] (v6.0 Update)
    # ==========================================
    USE_DYNAMIC_EMA = True      
    ENTRY_DEADLINE_HOUR_ET = 10 
    ENTRY_START_TIME = "04:10"  
    UPPER_BUFFER = 0.02         
    ACTIVATION_THRESHOLD = 0.40 
    MAX_DAILY_CHANGE = 5.0     
    
    GAP_LIMIT_GLOBAL = 0.40    
    GAP_LIMIT_LATE = 0.10      
    LATE_HOUR_START = 9        
   
    MAX_HOLDING_MINUTES = 0

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
    MIN_CHANGE_PCT = 42.0           
    MAX_CHANGE_PCT = 500.0          
    
    FILTER_MIN_PRICE = 0.4          
    FILTER_MAX_PRICE = 50.0         
    FILTER_MIN_TX_VALUE = 50000   
    
    BLACKLIST_KEYWORDS = [
        'SPAC', 'ACQUISITION', 'ACQ', 'MERGER', 'BLANK CHECK', 
        'CAPITAL CORP', 'INVESTMENT CORP',
        'WARRANT', 'WAR', 'WS', 'UNIT', 'UN', 'U', 'RIGHTS', 'RT',                
        'NOTE', 'DEBENTURE', 'PFD', 'FUND', 'TRUST', 'ETF', 'ETN',
        '스팩', '기업인수목적', '애퀴지션', '머저', '캐피탈',        
        '워런트', '유닛', '권리', '펀드', '트러스트'       
    ]

    # === [리스크 관리] ===
    MAX_DAILY_LOSS_PCT = 6.0          
    MARKET_SELL_BUFFER_PCT = 0.95     
    
    PRICE_RECHECK_ENABLED = True      
    MAX_PRICE_DEVIATION_PCT = 2.0     
    BALANCE_RECHECK_ENABLED = True    
    TOKEN_AUTO_REFRESH = True         
    
    ENABLE_DETAILED_LOGGING = True    
    LOG_PRICE_CHECKS = True           
    LOG_BALANCE_CHECKS = True         
    HEARTBEAT_INTERVAL_SEC = 41000     

    # ==========================================
    # ⚙️ [전략 파라미터] (Double Engine)
    # ==========================================
    ACTIVE_STRATEGY = "EMA_ZONE1"
    MAX_SLOTS = 2             
    EMA_LENGTH = 400           
    DIP_TOLERANCE = 0.005    
    HOVER_TOLERANCE = 0.002  

    TIME_HARD_CUTOFF = "15:45"
    STOP_LOSS_PCT = 0.095      
    TARGET_PROFIT_PCT = 0.065  
    TP_PCT = TARGET_PROFIT_PCT 

    # ==========================================
    # 🛡️ [안전장치] Upper Wick Filter
    # ==========================================
    UPPER_WICK_FILTER_ENABLED = False
    UPPER_WICK_FILTER_THRESHOLD_PCT = 17.708333333333176
    UPPER_WICK_FILTER_USE_CLOSED_CANDLE_ONLY = False