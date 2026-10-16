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
    [매수 체결 메시지 포맷팅 - 2026-10 개편]
    🟢 [매수] {ticker} ({strategy})
    • 체결: {shares:,}주 × ${price:.2f} (${total_amt:,.2f})
    • 목표: ${target_price:.2f} (+{target_pct:.1f}%) | 익절 주문 등록 완료
    • 잔여 현금: ${cash:,.2f}
    """
    total_amt = qty * price
    tgt_p = target_price or kwargs.get('target', 0.0) or 0.0
    if tp_pct is not None:
        target_pct = (tp_pct * 100.0) if abs(tp_pct) < 1.0 else tp_pct
    elif tgt_p > 0 and price > 0:
        target_pct = ((tgt_p - price) / price) * 100.0
    else:
        target_pct = 0.0

    rem_cash = cash if cash is not None else kwargs.get('rem_cash', 0.0)
    strat_clean = str(strategy).strip()
    paper_tag = " [PAPER]" if is_paper else ""

    lines = [
        f"🟢 [매수] {ticker} ({strat_clean}){paper_tag}",
        f"• 체결: {qty:,}주 × ${price:.2f} (${total_amt:,.2f})",
        f"• 목표: ${tgt_p:.2f} (+{target_pct:.1f}%) | 익절 주문 등록 완료",
        f"• 잔여 현금: ${rem_cash:,.2f}"
    ]
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
    [청산 메시지 포맷팅 - 2026-10 개편]
    💰 [익절] {ticker} ({reason_kr})
    • 가격: ${buy_price:.2f} ➔ ${sell_price:.2f} ({return_pct:+.2f}%)
    • 실현 손익: {trade_pnl:+.2f}
    • 계좌: 총자산 ${total_assets:,.2f} (오늘 누적 {daily_pnl:+.2f})
    """
    exit_p = exit_price if exit_price > 0 else kwargs.get('price', 0.0)
    raw_reason = str(reason or kwargs.get('reason', '')).strip()

    # 사유 매핑: TARGET_PROFIT_* / TAKE_PROFIT -> 목표가 달성, EMA -> EMA 트레일링, STOP_LOSS -> 손절
    if "TARGET_PROFIT" in raw_reason or "TAKE_PROFIT" in raw_reason:
        reason_kr = "목표가 달성"
    elif raw_reason == "EMA" or "TRAILING" in raw_reason:
        reason_kr = "EMA 트레일링"
    elif "STOP_LOSS" in raw_reason:
        reason_kr = "손절"
    elif "TIME_CUT" in raw_reason:
        reason_kr = "타임컷"
    elif "FORCE_EOD" in raw_reason or "EOD_FORCE" in raw_reason:
        reason_kr = "장마감 청산"
    elif raw_reason:
        reason_kr = raw_reason
    else:
        reason_kr = "목표가 달성" if is_win else "손절"

    header_icon = "💰 [익절]" if is_win else "📉 [손절]"
    paper_tag = " [PAPER]" if is_paper else ""

    trade_pnl_str = f"+${abs(pnl):,.2f}" if pnl >= 0 else f"-${abs(pnl):,.2f}"
    try:
        daily_pnl_val = float(daily_account_pnl if daily_account_pnl is not None else kwargs.get('daily_pnl', 0.0))
    except (TypeError, ValueError):
        daily_pnl_val = 0.0
    daily_pnl_str = f"+${abs(daily_pnl_val):,.2f}" if daily_pnl_val >= 0 else f"-${abs(daily_pnl_val):,.2f}"

    lines = [
        f"{header_icon} {ticker} ({reason_kr}){paper_tag}",
        f"• 가격: ${entry_price:.2f} ➔ ${exit_p:.2f} ({ret_pct:+.2f}%)",
        f"• 실현 손익: {trade_pnl_str}",
        f"• 계좌: 총자산 ${total_equity:,.2f} (오늘 누적 {daily_pnl_str})"
    ]
    return "\n".join(lines)

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
    [하트비트 메시지 간소화 - 2026-10 개편]
    💓 [{time_kst} KST] {strategy_name} 전략 상태
    • 총자산: ${total_assets:,.2f} (오늘 실현 {daily_pnl:+.2f})
    • 감시 종목({watch_count}개): {ticker_list_preview}
    • 필터 차단: Ban {ban_count}개 | 손절차단 {sl_ban_count}개
    """
    watch_cnt = len(watchlist)
    watch_display = " ".join(watchlist[:8]) if watchlist else "없음"
    if watch_cnt > 8:
        watch_display += " …"

    raw_reason = str(trigger_reason).strip()
    if "Alpha" in raw_reason:
        strat_name = "Alpha"
    elif "EMA" in raw_reason:
        strat_name = "EMA"
    elif "프리마켓" in raw_reason:
        strat_name = "프리마켓"
    elif "정기" in raw_reason or "생존" in raw_reason:
        strat_name = "정기 생존"
    else:
        strat_name = raw_reason.replace("전략 상태", "").replace("전략", "").strip() or "전략"

    try:
        daily_pnl_val = float(daily_account_pnl if daily_account_pnl is not None else 0.0)
    except (TypeError, ValueError):
        daily_pnl_val = 0.0
    daily_pnl_str = f"+${abs(daily_pnl_val):,.2f}" if daily_pnl_val >= 0 else f"-${abs(daily_pnl_val):,.2f}"

    lines = [
        f"💓 [{kst_time_str} KST] {strat_name} 전략 상태",
        f"• 총자산: ${total_equity:,.2f} (오늘 실현 {daily_pnl_str})",
        f"• 감시 종목({watch_cnt}개): {watch_display}",
        f"• 필터 차단: Ban {ban_count}개 | 손절차단 {loss_count}개"
    ]
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
    date_str: str = None,
    session_return_pct: float = 0.0,
    closed_trades: list = None,
    file_count: int = 0,
    **kwargs
) -> str:
    """
    [장 마감 요약 메시지 포맷팅 - 2026-10 개편]
    🌙 장 마감 리포트 ({date_str})
    ━━━━━━━━━━━━━━━━━━━━
    • 최종 총자산: ${final_assets:,.2f} (세션 변동: {session_return_pct:+.2f}%)
    • 당일 실현손익: {daily_pnl:+.2f}
    • 거래 전적: {trade_count}건 ({win_count}승 {loss_count}패 / 승률 {win_rate:.0f}%)

    [종목별 실현손익]
    {ticker_summary_lines}
    ━━━━━━━━━━━━━━━━━━━━
    📦 캔들 데이터 백업 완료 ({file_count}개 파일)
    """
    win_rate = (win_count / trade_count * 100.0) if trade_count > 0 else 0.0
    try:
        daily_pnl_val = float(daily_account_pnl if daily_account_pnl is not None else kwargs.get('daily_pnl', 0.0))
    except (TypeError, ValueError):
        daily_pnl_val = 0.0
    daily_pnl_str = f"+${abs(daily_pnl_val):,.2f}" if daily_pnl_val >= 0 else f"-${abs(daily_pnl_val):,.2f}"

    # 종목별 실현손익 합산
    ticker_map = {}
    trades_list = closed_trades if closed_trades is not None else kwargs.get('closed_trades_today', [])
    if trades_list:
        for t in trades_list:
            sym = t.get('ticker', 'UNKNOWN')
            p = t.get('pnl', 0.0)
            r = t.get('return_pct', 0.0)
            if sym not in ticker_map:
                ticker_map[sym] = {'pnl': 0.0, 'rets': []}
            ticker_map[sym]['pnl'] += p
            ticker_map[sym]['rets'].append(r)

    if ticker_map:
        summary_lines = []
        for sym, d in ticker_map.items():
            sym_pnl = d['pnl']
            sym_pnl_str = f"+${abs(sym_pnl):,.2f}" if sym_pnl >= 0 else f"-${abs(sym_pnl):,.2f}"
            avg_ret = sum(d['rets']) / len(d['rets']) if d['rets'] else 0.0
            summary_lines.append(f"• {sym}: {sym_pnl_str} ({avg_ret:+.2f}%)")
        ticker_summary_lines = "\n".join(summary_lines)
    else:
        ticker_summary_lines = "• 매매 내역 없음"

    d_str = date_str or kwargs.get('current_date_str', '')
    if not d_str:
        kst_tz = pytz.timezone('Asia/Seoul')
        if et_time is not None:
            if et_time.tzinfo is None:
                et_time = pytz.timezone('US/Eastern').localize(et_time)
            d_str = et_time.astimezone(kst_tz).strftime("%Y-%m-%d")
        else:
            d_str = datetime.datetime.now(kst_tz).strftime("%Y-%m-%d")

    msg = (
        f"🌙 장 마감 리포트 ({d_str})\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• 최종 총자산: ${total_equity:,.2f} (세션 변동: {session_return_pct:+.2f}%)\n"
        f"• 당일 실현손익: {daily_pnl_str}\n"
        f"• 거래 전적: {trade_count}건 ({win_count}승 {loss_count}패 / 승률 {win_rate:.0f}%)\n\n"
        f"[종목별 실현손익]\n"
        f"{ticker_summary_lines}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 캔들 데이터 백업 완료 ({file_count}개 파일)"
    )
    return msg

