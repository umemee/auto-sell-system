# infra/utils.py - v3.1 Integrated
import logging
import sys
import datetime
import pytz
import functools
from logging.handlers import RotatingFileHandler

# 로거 설정 (Singleton)
_logger = None

def get_logger(name="KIS_US_Scalper"):
    global _logger
    if _logger:
        return _logger

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s [%(filename)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    if not logger.handlers:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

        file_handler = RotatingFileHandler(
            'trade.log', 
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
            # logger.debug(f"📤 API Request: {api_name}") # 너무 시끄러우면 주석 처리
            try:
                result = func(*args, **kwargs)
                return result
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

