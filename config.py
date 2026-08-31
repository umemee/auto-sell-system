# config.py
import os
import sys
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env.production")
load_dotenv()

class Config:
    # ==========================================
    # 🚨 [CRITICAL SAFETY] 실행 모드 고정 (실매매 전면 차단)
    # ==========================================
    EXECUTION_MODE = "PAPER_TRADING_ONLY"  # 🔒 실계좌 주문 전송 100% 차단 및 페이퍼 트레이딩 모드
    IS_PAPER_TRADING = True
    VIRTUAL_INITIAL_BALANCE = 10000.0      # 가상 시작 예수금 ($10,000)
    VIRTUAL_LATENCY_MS = 100               # 네트워크 지연 모사 (100ms)
    VIRTUAL_SLIPPAGE_PCT = 0.0003          # 시장가 슬리피지 페널티 (0.03%)

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
    # 💡 2026-06~08 실전 매매 기록 기반 추출된 29개 손절 종목 초기 리스트
    INITIAL_LOSS_TICKERS = [
        'AQB', 'BEEM', 'BGMS', 'BNRG', 'CAPR', 'CGTL', 'CIIT', 'CRE', 
        'CYCU', 'DSY', 'DXST', 'FCUV', 'HIHO', 'ILLR', 'JLHL', 'KUST', 
        'LNAI', 'LRHC', 'MASK', 'MGRX', 'NCRA', 'NIVF', 'RUBI', 'STKH', 
        'SVRE', 'TDTH', 'UPC', 'VIVS', 'YXT'
    ]
    # ==========================================
    # ⚙️ [전략 파라미터 고도화] (2026 프로덕션 골든스팟 동결)
    # ==========================================
    USE_DYNAMIC_EMA = False     # 400 EMA 단일 고정
    ENTRY_DEADLINE_HOUR_ET = 10 
    ENTRY_START_TIME = "04:10"  
    UPPER_BUFFER = 0.01        # 🛡️ [Anti-FOMO] 매수 상한 버퍼 0.5% (과거 2.0% 결함 차단)
    BUY_SLIPPAGE_BUFFER = 0.01 # 매수 슬리피지 버퍼 0.5%
    ACTIVATION_THRESHOLD = 0.40 
    MAX_DAILY_CHANGE = 5.0     
    
    # 🛡️ [F1 5분 급락 방어 필터]
    CHG_5M_CRASH_FILTER_ENABLED = True
    CHG_5M_CRASH_THRESHOLD = -0.04  # -4.0% 이하 급락 시 진입 차단

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
    MAX_PRICE_DEVIATION_PCT = 0.5     # 호가 이탈 허용치 0.5% (Anti-FOMO)
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
    DIP_TOLERANCE = 0.010      # 지지선 허용오차 1.0% (백테스트 ENTRY_PRICE_BUFFER 동기화)
    SUPPORT_DROP_SLACK_PCT = 0.003  # ⚡ 지지선 이탈(DROP) 0.3% 미세 완충 버퍼 (백테스트 동기화)
    HOVER_TOLERANCE = 0.002  


    TIME_HARD_CUTOFF = "15:45"
    STOP_LOSS_PCT = 0.10       # 10% 기본 손절선 (백테스트 동기화)
    TARGET_PROFIT_PCT = 0.07   # 7% 고정 익절선 (백테스트 동기화)
    TP_PCT = TARGET_PROFIT_PCT 

    # ==========================================
    # 🛡️ [2026 Golden Spot] 직전봉 윗꼬리 및 고점 눌림목 복합 진입 필터 (B4 최적 룰)
    # ==========================================
    UPPER_WICK_FILTER_ENABLED = True
    UPPER_WICK_FILTER_THRESHOLD_PCT = 65.0
    UPPER_WICK_FILTER_USE_CLOSED_CANDLE_ONLY = True

    ENABLE_MIN_PEAK_DRAWDOWN_FILTER = True
    MIN_PEAK_DRAWDOWN_PCT = 10.0