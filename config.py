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
    # 16 = 정규장 종료 (한국 시간 06:00 겨울 / 05:00 여름)
    ACTIVE_START_HOUR = 4  
    ACTIVE_END_HOUR = 16   
    
    # ==========================================
    # ⚙️ [전략 파라미터 고도화] (v6.0 Update)
    # ==========================================
    # [1] 진입 제한 (Entry Limit)
    # 오전 10시(ET) 이후에는 신규 진입 금지 (승률 하락 구간)
    ENTRY_DEADLINE_HOUR_ET = 10
    ENTRY_START_TIME = "04:10"  # 04:10 이전 진입 금지 (노이즈 회피)
    UPPER_BUFFER = 0.02         # 이평선 위 2% 이내까지만 눌림 인정 (천장 확인)
    ACTIVATION_THRESHOLD = 0.40 # 당일 40% 이상 상승 이력 필요
    MAX_DAILY_CHANGE = 1.5     # 당일 150% 이상 폭등 시 진입 금지 (과열 필터)
    
    # [2] 타임 컷 (Time Cut)
    # 진입 후 00분 무제한
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
    MIN_CHANGE_PCT = 42.0           # 급등주 필터 (42% 이상)
    MAX_CHANGE_PCT = 150.0          # [추가] 150% 이상 폭등주는 위험하므로 제외
    
    # [실전 필터링 기준]
    FILTER_MIN_PRICE = 0.5          # 최소 주가 $0.5 (동전주 제외)
    FILTER_MAX_PRICE = 50.0         # 최대 주가 $50.0 (너무 비싼 주식 제외)
    # 💡 새벽 4시(프리마켓 초기)에는 거래량이 적으므로, 이 기준에 못 미쳐 종목이 안 잡힐 수 있습니다.
    FILTER_MIN_TX_VALUE = 50000   # 최소 거래대금 $50,000 (약 7천만원)
    
    # [SPAC 및 악성 종목 필터링 키워드 DB]
    # ASPC 등 "ACQUISITION"이 들어간 종목을 원천 차단합니다.
    BLACKLIST_KEYWORDS = [
        # 1. SPAC (기업인수목적회사) 관련 강력 키워드
        'SPAC', 'ACQUISITION', 'ACQ', 'MERGER', 'BLANK CHECK', 
        'CAPITAL CORP', 'INVESTMENT CORP',
        
        # 2. 파생상품 및 채권
        'WARRANT', 'WAR', 'WS',        # 워런트
        'UNIT', 'UN', 'U',             # 유닛 (보통주+워런트)
        'RIGHTS', 'RT',                # 신주인수권
        'NOTE', 'DEBENTURE', 'PFD',    # 채권/우선주
        'FUND', 'TRUST', 'ETF', 'ETN',  # 펀드류

        # 3. [긴급 추가] 한글 키워드 (API 응답 대응)
        '스팩',          # 가장 중요 (ASPC 방어)
        '기업인수목적',   # SPAC의 정식 명칭
        '애퀴지션',       # Acquisition의 한글 발음
        '머저',          # Merger의 한글 발음
        '캐피탈',        # Capital
        '워런트',        # Warrant (파생상품)
        '유닛',          # Unit (스팩+워런트)
        '권리',          # Rights (신주인수권 등)
        '펀드',          # Fund
        '트러스트'       # Trust
    ]

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
    EMA_LENGTH = 200           
    DIP_TOLERANCE = 0.005    # 눌림목 인정 오차 (0.5%)
    HOVER_TOLERANCE = 0.002  # 반등 인정 오차 (0.2%)

    # [청산 설정]
    STOP_LOSS_PCT = 0.40      # -40% 손절
    TARGET_PROFIT_PCT = 0.12  # +12% 목표 수익률 (TP)
    TP_PCT = TARGET_PROFIT_PCT # (호환성 유지)


