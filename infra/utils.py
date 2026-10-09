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
    [추정 총시드 표기 공통 포맷팅 함수 - 하위 호환성 유지]
    - seed > 0.0: f"${seed + pnl:,.2f}"
    - seed <= 0.0 (미설정/장중 재시작): "산정 불가(장중 재시작)"
    """
    if seed is not None and float(seed) > 0.0:
        return f"${float(seed) + float(pnl):,.2f}"
    return "산정 불가(장중 재시작)"

# =========================================================================
# 💬 [작업 4] 표준 텔레그램 메시지 포맷팅 함수군
# =========================================================================

def format_buy_fill_message(
    strategy: str,
    ticker: str,
    qty: int,
    price: float,
    target_price: float = None,
    tp_pct: float = None,
    cash: float = None,
    is_paper: bool = False,
    **kwargs
) -> str:
    """
    [매수 체결 메시지 포맷팅]
    🟢 매수 체결 | AIXI (EMA)
    673주 × $2.10 = $1,413.30
    목표 $2.25 (+7.1%)
    """
    total_cost = qty * price
    paper_tag = " [PAPER]" if is_paper else ""
    lines = [
        f"🟢 매수 체결 | {ticker} ({strategy}){paper_tag}",
        f"{qty}주 × ${price:.2f} = ${total_cost:,.2f}"
    ]
    tgt_p = target_price or kwargs.get('target', None)
    if tgt_p is not None and tgt_p > 0:
        if tp_pct is not None:
            pct_val = (tp_pct * 100.0) if abs(tp_pct) < 1.0 else tp_pct
            lines.append(f"목표 ${tgt_p:.2f} (+{pct_val:.1f}%)")
        else:
            lines.append(f"목표 ${tgt_p:.2f}")
    return "\n".join(lines)

def format_pre_order_ack_message(
    ticker: str,
    target_price: float = None,
    is_success: bool = True,
    err_msg: str = None,
    **kwargs
) -> str:
    """
    [익절 주문 접수 확인/실패 포맷팅]
    확인: 🔒 익절 주문 접수 확인 | AIXI · $2.25
    실패: 🚨 익절 주문 실패 | AIXI — HTS에서 확인 필요
    """
    price_val = target_price or kwargs.get('price', 0.0)
    if is_success:
        return f"🔒 익절 주문 접수 확인 | {ticker} · ${price_val:.2f}"
    else:
        reason_str = f" — {err_msg}" if err_msg else " — HTS에서 확인 필요"
        return f"🚨 익절 주문 실패 | {ticker}{reason_str}"

def format_exit_message(
    ticker: str,
    strategy: str = "EMA",
    entry_price: float = 0.0,
    exit_price: float = 0.0,
    ret_pct: float = 0.0,
    pnl: float = 0.0,
    cash: float = 0.0,
    total_equity: float = 0.0,
    daily_account_pnl: float = 0.0,
    is_win: bool = True,
    is_fill_price: bool = True,
    is_delayed: bool = False,
    qty: int = 0,
    reason: str = None,
    is_paper: bool = False,
    **kwargs
) -> str:
    """
    [청산 메시지 포맷팅 (익절 🟢 / 손절 🔴)]
    🟢 익절 체결 | AIXI (EMA)
    $2.10 → $2.25 (+7.14%) · 손익 +$99.44
    현금 $1,655.00 · 총자산 $2,929.48
    오늘 +$99.44 (계좌 기준)
    """
    exit_p = exit_price if exit_price > 0 else kwargs.get('price', 0.0)
    reason_val = reason or kwargs.get('reason', '')
    is_paper_mode = is_paper or kwargs.get('is_paper', False)
    paper_tag = " [PAPER]" if is_paper_mode else ""
    icon_type = "익절" if is_win else "손절"
    emoji = "🟢" if is_win else "🔴"
    pnl_sign = f"+${abs(pnl):,.2f}" if is_win else f"-${abs(pnl):,.2f}"
    basis_tag = "" if is_fill_price else " (주문가 기준)"
    delayed_tag = " (반영 지연 가능)" if is_delayed else ""
    
    strat_clean = str(strategy).strip()
    reason_extra = ""
    if reason_val and reason_val not in ["TAKE_PROFIT", "STOP_LOSS"]:
        reason_extra = f" · {reason_val}"
    
    line1 = f"{emoji} {icon_type} 체결 | {ticker} ({strat_clean}){paper_tag}{reason_extra}"
    line2 = f"${entry_price:.2f} → ${exit_p:.2f} ({ret_pct:+.2f}%) · 손익 {pnl_sign}{basis_tag}"
    line3 = f"현금 ${cash:,.2f}{delayed_tag} · 총자산 ${total_equity:,.2f}"
    acct_pnl_sign = f"+${daily_account_pnl:,.2f}" if daily_account_pnl >= 0 else f"-${abs(daily_account_pnl):,.2f}"
    line4 = f"오늘 {acct_pnl_sign} (계좌 기준)"
    
    return f"{line1}\n{line2}\n{line3}\n{line4}"

def format_heartbeat_message(
    kst_time_str: str,
    trigger_reason: str,
    cash: float,
    total_equity: float,
    holdings_list: list,
    daily_account_pnl: float,
    watchlist: list,
    ban_count: int,
    loss_count: int,
    is_delayed: bool = False,
    is_premarket_startup: bool = False,
    **kwargs
) -> str:
    """
    [하트비트 메시지 포맷팅]
    💓 21:00 KST · Alpha 개시
    현금 $2,830.04 · 총자산 $2,830.04 · 보유 없음
    오늘 $0.00
    감시 6: AIXI IPW DKI …
    차단: Ban 0개 | 손절차단 0개
    """
    delayed_tag = " (반영 지연 가능)" if is_delayed else ""
    holdings_str = f"보유 {len(holdings_list)}개 ({', '.join(holdings_list)})" if holdings_list else "보유 없음"
    
    watch_cnt = len(watchlist)
    watch_display = " ".join(watchlist[:8]) if watchlist else "없음"
    if watch_cnt > 8:
        watch_display += f" …"
    
    lines = [
        f"💓 {kst_time_str} KST · {trigger_reason}",
        f"현금 ${cash:,.2f}{delayed_tag} · 총자산 ${total_equity:,.2f} · {holdings_str}"
    ]
    if not is_premarket_startup:
        hb_pnl_str = f"+${daily_account_pnl:,.2f}" if daily_account_pnl > 0 else (f"-${abs(daily_account_pnl):,.2f}" if daily_account_pnl < 0 else "$0.00")
        lines.append(f"오늘 {hb_pnl_str}")
    lines.append(f"감시 {watch_cnt}: {watch_display}")
    if not is_premarket_startup:
        lines.append(f"차단: Ban {ban_count}개 | 손절차단 {loss_count}개")
        
    return "\n".join(lines)

def format_eod_summary_message(
    kst_time_str: str = None,
    total_equity: float = 0.0,
    cash: float = 0.0,
    holdings_dict: dict = None,
    daily_account_pnl: float = 0.0,
    trade_count: int = 0,
    win_count: int = 0,
    loss_count: int = 0,
    et_time: datetime.datetime = None,
    **kwargs
) -> str:
    """
    [장 마감 요약 메시지 포맷팅 (15:55 ET -> 서머타임 04:55 KST / 표준시 05:55 KST)]
    🌙 장 마감 요약 (04:55 KST)
    최종 총자산 $2,929.48 (현금 $2,929.48 · 보유 0)
    오늘 +$99.44 (계좌 기준) · 거래 2건 (익절 2, 손절 0)
    🚨 미청산 종목: AIXI 673주 — HTS에서 확인 필요 (잔여 있을 시)
    """
    if holdings_dict is None:
        holdings_dict = {}
        
    if not kst_time_str:
        kst_tz = pytz.timezone('Asia/Seoul')
        if et_time is not None:
            if et_time.tzinfo is None:
                et_time = pytz.timezone('US/Eastern').localize(et_time)
            kst_time_str = et_time.astimezone(kst_tz).strftime("%H:%M")
        else:
            kst_time_str = datetime.datetime.now(kst_tz).strftime("%H:%M")

    pos_cnt = len(holdings_dict)
    eod_pnl_sign = f"+${daily_account_pnl:,.2f}" if daily_account_pnl >= 0 else f"-${abs(daily_account_pnl):,.2f}"
    msg = (
        f"🌙 장 마감 요약 ({kst_time_str} KST)\n"
        f"최종 총자산 ${total_equity:,.2f} (현금 ${cash:,.2f} · 보유 {pos_cnt})\n"
        f"오늘 {eod_pnl_sign} (계좌 기준) · 거래 {trade_count}건 (익절 {win_count}, 손절 {loss_count})"
    )
    if pos_cnt > 0:
        rem_list = [f"{sym} {data.get('qty', 0)}주" for sym, data in holdings_dict.items()]
        rem_str = ", ".join(rem_list)
        msg += f"\n🚨 미청산 종목: {rem_str} — HTS에서 확인 필요"
    return msg

