# utils.py
import logging
import sys
import datetime
import pytz
from logging.handlers import RotatingFileHandler

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

    # 핸들러가 없을 때만 추가 (중복 로그 방지)
    if not logger.handlers:
        # 1. 콘솔 핸들러 (화면 출력)
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

        # 2. 파일 핸들러 (RotatingFileHandler 적용)
        # maxBytes: 10MB (10 * 1024 * 1024)
        # backupCount: 5개 파일까지 보관 (trade.log, trade.log.1, ...)
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

def get_us_time():
    """현재 미국 동부 시간(EST/EDT) 반환"""
    us_eastern = pytz.timezone('America/New_York')
    return datetime.datetime.now(us_eastern)

def is_market_open():
    """
    미국 주식 정규장 운영 시간 확인 (09:30 ~ 16:00 EST)
    단, Pre-market 대응을 위해 시간 범위 조정 가능
    """
    now = get_us_time()
    # 예: 09:30 ~ 16:00
    market_start = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_end = now.replace(hour=16, minute=0, second=0, microsecond=0)
    
    # 주말 체크 (월=0, ... 일=6)
    if now.weekday() >= 5:
        return False
        
    return market_start <= now <= market_end