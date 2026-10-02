# infra/utils.py - v3.1 Integrated
import logging
import sys
import datetime
import pytz
import functools
from pathlib import Path
from logging.handlers import RotatingFileHandler

# Windows 콘솔 UTF-8 인코딩 보장 (이모지 출력 에러 방지)
if sys.platform == 'win32':
    try:
        if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
        if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='backslashreplace')
    except Exception:
        pass

# 로거 설정 (Singleton)
_logger = None
BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

def get_logger(name="KIS_US_Scalper"):
    global _logger
    if _logger:
        return _logger

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    formatter = logging.Formatter(
        '[LIVE] [%(asctime)s] %(levelname)s [%(filename)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    if not logger.handlers:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

        file_handler = RotatingFileHandler(
            str(LOG_DIR / 'trade.log'), 
            maxBytes=10*1024*1024, 
            backupCount=5, 
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    _logger = logger
    return logger

# [V1 Feature] API 로깅 데코레이터
def log_api_call(api_name):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            logger = get_logger()
            try:
                result = func(*args, **kwargs)
                return result
            except AssertionError:
                # 🚨 Fail-safe assertion은 삼키지 않고 즉시 프로세스 중단으로 전파
                raise
            except Exception as e:
                logger.error(f"❌ API Fail [{api_name}]: {e}")
                return None
        return wrapper
    return decorator

def get_us_time():
    """
    [DEPRECATED] 현재 미국 동부 시간(EST/EDT) 반환 (서머타임 자동 적용)
    
    ⚠️ 이 함수는 더 이상 main.py에서 사용되지 않습니다.
    main.py는 내장된 시간 체크 로직을 사용합니다.
    하위 호환성을 위해 유지됩니다.
    """
    us_eastern = pytz.timezone('America/New_York')
    return datetime.datetime.now(us_eastern)

def is_market_open():
    """
    [DEPRECATED] 스마트 마켓 타임 체크
    
    ⚠️ 이 함수는 더 이상 사용되지 않습니다.
    대신 main.py의 is_active_market_time()을 사용하세요.
    
    레거시 기능: 
    - 서머타임 자동 반영
    - 주말(토/일) 자동 체크
    - 프리마켓(04:00~) ~ 정규장 종료(16:00) 커버
    
    하위 호환성을 위해 유지됩니다.
    """
    now = get_us_time()
    
    # 주말 체크 (월=0, ... 토=5, 일=6)
    if now.weekday() >= 5:
        return False

    # 시간 범위 설정 (04:00 ~ 16:00)
    market_start = now.replace(hour=4, minute=0, second=0, microsecond=0)
    market_end = now.replace(hour=16, minute=0, second=0, microsecond=0)
    
    return market_start <= now <= market_end

def get_next_market_open():
    """
    [DEPRECATED] 다음 개장 시간 계산 (안내용)
    
    ⚠️ 이 함수는 현재 사용되지 않습니다. 
    하위 호환성을 위해 유지됩니다. 
    """
    now = get_us_time()
    target = now.replace(hour=4, minute=0, second=0, microsecond=0)
    
    if now > target or now.weekday() >= 5:
        target += datetime.timedelta(days=1)
        
    # 주말 건너뛰기
    while target.weekday() >= 5:
        target += datetime.timedelta(days=1)
        

    return target

def round_price(price: float) -> float:
    """
    SEC Rule 612 Sub-Penny Rule 규격 가격 보정:
    - $1.00 이상: 2자리 ($0.01)
    - $1.00 미만: 4자리 ($0.0001)
    """
    if price >= 1.0:
        return round(float(price), 2)
    else:
        return round(float(price), 4)

def get_trade_date_key(now=None) -> str:
    """
    [거래일 키 표준화 함수]
    - 미국 동부시간(America/New_York) 벽시계 시각 기준 +4시간 시프트한 날짜(YYYY-MM-DD) 반환.
    - ET 20:00(애프터마켓 종료) = KST 09:00(서머타임)/10:00(표준시)에 거래일이 익일로 전환됨.
    - now는 반드시 timezone-aware datetime이어야 하며, naive일 경우 ValueError 발생.
    - DST 경계 왜곡 방지를 위해 ET 벽시계 시각 추출 후 naive 상태에서 +4h 연산 수행.
    """
    if now is None:
        now = datetime.datetime.now(pytz.timezone('Asia/Seoul'))
    elif now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("now must be a timezone-aware datetime")

    tz_ny = pytz.timezone('America/New_York')
    ny_dt = now.astimezone(tz_ny)
    ny_naive = ny_dt.replace(tzinfo=None) + datetime.timedelta(hours=4)
    return ny_naive.strftime("%Y-%m-%d")

def format_est_seed(seed: float, pnl: float) -> str:
    """
    [추정 총시드 표기 공통 포맷팅 함수]
    - seed > 0.0: f"${seed + pnl:,.2f}"
    - seed <= 0.0 (미설정/장중 재시작): "산정 불가(장중 재시작)"
    """
    if seed is not None and float(seed) > 0.0:
        return f"${float(seed) + float(pnl):,.2f}"
    return "산정 불가(장중 재시작)"
