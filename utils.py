# utils.py
import logging
import sys
import datetime
import pytz # pip install pytz 필요

# 로거 설정 (Singleton 패턴 유사 효과)
_logger = None

def get_logger(name="KIS_US_Scalper"):
    global _logger
    if _logger:
        return _logger

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    # 포맷 설정
    formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s [%(filename)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 1. 콘솔 핸들러 (화면 출력)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    # 2. 파일 핸들러 (trade.log 저장)
    file_handler = logging.FileHandler('trade.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    _logger = logger
    return logger

def get_us_time():
    """현재 미국 동부 시간(EST/EDT) 반환"""
    us_eastern = pytz.timezone('America/New_York')
    return datetime.datetime.now(us_eastern)

def is_market_open():
    """
    미국 주식 시장 정규장 운영 여부 확인 (09:30 ~ 16:00)
    주말 및 공휴일 체크 로직은 별도 추가 필요 (여기선 시간만 체크)
    """
    now = get_us_time()
    
    # 주말 체크 (5:토, 6:일)
    if now.weekday() >= 5:
        return False

    # 시간 체크 (HHMM 형태 정수 변환)
    current_time = now.hour * 100 + now.minute
    
    # 정규장: 09:30 ~ 16:00
    if 930 <= current_time < 1600:
        return True
        
    return False

def get_timestamp():
    return datetime.datetime.now().strftime("%Y%m%d%H%M%S")