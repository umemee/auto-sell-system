# main.py
import sys
import time
import datetime
import pytz 
import json 
import os   
import threading
import random 
from pathlib import Path

# Windows 콘솔 UTF-8 인코딩 보장 (이모지 및 특수문자 출력 에러 방지)
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass
from config import Config
from infra.utils import get_logger, round_price, get_trade_date_key, format_est_seed
from infra.kis_api import KisApi
from infra.kis_auth import KisAuth
from infra.telegram_bot import TelegramBot
from infra.real_portfolio import RealPortfolio
from infra.real_order_manager import RealOrderManager
from infra.live_candle_exporter import LiveCandleExporter
from infra.risk_filter import TradeRiskFilter  # 👈 [추가] 3중 리스크 필터
from data.market_listener import MarketListener
from strategy import get_strategy

BASE_DIR = Path(__file__).resolve().parent
logger = get_logger("Main")
STATE_FILE = str(BASE_DIR / "system_state.json")

def save_state(ban_list, active_candidates, loss_blacklist=None, daily_realized_pnl=None, unsettled_sell_amount=None, portfolio=None):
    """
    [설명] 밴 리스트, 감시 중인 종목, 손절 블랙리스트 및 체결기준 손익/미결제대금/당일시작시드를 파일로 저장합니다.
    """
    try:
        initial_seed_today = None
        if portfolio is not None:
            initial_seed_today = getattr(portfolio, 'initial_seed_today', 0.0)
            if daily_realized_pnl is None:
                daily_realized_pnl = getattr(portfolio, 'daily_realized_pnl', 0.0)
            if unsettled_sell_amount is None:
                unsettled_sell_amount = getattr(portfolio, 'unsettled_sell_amount', 0.0)

        # 손익/미결제금/시드가 명시되지 않은 경우 기존 파일의 당일 값 보존
        if daily_realized_pnl is None or unsettled_sell_amount is None or initial_seed_today is None:
            if os.path.exists(STATE_FILE):
                try:
                    with open(STATE_FILE, "r") as f_prev:
                        old_s = json.load(f_prev)
                        current_t_key = get_trade_date_key()
                        if old_s.get("trade_date") == current_t_key:
                            if daily_realized_pnl is None:
                                daily_realized_pnl = old_s.get("daily_realized_pnl", 0.0)
                            if unsettled_sell_amount is None:
                                unsettled_sell_amount = old_s.get("unsettled_sell_amount", 0.0)
                            if initial_seed_today is None:
                                initial_seed_today = old_s.get("initial_seed_today", 0.0)
                except Exception:
                    pass
        if daily_realized_pnl is None:
            daily_realized_pnl = 0.0
        if unsettled_sell_amount is None:
            unsettled_sell_amount = 0.0
        if initial_seed_today is None:
            initial_seed_today = 0.0

        candidates_data = {}
        if isinstance(active_candidates, dict):
            candidates_data = active_candidates
        else:
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            candidates_data = {sym: now_str for sym in active_candidates}

        positions_meta = {}
        if portfolio is not None and hasattr(portfolio, 'positions'):
            for t_sym, p_data in portfolio.positions.items():
                positions_meta[t_sym] = {
                    'strategy': p_data.get('strategy', p_data.get('strategy_name', 'EMA')),
                    'strategy_name': p_data.get('strategy_name', p_data.get('strategy', 'EMA')),
                    'alpha_id': p_data.get('alpha_id', ''),
                    'entry_price': p_data.get('entry_price', 0.0),
                    'qty': p_data.get('qty', 0),
                    'target_price': p_data.get('target_price', 0.0),
                    'entry_time': str(p_data.get('entry_time', '')),
                    'time_cut_minutes': p_data.get('time_cut_minutes', p_data.get('max_hold_min', 45)),
                    'tp_pct': p_data.get('tp_pct', 0.035),
                    'sl_pct': p_data.get('sl_pct', -0.10)
                }

        trade_date = get_trade_date_key()
        state = {
            "ban_list": list(ban_list),
            "loss_blacklist": list(loss_blacklist) if loss_blacklist is not None else [],
            "active_candidates": candidates_data,
            "positions_meta": positions_meta,
            "daily_realized_pnl": float(daily_realized_pnl),
            "unsettled_sell_amount": float(unsettled_sell_amount),
            "initial_seed_today": float(initial_seed_today),
            "trade_date": trade_date
        }
        
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)
            
    except Exception as e:
        logger.error(f"⚠️ 상태 저장 실패: {e}")

def load_state():
    """[설명] 저장된 상태 파일이 있다면 불러옵니다."""
    if not os.path.exists(STATE_FILE):
        return set(), {}, set(), 0.0, 0.0, {}, None, 0.0
    
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
            
        current_trade_date = get_trade_date_key()
        saved_trade_date = state.get("trade_date")
        if not saved_trade_date:
            logger.info("📅 [상태 파일] trade_date 키가 없는 구버전 파일이므로 상태를 초기화합니다.")
            return set(), {}, set(), 0.0, 0.0, {}, None, 0.0
            
        if saved_trade_date != current_trade_date:
            logger.info(f"📅 거래일 변경 감지(저장일: {saved_trade_date} vs 현재: {current_trade_date})으로 저장된 상태를 초기화합니다.")
            return set(), {}, set(), 0.0, 0.0, {}, saved_trade_date, 0.0
            
        loaded_ban = set(state.get("ban_list", []))
        loaded_loss = set(state.get("loss_blacklist", []))
        daily_pnl = float(state.get("daily_realized_pnl", 0.0))
        unsettled = float(state.get("unsettled_sell_amount", 0.0))
        loaded_seed = float(state.get("initial_seed_today", 0.0))
        raw_candidates = state.get("active_candidates", {})
        loaded_positions_meta = state.get("positions_meta", {})
        
        loaded_candidates = {}
        if isinstance(raw_candidates, dict):
            loaded_candidates = raw_candidates
        elif isinstance(raw_candidates, (list, set)):
            now_str = datetime.datetime.now(pytz.timezone('Asia/Seoul')).strftime("%Y-%m-%d %H:%M:%S")
            loaded_candidates = {sym: now_str for sym in raw_candidates}
        else:
            loaded_candidates = {}
            
        return loaded_ban, loaded_candidates, loaded_loss, daily_pnl, unsettled, loaded_positions_meta, saved_trade_date, loaded_seed
    
    except Exception as e:
        logger.error(f"⚠️ 상태 로드 실패: {e}")
        return set(), {}, set(), 0.0, 0.0, {}, None, 0.0

def merge_candle_dfs(old_df, new_df, max_len=1200):
    """
    [Candle Cache Merge Utility]
    - 새로 받아온 분봉(new_df)의 마지막 줄이 실시간 미완성봉인 경우
    - 인덱스/컬럼 중복 제거(drop_duplicates keep='last')로 최신 틱 데이터 반영
    - Datetime 정렬 완벽 유지 및 RangeIndex 재설정 보장
    """
    import pandas as pd
    if old_df is None or old_df.empty:
        combined = new_df.copy() if new_df is not None else pd.DataFrame()
    elif new_df is None or new_df.empty:
        combined = old_df.copy()
    else:
        combined = pd.concat([old_df, new_df], ignore_index=True)
    
    if not combined.empty and 'date' in combined.columns and 'time' in combined.columns:
        combined['date'] = combined['date'].astype(str)
        # 시간 문자열 정규화: 4자리 이하(HHMM)는 zfill(4), 5~6자리(HHMMSS)는 zfill(6)
        time_str_series = combined['time'].astype(str).str.strip()
        max_time_len = time_str_series.str.len().max()
        pad_len = 6 if max_time_len > 4 else 4
        combined['time'] = time_str_series.str.zfill(pad_len)
        
        combined = combined.drop_duplicates(subset=['date', 'time'], keep='last')
        combined = combined.sort_values(by=['date', 'time']).reset_index(drop=True)
        if len(combined) > max_len:
            combined = combined.iloc[-max_len:].reset_index(drop=True)
            
    return combined

ACTIVE_START_HOUR = getattr(Config, 'ACTIVE_START_HOUR', 4) 
ACTIVE_END_HOUR = getattr(Config, 'ACTIVE_END_HOUR', 20)    

def is_active_market_time():
    tz_et = pytz.timezone('US/Eastern')
    now_et = datetime.datetime.now(tz_et)
    
    tz_kst = pytz.timezone('Asia/Seoul')
    now_kst = datetime.datetime.now(tz_kst)

    if now_et.weekday() >= 5: 
        return False, f"주말 (Weekend) - KST: {now_kst.strftime('%H:%M')}"

    holidays = [
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", 
        "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07", 
        "2026-11-26", "2026-12-25"
    ]
    if now_et.strftime("%Y-%m-%d") in holidays:
        return False, "미국 증시 휴장일 (Holiday)"

    current_hour = now_et.hour
    if ACTIVE_START_HOUR <= current_hour < ACTIVE_END_HOUR:
        return True, f"Active Market (NY: {now_et.strftime('%H:%M')} | KR: {now_kst.strftime('%H:%M')})"
    
    return False, f"After Market / Night (NY: {now_et.strftime('%H:%M')} | KR: {now_kst.strftime('%H:%M')})"

def send_heartbeat_report(bot, portfolio, risk_filter, active_candidates, tz_kst, tz_et, trigger_reason="생존"):
    """
    [하트비트 생존 신고 및 매매 가능 시드 보고]
    - 트리거: 1) 프리마켓 시작 (21:00 KST / 08:00 ET), 2) 포지션 청산(익절/손절) 직후
    - 표기 내용: 현재 매매 가능 시드(Buying Power), 1회 진입 한도, 총 자산, 보유 종목 및 감시 현황
    """
    try:
        portfolio.sync_balance()
        buyable_cash = portfolio.get_effective_balance() if hasattr(portfolio, 'get_effective_balance') else portfolio.balance
        order_limit = portfolio.get_max_order_amount()
        total_eq = portfolio.total_equity
        pos_cnt = len(portfolio.positions)
        cur_k = datetime.datetime.now(tz_kst).strftime("%H:%M")
        cur_n = datetime.datetime.now(tz_et).strftime("%H:%M")

        watching_list = list(active_candidates)
        banned_list = list(portfolio.ban_list)
        loss_list = list(risk_filter.loss_blacklist)

        watch_str = ", ".join(watching_list[:5]) + ("..." if len(watching_list) > 5 else "")
        ban_str = ", ".join(banned_list[:5]) + ("..." if len(banned_list) > 5 else "")
        loss_str = ", ".join(loss_list[:5]) + ("..." if len(loss_list) > 5 else "")
        holdings_str = ", ".join(portfolio.positions.keys()) if pos_cnt else "없음"

        init_seed = getattr(portfolio, 'initial_seed_today', 0.0)
        cum_pnl = getattr(portfolio, 'daily_realized_pnl', 0.0)
        est_seed_str = format_est_seed(init_seed, cum_pnl)

        unconfirmed_tag = " (원장 반영 대기중)" if getattr(portfolio, 'is_balance_unconfirmed', False) else ""
        msg = (
            f"💓 [하트비트 - {trigger_reason}] KR {cur_k} / NY {cur_n}\n"
            f"💵 매매 가능 시드: ${buyable_cash:,.2f}{unconfirmed_tag} (1회 주문한도: ${order_limit:,.2f})\n"
            f"📊 당일 누적 손익: ${cum_pnl:+,.2f} | 🏦 추정 총시드: {est_seed_str}\n"
            f"💰 총 평가 자산: ${total_eq:,.2f}\n"
            f"🎰 보유({pos_cnt}개): {holdings_str}\n"
            f"👁️ 감시({len(watching_list)}개): {watch_str if watch_str else '없음'}\n"
            f"🚫 Ban({len(banned_list)}개): {ban_str if ban_str else '없음'}\n"
            f"🛑 손절차단({len(loss_list)}개): {loss_str if loss_str else '없음'}"
        )
        bot.send_message(msg)
        logger.info(f"💓 [Heartbeat Sent] {trigger_reason} | 매매가능시드: ${buyable_cash:,.2f} | 총자산: ${total_eq:,.2f}")
    except Exception as hb_err:
        logger.error(f"⚠️ 하트비트 전송 실패: {hb_err}")

def main():
    # =========================================================================
    # 🚨 [CRITICAL SAFETY] 실행 모드 경고 배너 출력
    # =========================================================================
    is_paper_mode = getattr(Config, 'IS_PAPER_TRADING', False)
    cano_raw = str(getattr(Config, 'CANO', ''))
    cano_masked = (cano_raw[:4] + "****") if len(cano_raw) >= 4 else "****"
    acnt_prdt = getattr(Config, 'ACNT_PRDT_CD', '01')

    if is_paper_mode:
        print("\n" + "=" * 85)
        print("🚨 [MODE: PAPER TRADING / REAL ORDERS DISABLED]")
        print("🚨 실제 브로커 주문 API 전면 차단됨! 가상 체결 엔진(Virtual Simulator)으로 동작합니다.")
        print(f"💰 가상 운용 예수금: ${getattr(Config, 'VIRTUAL_INITIAL_BALANCE', 10000.0):,.2f}")
        print("=" * 85 + "\n")
        logger.critical("================================================================================")
        logger.critical("🚨 [MODE: PAPER TRADING / REAL ORDERS DISABLED]")
        logger.critical("🚨 실제 브로커 주문 API 전면 차단됨! 가상 체결 엔진(Virtual Simulator)으로 가동됩니다.")
        logger.critical("================================================================================")
        logger.info("🚀 GapZone System v5.5 (Paper Trading Simulator Edition) Starting...")
    else:
        print("\n" + "=" * 85)
        print("⚠️ [MODE: REAL TRADING / CAUTION - REAL MONEY AT RISK]")
        print("⚠️ 실제 증권사 실계좌 주문이 활성화되었습니다!")
        print(f"🏦 계좌번호: {cano_masked}-{acnt_prdt}")
        print("=" * 85 + "\n")
        logger.critical("================================================================================")
        logger.critical("⚠️ [MODE: REAL TRADING / CAUTION - REAL MONEY AT RISK]")
        logger.critical(f"⚠️ 실제 증권사 실계좌 주문이 활성화되었습니다! (계좌: {cano_masked}-{acnt_prdt})")
        logger.critical("================================================================================")
        logger.info("🚀 GapZone System v5.5 (Live Trading Production Edition) Starting...")
    
    tz_kst = pytz.timezone('Asia/Seoul')
    tz_et = pytz.timezone('US/Eastern')
    now_kst_start = datetime.datetime.now(tz_kst)
    now_et_start = datetime.datetime.now(tz_et)
    
    logger.info(f"⏰ [Time Check] Korea: {now_kst_start.strftime('%Y-%m-%d %H:%M:%S')} | NY: {now_et_start.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"⚙️ [Config] 활동 시간: NY {ACTIVE_START_HOUR}:00 ~ {ACTIVE_END_HOUR}:00 | 모드: {'PAPER TRADING' if is_paper_mode else 'REAL'}")

    last_heartbeat_time = time.time()
    HEARTBEAT_INTERVAL = getattr(Config, 'HEARTBEAT_INTERVAL_SEC', 40000)
    was_sleeping = False
    alpha_entry_heartbeat_sent = False
    ema_entry_heartbeat_sent = False
    
    last_processed_minute = None
    eod_processed = False  
    current_date_str = get_trade_date_key(now_kst_start)

    try:
        # 1. 인프라 초기화
        token_manager = KisAuth()
        kis = KisApi(token_manager)
        bot = TelegramBot()
        listener = MarketListener(kis)
        candle_exporter = LiveCandleExporter(kis, bot, base_dir=BASE_DIR)
        
        # 🛡️ [추가] 3중 리스크 필터 초기화
        risk_filter = TradeRiskFilter()

        # 2. 포트폴리오 및 주문 관리자
        portfolio = RealPortfolio(kis)
        order_manager = RealOrderManager(kis)
        strategy = get_strategy() 
        
        # 🎯 [지시 9] Alpha 실시간 전략 어댑터 인스턴스화
        alpha_strategy = None
        try:
            from infra.virtual_combined_engine import AlphaLiveAdapter
            alpha_strategy = AlphaLiveAdapter()
            logger.info("🎯 [AlphaLiveAdapter] 알파 실시간 전략 어댑터 로드 완료")
        except Exception as ae:
            logger.error(f"⚠️ [AlphaLiveAdapter Init Error] 알파 어댑터 로드 실패: {ae}")

        # 🧪 [VIRTUAL PAPER TRACK] 결합 전략 가상 페이퍼 엔진 인스턴스화
        virtual_combined_engine = None
        if getattr(Config, 'ENABLE_COMBINED_PAPER_TRACK', True):
            try:
                from infra.virtual_combined_engine import VirtualCombinedEngine
                v_seed = getattr(Config, 'VIRTUAL_INITIAL_BALANCE', 2000.0)
                virtual_combined_engine = VirtualCombinedEngine(kis_api=kis, initial_capital=v_seed)
                logger.info(f"🧪 [VirtualTrack] 결합 전략 가상 페이퍼 엔진 탑재 완료 (EMA + Alpha + CapitalManager, Seed: ${v_seed:,.0f})")
            except Exception as e:
                logger.error(f"⚠️ [VirtualTrack Init Error] 가상 엔진 초기화 실패: {e}") 

        # 3. 서버 동기화 및 상태 복구
        logger.info("📡 증권사 서버와 동기화 중...")
        portfolio.sync_with_kis()
        loaded_state_res = load_state()
        saved_date = ""
        loaded_seed = 0.0
        if len(loaded_state_res) == 8:
            loaded_ban, loaded_candidates, loaded_loss, loaded_daily_pnl, loaded_unsettled, loaded_positions_meta, saved_date, loaded_seed = loaded_state_res
        elif len(loaded_state_res) == 7:
            loaded_ban, loaded_candidates, loaded_loss, loaded_daily_pnl, loaded_unsettled, loaded_positions_meta, saved_date = loaded_state_res
        elif len(loaded_state_res) == 6:
            loaded_ban, loaded_candidates, loaded_loss, loaded_daily_pnl, loaded_unsettled, loaded_positions_meta = loaded_state_res
        else:
            loaded_ban, loaded_candidates, loaded_loss, loaded_daily_pnl, loaded_unsettled = loaded_state_res[:5]
            loaded_positions_meta = {}

        portfolio.ban_list.update(loaded_ban)
        strategy.banned_tickers.update(loaded_ban)
        risk_filter.loss_blacklist.update(loaded_loss)
        
        # [지시 1-2 & 작업 2 안전장치] 거래일 일치 여부 검증 후 상태 반영 (불일치 시 daily_reset 자동 수행)
        if hasattr(portfolio, 'validate_and_apply_state'):
            portfolio.validate_and_apply_state(saved_date, loaded_daily_pnl, loaded_unsettled, loaded_seed)
        else:
            portfolio.daily_realized_pnl = loaded_daily_pnl
            portfolio.unsettled_sell_amount = loaded_unsettled
            portfolio.initial_seed_today = loaded_seed

        # 복구된 포지션 메타데이터(전략명, 목표가, 알파 청산 정보) 복원
        for t_sym, p_meta in loaded_positions_meta.items():
            if t_sym in portfolio.positions:
                strat_saved = p_meta.get('strategy', p_meta.get('strategy_name', 'EMA'))
                portfolio.positions[t_sym]['strategy'] = strat_saved
                portfolio.positions[t_sym]['strategy_name'] = p_meta.get('strategy_name', strat_saved)
                if 'alpha_id' in p_meta:
                    portfolio.positions[t_sym]['alpha_id'] = p_meta['alpha_id']
                if 'target_price' in p_meta and p_meta['target_price'] > 0:
                    portfolio.positions[t_sym]['target_price'] = p_meta['target_price']
                if 'time_cut_minutes' in p_meta:
                    portfolio.positions[t_sym]['time_cut_minutes'] = p_meta['time_cut_minutes']
                if 'tp_pct' in p_meta:
                    portfolio.positions[t_sym]['tp_pct'] = p_meta['tp_pct']
                if 'sl_pct' in p_meta:
                    portfolio.positions[t_sym]['sl_pct'] = p_meta['sl_pct']
                if 'entry_time' in p_meta and p_meta['entry_time']:
                    try:
                        entry_t_str = str(p_meta['entry_time'])
                        if len(entry_t_str) > 5:
                            from dateutil.parser import parse as parse_date
                            portfolio.positions[t_sym]['entry_time'] = parse_date(entry_t_str)
                    except Exception:
                        pass

        # 2차 복구: 당일 trade.log 기반 복구 (현재 거래일 키 명시)
        current_trade_date = get_trade_date_key(now_kst_start)
        recovered_count = portfolio.recover_from_log(today_str=current_trade_date)

        # [작업 2-2] 상태 복원 완료 후 시작 시드 래치 점검 (포지션 0개, 실현손익 0일 때만 래치)
        if hasattr(portfolio, '_maybe_latch_initial_seed'):
            portfolio._maybe_latch_initial_seed()

        # 체결기준 총자산 재계산 (미결제대금 이중합산 완전 제거)
        current_val = sum(
            p['qty'] * p.get('current_price', p.get('entry_price', 0.0))
            for p in portfolio.positions.values()
        )
        portfolio.total_equity = portfolio.balance + current_val
        
        if isinstance(loaded_candidates, (set, list)):
             active_candidates = {sym: datetime.datetime.now(pytz.timezone('Asia/Seoul')).strftime("%Y-%m-%d %H:%M:%S") for sym in loaded_candidates}
        else:
             active_candidates = loaded_candidates

        for sym in active_candidates:
            candle_exporter.register_candidate(sym)
        
        logger.info(
            f"💾 [Memory] 복구 완료 | 🚫Ban: {len(portfolio.ban_list)}개, "
            f"🛑Loss-Blacklist: {len(risk_filter.loss_blacklist)}개, "
            f"👁️Watch: {len(active_candidates)}개 | "
            f"시드: ${portfolio.initial_seed_today:,.2f}, 손익: ${portfolio.daily_realized_pnl:+,.2f}, 미결제: ${portfolio.unsettled_sell_amount:,.2f}"
        )
        
        mode_label = "가상 페이퍼 [PAPER]" if is_paper_mode else f"실계좌 실전 [REAL] ({cano_masked})"
        buyable_cash = portfolio.balance
        cum_pnl = getattr(portfolio, 'daily_realized_pnl', 0.0)
        init_seed = getattr(portfolio, 'initial_seed_today', 0.0)
        est_seed_str = format_est_seed(init_seed, cum_pnl)

        start_msg = (
            f"⚔️ [시스템 가동 v5.4 - 3중 리스크 필터 탑재]\n"
            f"🕹️ 모드: {mode_label}\n"
            f"⏰ 시간: KR {now_kst_start.strftime('%H:%M')} / NY {now_et_start.strftime('%H:%M')}\n"
            f"💵 매매 가능 시드: ${buyable_cash:,.2f}\n"
            f"📊 당일 누적 손익: ${cum_pnl:+,.2f} | 🏦 추정 총시드: {est_seed_str}\n"
            f"🎰 슬롯: {len(portfolio.positions)} / {portfolio.MAX_SLOTS}\n"
            f"🛡️ 손절 차단 종목 수: {len(risk_filter.loss_blacklist)}개"
        )
        bot.send_message(start_msg)

        # 시작 시점에 상태 즉시 1회 저장 (시작 시드 등 확정 저장)
        save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist, portfolio=portfolio)
        
        def get_status_data():
            return {
                'cash': portfolio.balance,
                'total_equity': portfolio.total_equity,
                'unsettled': getattr(portfolio, 'unsettled_sell_amount', 0.0),
                'daily_pnl': getattr(portfolio, 'daily_realized_pnl', 0.0),
                'positions': portfolio.positions,
                'targets': getattr(listener, 'current_watchlist', []),
                'ban_list': list(portfolio.ban_list),
                'loss_blacklist': list(risk_filter.loss_blacklist),
                'loss': 0.0,
                'loss_limit': getattr(Config, 'MAX_DAILY_LOSS_PCT', 0.0)
            }
        bot.set_status_provider(get_status_data)

        def run_live_candle_export(export_date=None, reason="manual"):
            try:
                result = candle_exporter.export_zip_and_send(export_date)
                manifest_rows = result.get("manifest_rows", [])
                saved_count = sum(1 for row in manifest_rows if row.get("status") == "saved")
                zip_path = result.get("zip_path", "")
                telegram_sent = result.get("telegram_sent", False)

                if zip_path:
                    delivery = "Telegram sent" if telegram_sent else "Local only"
                    logger.info(f"📦 [Live Export] {reason} | files={saved_count} | zip={zip_path} | {delivery}")
                    bot.send_message(
                        f"📦 [Live Candle Export]\nReason: {reason}\nFiles: {saved_count}\nZip: {zip_path}\nDelivery: {delivery}"
                    )
                return result
            except Exception as export_error:
                logger.error(f"❌ [Live Export] {reason} failed: {export_error}")
                return {"date": export_date or current_date_str, "files": [], "zip_path": "", "telegram_sent": False, "manifest_rows": []}

        def send_spread_analysis_log(export_date=None):
            try:
                date_target = export_date or current_date_str
                date_clean = date_target.replace("-", "")
                spread_file = BASE_DIR / "logs" / "spread_analysis" / f"signal_spreads_{date_clean}.csv"
                
                if spread_file.exists():
                    sent = bot.send_document(
                        str(spread_file), 
                        caption=f"📊 [Spread Analysis] {date_target} 호가 스냅샷 로그 (Ask/Bid/Volume)"
                    )
                    if sent:
                        logger.info(f"📤 [Spread Log] 텔레그램 전송 성공: {spread_file.name}")
            except Exception as e:
                logger.error(f"❌ [Spread Log] 텔레그램 전송 중 에러: {e}")
                
        def run_bot_thread():
            bot.start()
            
        t = threading.Thread(target=run_bot_thread)
        t.daemon = True 
        t.start()
        logger.info("🤖 텔레그램 봇 시작됨")

    except Exception as e:
        logger.critical(f"❌ 초기화 실패: {e}")
        return

    candle_cache = {}

    # ---------------------------------------------------------
    # [메인 루프]
    # ---------------------------------------------------------
    while True:
        try:
            now = datetime.datetime.now(pytz.timezone('America/New_York'))
            current_minute_str = now.strftime("%H:%M")

            # =========================================================
            # 🚀 [초고속 매도 전용 차선] 보유 종목 실시간 1초 감시
            # =========================================================
            if portfolio.positions:
                for ticker in list(portfolio.positions.keys()):
                    real_time_price = kis.get_current_price(ticker, exchange="NAS")
                    
                    if real_time_price and real_time_price > 0:
                        pos = portfolio.positions[ticker]
                        strat_type = pos.get('strategy', pos.get('strategy_name', 'EMA'))
                        is_alpha_pos = (strat_type == 'ALPHA')

                        if is_alpha_pos:
                            exit_signal = order_manager.check_alpha_exit(
                                position=pos, current_price=real_time_price, now_time=now
                            )
                        else:
                            exit_signal = strategy.check_exit(
                                ticker=ticker, position=pos, 
                                current_price=real_time_price, now_time=now
                            )
                        
                        if exit_signal:
                            reason = exit_signal['reason']
                            # 페이퍼 모드이거나 실전 비상 매도(손절/타임컷 등)일 때 매도 집행
                            # (익절 주문은 EMA/Alpha 공히 매수 직후 브로커에 지정가 사전 예약되므로, 실전에서는 손절/타임컷 시 취소 후 비상 매도 집행)
                            if is_paper_mode or (reason != 'TAKE_PROFIT' and reason != 'TARGET_PROFIT_0.035'):
                                entry_p = pos.get('entry_price', real_time_price)
                                trade_pnl = (real_time_price - entry_p) / entry_p if entry_p > 0 else -0.01

                                result = order_manager.execute_sell(portfolio, ticker, reason, price=real_time_price)
                                if result:
                                    bot.send_message(result['msg'])
            
                                    # 🛑 손절 발생 즉시 3중 필터 블랙리스트에 추가 (손절일 경우만)
                                    if trade_pnl < 0:
                                        risk_filter.register_trade_result(ticker, trade_pnl, reason=reason)
                                    
                                    if ticker in active_candidates:
                                        del active_candidates[ticker]
                                        
                                    save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist, portfolio=portfolio)
                                    # ⏳ [전산 딜레이 완충 가드] KIS 증권사 원장 반영 대기(1.2s) 후 잔고 동기화 및 하트비트 전송
                                    time.sleep(1.2)
                                    send_heartbeat_report(bot, portfolio, risk_filter, active_candidates, tz_kst, tz_et, trigger_reason=f"청산({reason}) 후 시드 갱신")
                    
                    time.sleep(0.5)

            # =========================================================
            # 🕒 [Time Sync] 캔들 완성형 (매 분 02초~04초 진입 - 증권사 1분봉 집계 완료 대기)
            # =========================================================
            current_kst = datetime.datetime.now(pytz.timezone('Asia/Seoul'))
            if not (current_kst.hour >= 17 or current_kst.hour < 5):
                if not was_sleeping:
                    logger.warning(f"💤 [AWS 정시 대기] 현재 한국 시간 {current_kst.strftime('%H:%M')}. 17시 정각까지 대기 루프 가동.")
                    was_sleeping = True
                time.sleep(10)
                continue

            if now.second < 2:
                time.sleep(0.3)
                continue

            if now.second > 5:
                time.sleep(0.5)
                continue
            
            if last_processed_minute == current_minute_str:
                time.sleep(0.5)
                continue
                
            last_processed_minute = current_minute_str
            
            # =========================================================
            # 💤 [Sleep Mode] 활동 시간 체크
            # =========================================================
            is_active, reason = is_active_market_time()
            
            if not is_active:
                if not was_sleeping:
                    logger.warning(f"💤 Sleep Mode: {reason}")
                    bot.send_message(f"💤 [대기] {reason}")
                    was_sleeping = True
                    save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist, portfolio=portfolio)
                
                time.sleep(30)
                continue
            
            if was_sleeping:
                bot.send_message("🌅 [프리마켓 데이터 수집 개시] KR 17:00 / NY 04:00 (전략 대기 중)")
                logger.info("🌅 [Premarket Data Collection Started] KR 17:00 / NY 04:00 (Waiting for Strategy Entry)")
                was_sleeping = False
                portfolio.sync_with_kis()

            # ---------------------------------------------------------
            # 🛑 [EOD] 장 마감 강제 청산
            # ---------------------------------------------------------
            cutoff_time_str = getattr(Config, 'TIME_HARD_CUTOFF', "15:54")
            cutoff_h, cutoff_m = map(int, cutoff_time_str.split(':'))
            
            is_after_cutoff = (now.hour > cutoff_h) or (now.hour == cutoff_h and now.minute >= cutoff_m)
            
            if is_after_cutoff and not eod_processed:
                logger.warning(f"⏰ [장 마감] 강제 청산 실행 (Current: {now.strftime('%H:%M')} >= Cutoff: {cutoff_time_str})")
                bot.send_message(f"🚨 [장 마감] 강제 청산 실행")
                
                if portfolio.positions:
                    for ticker in list(portfolio.positions.keys()):
                        order_manager.execute_sell(portfolio, ticker, "FORCE_EOD_EXIT", price=0)
                        time.sleep(0.2)

                if virtual_combined_engine:
                    virtual_combined_engine.force_eod_exit(now)
                
                save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist, portfolio=portfolio)
                run_live_candle_export(current_date_str, reason="eod")
                send_spread_analysis_log(current_date_str)
                logger.info("👋 [System] 장 마감으로 시스템을 종료합니다.")
                
                eod_processed = True
                time.sleep(300) 
                continue
            
            if not is_after_cutoff:
                eod_processed = False

            # =========================================================
            # 💓 [Heartbeat] 전략 매매 개시 생존 신고 (Alpha 21:00 / EMA 22:00)
            # =========================================================
            if now.hour >= 8 and not alpha_entry_heartbeat_sent:
                send_heartbeat_report(bot, portfolio, risk_filter, active_candidates, tz_kst, tz_et, trigger_reason="Alpha 전략 매매 개시")
                alpha_entry_heartbeat_sent = True
                last_heartbeat_time = time.time()
            elif now.hour >= 9 and not ema_entry_heartbeat_sent:
                send_heartbeat_report(bot, portfolio, risk_filter, active_candidates, tz_kst, tz_et, trigger_reason="EMA 전략 매매 개시")
                ema_entry_heartbeat_sent = True
                last_heartbeat_time = time.time()
            elif time.time() - last_heartbeat_time > HEARTBEAT_INTERVAL:
                send_heartbeat_report(bot, portfolio, risk_filter, active_candidates, tz_kst, tz_et, trigger_reason="정기 생존")
                last_heartbeat_time = time.time()

            # =========================================================
            # 📅 [Daily Reset] 거래일 변경 체크
            # =========================================================
            new_date_str = get_trade_date_key(now)
            if new_date_str != current_date_str:
                logger.info(f"📅 [New Trading Day] 거래일 변경 감지: {current_date_str} -> {new_date_str}")
                portfolio.daily_reset()    # 👈 [추가] 일일 실현손익 및 미결제대금 리셋
                strategy.daily_reset()     # 👈 [추가] 일별 세션 상태(banned_tickers 등) 초기화
                if alpha_strategy:
                    alpha_strategy.daily_reset() # 👈 [지시 9] 알파 전략 일별 세션 상태 초기화
                risk_filter.reset_daily()  # 👈 [추가] 일일 리스크 필터 리셋
                if virtual_combined_engine:
                    virtual_combined_engine.daily_reset()
                active_candidates.clear()
                candle_cache.clear()
                candle_exporter.reset_session()
                alpha_entry_heartbeat_sent = False  # 👈 익일 Alpha 매매 개시 하트비트 플래그 리셋
                ema_entry_heartbeat_sent = False    # 👈 익일 EMA 매매 개시 하트비트 플래그 리셋
                save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist, portfolio=portfolio)
                logger.info("✨ [Reset] 금일 감시 종목 및 밴 리스트 초기화 완료")
                current_date_str = new_date_str

            # =========================================================
            # 🧠 [Logic] 매매 로직 시작 (매 분 1회 실행)
            # =========================================================
            prev_holdings = set(portfolio.positions.keys())
            portfolio.sync_with_kis()
            current_holdings = set(portfolio.positions.keys())
            
            # sync_with_kis()에서 삭제된 종목(증권사 체결) 감지 및 실현손익 등록
            removed_positions = dict(portfolio.last_sync_removed_positions)
            portfolio.last_sync_removed_positions.clear()
            sold_tickers = set(removed_positions.keys()) | (prev_holdings - current_holdings)

            for ticker in sold_tickers:
                pos_info = removed_positions.get(ticker, {})
                qty = pos_info.get('qty', 0)
                entry_p = pos_info.get('entry_price', 0.0)
                
                strat_name = pos_info.get('strategy_name', pos_info.get('strategy', 'EMA'))
                is_alpha_sold = (strat_name == 'ALPHA')
                default_tp_pct = getattr(Config, 'ALPHA_TP_PCT', 0.035) if is_alpha_sold else getattr(Config, 'TARGET_PROFIT_PCT', 0.07)
                sell_p = pos_info.get('target_price', 0.0)
                if sell_p <= 0:
                    sell_p = round_price(entry_p * (1.0 + default_tp_pct)) if entry_p > 0 else 0.0
                
                exit_reason = 'TARGET_PROFIT_0.035' if is_alpha_sold else 'TAKE_PROFIT'
                
                if qty > 0 and entry_p > 0 and sell_p > 0:
                    record = portfolio.register_realized_sale(
                        ticker=ticker,
                        qty=qty,
                        sell_price=sell_p,
                        entry_price=entry_p,
                        reason=exit_reason
                    )
                    pnl = record.get('pnl', 0.0)
                    ret_pct = record.get('return_pct', 0.0)
                    env_mode = getattr(Config, 'EXECUTION_ENVIRONMENT', '')
                    if env_mode == 'LIVE_TRADING':
                        mode_str = "REAL"
                    elif env_mode == 'DUAL_SHADOW':
                        mode_str = "SHADOW"
                    else:
                        mode_str = "PAPER"

                    strat_label = f"{strat_name} 전략"

                    init_seed = getattr(portfolio, 'initial_seed_today', 0.0)
                    cum_pnl = getattr(portfolio, 'daily_realized_pnl', 0.0)
                    est_seed_str = format_est_seed(init_seed, cum_pnl)
                    effective_cash = portfolio.get_effective_balance() if hasattr(portfolio, 'get_effective_balance') else portfolio.balance
                    order_limit = portfolio.get_max_order_amount()
                    unconfirmed_tag = " (원장 반영 대기중)" if getattr(portfolio, 'is_balance_unconfirmed', False) else ""

                    msg = (
                        f"🎉 [매도/청산 체결 완료]\n"
                        f"🕹️ 모드: {mode_str}\n"
                        f"🎯 전략: {strat_label}\n"
                        f"📦 종목: {ticker}\n"
                        f"💵 실현손익: ${pnl:+,.2f} ({ret_pct:+.2f}%)\n"
                        f"💰 매매 가능 시드: ${effective_cash:,.2f}{unconfirmed_tag} (1회 한도: ${order_limit:,.2f})\n"
                        f"📊 당일 누적 손익: ${cum_pnl:+,.2f} | 🏦 추정 총시드: {est_seed_str}\n"
                        f"📌 사유: {exit_reason}"
                    )
                else:
                    logger.info(f"🎉 [익절 감지] {ticker} 목표가 도달 확인!")
                    env_mode = getattr(Config, 'EXECUTION_ENVIRONMENT', '')
                    mode_str = "REAL" if env_mode == 'LIVE_TRADING' else ("SHADOW" if env_mode == 'DUAL_SHADOW' else "PAPER")
                    strat_name = pos_info.get('strategy_name', 'EMA')
                    init_seed = getattr(portfolio, 'initial_seed_today', 0.0)
                    cum_pnl = getattr(portfolio, 'daily_realized_pnl', 0.0)
                    est_seed_str = format_est_seed(init_seed, cum_pnl)
                    effective_cash = portfolio.get_effective_balance() if hasattr(portfolio, 'get_effective_balance') else portfolio.balance
                    order_limit = portfolio.get_max_order_amount()
                    unconfirmed_tag = " (원장 반영 대기중)" if getattr(portfolio, 'is_balance_unconfirmed', False) else ""
                    msg = (
                        f"🎉 [매도/청산 체결 완료]\n"
                        f"🕹️ 모드: {mode_str}\n"
                        f"🎯 전략: {strat_name} 전략\n"
                        f"📦 종목: {ticker}\n"
                        f"💰 매매 가능 시드: ${effective_cash:,.2f}{unconfirmed_tag} (1회 한도: ${order_limit:,.2f})\n"
                        f"📊 당일 누적 손익: ${cum_pnl:+,.2f} | 🏦 추정 총시드: {est_seed_str}\n"
                        f"📌 사유: TAKE_PROFIT"
                    )
                
                bot.send_message(msg)
                portfolio.ban_list.add(ticker)
                
                if ticker in active_candidates:
                    del active_candidates[ticker]
                    
                save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist, portfolio=portfolio)

            if sold_tickers:
                # ⏳ [전산 딜레이 완충 가드] KIS 증권사 원장 반영 대기(1.2s) 후 잔고 동기화 및 하트비트 전송
                time.sleep(1.2)
                send_heartbeat_report(bot, portfolio, risk_filter, active_candidates, tz_kst, tz_et, trigger_reason="익절(TAKE_PROFIT) 후 시드 갱신")

            # ---------------------------------------------------------
            # D. [매수] 진입 타점 확인 (Fast-Path 최우선 집행 & 전략별 시간 분리)
            # 02~05초 골든 타임 보장을 위해 신규 급등주 스캔보다 매수 타점을 먼저 즉시 평가!
            # ---------------------------------------------------------
            ema_start_hour = getattr(Config, 'EMA_ENTRY_START_HOUR_ET', 9)
            alpha_start_hour = getattr(Config, 'ALPHA_ENTRY_START_HOUR_ET', 8)
            is_ema_active = (now.hour >= ema_start_hour)
            is_alpha_active = (now.hour >= alpha_start_hour)

            buy_candidates = [
                sym for sym in list(active_candidates)
                if not portfolio.is_holding(sym) and not portfolio.is_banned(sym)
            ]

            # 🚀 [순회 지연 최적화: RETO / SCYX 누락 방지 Fast-Path]
            # 무작위 셔플(random.shuffle) 제거 -> 당일 시초 갭/상승률 상위 종목 우선 순회 정렬
            def get_candidate_priority(sym):
                if sym in candle_cache and candle_cache[sym].get('df') is not None and not candle_cache[sym]['df'].empty:
                    c_df = candle_cache[sym]['df']
                    if len(c_df) >= 2 and c_df['open'].iloc[0] > 0:
                        return (c_df['close'].iloc[-1] - c_df['open'].iloc[0]) / c_df['open'].iloc[0]
                return 0.0

            buy_candidates.sort(key=get_candidate_priority, reverse=True)
            targets_to_check = buy_candidates[:15]
            listener.current_watchlist = targets_to_check 

            def _execute_buy_flow(sym, sig, sel_exch):
                if not portfolio.has_open_slot():
                    logger.warning(f"🚌 [Missed Bus] {sym} ({sig.get('strategy', 'EMA')}) 진입 신호 왔으나 자리 없음 -> 영구 제외")
                    portfolio.ban_list.add(sym)      
                    if sym in active_candidates:
                        del active_candidates[sym]
                    candle_cache.pop(sym, None)
                    save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist, portfolio=portfolio)
                    return False
                
                sig_price = float(sig['price'])
                ask, bid, ask_vol, bid_vol = kis.get_market_spread(sym, exchange=sel_exch or "NAS")
                if ask > 0 and bid > 0:
                    spread = (ask - bid) / ask * 100.0
                    if spread > 3.0:
                        logger.warning(f"⚠️ [Spread Guard] {sym}: 스프레드 과다 ({spread:.2f}% > 3.0%) 로 매수 보류")
                        return False

                    buy_buffer = float(getattr(Config, 'BUY_SLIPPAGE_BUFFER', 0.005))
                    max_allowed_price = sig_price * (1.0 + buy_buffer)
                    if ask > max_allowed_price:
                        logger.warning(
                            f"⚠️ [Pre-Order Price Guard] {sym}: "
                            f"Ask ${ask:.4f} > 허용상한 ${max_allowed_price:.4f} (시그널 ${sig_price:.4f} +{buy_buffer*100:.1f}%) -> 매수 차단"
                        )
                        return False

                entry_price = ask if ask > 0 else sig_price
                sig['price'] = entry_price
                sig['ticker'] = sym

                is_blocked, block_reason = risk_filter.is_order_blocked(
                    ticker=sym, price=entry_price, current_time_et=now
                )
                if is_blocked:
                    logger.warning(f"🛑 [Risk Filter Blocked] {sym}: {block_reason}")
                    return False

                is_alpha_sig = (sig.get('strategy') == 'ALPHA' or sig.get('strategy_name') == 'ALPHA')
                alpha_mode = getattr(Config, 'ALPHA_TRADING_MODE', 'PAPER').upper()

                if is_alpha_sig and alpha_mode == 'PAPER':
                    time_cut = sig.get('time_cut_minutes', sig.get('max_hold_min', getattr(Config, 'ALPHA_TIME_CUT_MINUTES', 45)))
                    alpha_lbl = sig.get('alpha_id', 'ALPHA')
                    logger.info(
                        f"🧪 [ALPHA PAPER] 알파 진입 신호 포착 (ALPHA_TRADING_MODE=PAPER, 실주문 미전송): "
                        f"{sym} ({alpha_lbl}) @ ${entry_price:.4f}"
                    )
                    bot.send_message(
                        f"🧪 [ALPHA 진입 신호 - PAPER 모드]\n"
                        f"📦 종목: {sym}\n"
                        f"🎯 세부전략: {alpha_lbl}\n"
                        f"💵 진입가: ${entry_price:.4f}\n"
                        f"⏱️ 타임컷: {time_cut}분\n"
                        f"ℹ️ 안내: ALPHA_TRADING_MODE=PAPER 설정에 따라 실주문을 전송하지 않고 가상 기록합니다."
                    )
                    candle_cache.pop(sym, None)
                    return False

                if portfolio.has_open_slot():
                    res = order_manager.execute_buy(portfolio, sig)
                    if res:
                        if res.get('msg'):
                            bot.send_message(res['msg'])
                        if res['status'] == 'success':
                            candle_cache.pop(sym, None)
                            save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist, portfolio=portfolio)
                            time.sleep(1.5)
                            portfolio.sync_with_kis()

                            try:
                                actual_pos = portfolio.get_position(sym)
                                if actual_pos and actual_pos.get('entry_price', 0) > 0:
                                    buy_p = actual_pos['entry_price']
                                else:
                                    buy_p = res.get('avg_price', sig['price'])

                                if buy_p > 0:
                                    qty = res.get('qty', 0)
                                    is_pure_paper = getattr(Config, 'IS_PURE_PAPER', is_paper_mode)
                                    if not is_alpha_sig:
                                        # EMA: +7% 지정가 사전 주문 전송
                                        tp_pct = getattr(Config, 'TARGET_PROFIT_PCT', 0.07)
                                        tgt_p = round_price(buy_p * (1.0 + tp_pct))
                                        if sym in portfolio.positions:
                                            portfolio.positions[sym]['target_price'] = tgt_p
                                            if 'strategy_name' not in portfolio.positions[sym]:
                                                portfolio.positions[sym]['strategy_name'] = sig.get('strategy_name', 'EMA')
                                        if qty > 0:
                                            if is_pure_paper:
                                                logger.info(f"🔒 [PAPER Pre-Order] [EMA] {sym} 가상 익절 목표가(${tgt_p}) 감시 등록 완료 (평단가: ${buy_p:.3f})")
                                                bot.send_message(f"🔒 [가상 익절 잠금/PAPER] [EMA] {sym} 익절 감시 등록 (평단가: ${buy_p:.3f} -> 목표가: ${tgt_p:.2f})")
                                            else:
                                                assert not is_pure_paper, "CRITICAL GUARD: Pure paper mode must NEVER call send_order"
                                                logger.info(f"⚡ [REAL Pre-Order] [EMA] {sym} 실제 평단가(${buy_p:.3f}) 기반 익절 주문 전송: ${tgt_p:.2f}")
                                                kis.send_order(sym, "SELL", qty, tgt_p, "00", exchange=sel_exch or "NAS")
                                                bot.send_message(f"🔒 [실전 익절 잠금/REAL] [EMA] {sym} 익절 주문 전송 완료 (평단가: ${buy_p:.3f} -> 목표가: ${tgt_p:.2f})")
                                    else:
                                        # ALPHA: +3.5% 지정가 사전 주문 전송 및 동적 타임컷 등록
                                        alpha_tp = getattr(Config, 'ALPHA_TP_PCT', 0.035)
                                        tgt_p = round_price(buy_p * (1.0 + alpha_tp))
                                        time_cut = sig.get('time_cut_minutes', sig.get('max_hold_min', getattr(Config, 'ALPHA_TIME_CUT_MINUTES', 45)))
                                        alpha_lbl = sig.get('alpha_id', 'ALPHA')
                                        if sym in portfolio.positions:
                                            portfolio.positions[sym]['target_price'] = tgt_p
                                            portfolio.positions[sym]['strategy'] = 'ALPHA'
                                            portfolio.positions[sym]['strategy_name'] = 'ALPHA'
                                            portfolio.positions[sym]['alpha_id'] = alpha_lbl
                                            portfolio.positions[sym]['entry_time'] = now
                                            portfolio.positions[sym]['time_cut_minutes'] = time_cut
                                            portfolio.positions[sym]['tp_pct'] = alpha_tp
                                            portfolio.positions[sym]['sl_pct'] = getattr(Config, 'ALPHA_SL_PCT', -0.10)
                                        if qty > 0:
                                            if is_pure_paper:
                                                logger.info(f"🔒 [PAPER Pre-Order] [ALPHA] {sym} 가상 익절 목표가(${tgt_p:.4f}) 감시 등록 완료 (평단가: ${buy_p:.3f}, 타임컷: {time_cut}분)")
                                                bot.send_message(f"🔒 [가상 익절 잠금/PAPER] [{alpha_lbl}] {sym} 익절 감시 등록 (평단가: ${buy_p:.3f} -> 목표가: ${tgt_p:.2f} [+3.5%] | 타임컷: {time_cut}분)")
                                            else:
                                                assert not is_pure_paper, "CRITICAL GUARD: Pure paper mode must NEVER call send_order"
                                                logger.info(f"⚡ [REAL Pre-Order] [ALPHA] {sym} 실제 평단가(${buy_p:.3f}) 기반 익절 주문 전송: ${tgt_p:.2f} (사전 예약)")
                                                kis.send_order(sym, "SELL", qty, tgt_p, "00", exchange=sel_exch or "NAS")
                                                bot.send_message(f"🔒 [실전 익절 잠금/REAL] [{alpha_lbl}] {sym} 익절 주문 전송 완료 (평단가: ${buy_p:.3f} -> 목표가: ${tgt_p:.2f} [+3.5%] | 타임컷: {time_cut}분)")
                            except Exception as pe_err:
                                logger.error(f"❌ 익절 사전 주문 처리 중 에러: {pe_err}")
                            return True
                        else:
                            logger.warning(f"🚌 [실패] {sym} 매수 실패. 금일 제외.")
                            portfolio.ban_list.add(sym)
                            candle_cache.pop(sym, None)
                            save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist, portfolio=portfolio)
                            return False
                return False

            # ---------------------------------------------------------
            # 1단계: 캔들 데이터 동기화 및 캐시 준비 (전 종목 일괄 수집)
            # ---------------------------------------------------------
            target_dfs = {}
            for sym in targets_to_check:
                try:
                    df = None
                    selected_exchange = None
                    if sym not in candle_cache:
                        for exch in ["NAS", "NYS", "AMS"]:
                            temp_df = kis.get_minute_candles(exch, sym, limit=1200)
                            if not temp_df.empty and len(temp_df) >= 26:
                                df = merge_candle_dfs(None, temp_df)
                                selected_exchange = exch
                                candle_cache[sym] = {'df': df, 'exch': exch}
                                break
                    else:
                        cached_data = candle_cache[sym]
                        old_df = cached_data['df']
                        exch = cached_data['exch']
                        selected_exchange = exch
                        new_df = kis.get_minute_candles(exch, sym, limit=120)
                        if not new_df.empty:
                            combined_df = merge_candle_dfs(old_df, new_df)
                            candle_cache[sym]['df'] = combined_df
                            df = combined_df
                        else:
                            df = old_df

                    if df is None or df.empty or len(df) < 26:
                        strategy._log_rejection(sym, "데이터 부족 (NAS/NYS/AMS 전체 탐색 실패)", 0.0)
                        candle_cache.pop(sym, None)
                        continue

                    candle_exporter.update_runtime_candles(sym, df, exchange=selected_exchange)
                    target_dfs[sym] = (df, selected_exchange)
                except Exception as c_err:
                    logger.error(f"⚠️ 캔들 수집 오류 ({sym}): {c_err}")

            # ---------------------------------------------------------
            # 2단계: [EMA 우선 패스] 전략1(EMA) 신호 우선 평가 및 슬롯 선점 (SharedCapitalManager 규칙 1:1 일치)
            # ---------------------------------------------------------
            if is_ema_active and portfolio.has_open_slot():
                for sym, (df, selected_exchange) in list(target_dfs.items()):
                    if not portfolio.has_open_slot():
                        break
                    if portfolio.is_holding(sym) or portfolio.is_banned(sym):
                        continue
                    try:
                        sig = strategy.check_entry(sym, df, now_time=now)
                        max_retries = 2
                        retry_count = 0
                        while sig and isinstance(sig, dict) and sig.get('type') == 'WAIT_NEW_BAR' and retry_count < max_retries:
                            time.sleep(1.0)
                            retry_count += 1
                            exch = selected_exchange or "NAS"
                            retry_df = kis.get_minute_candles(exch, sym, limit=120)
                            if not retry_df.empty:
                                base_df = candle_cache[sym]['df'] if sym in candle_cache else df
                                df = merge_candle_dfs(base_df, retry_df)
                                candle_cache[sym] = {'df': df, 'exch': exch}
                                target_dfs[sym] = (df, exch)
                                candle_exporter.update_runtime_candles(sym, df, exchange=exch)
                            sig = strategy.check_entry(sym, df, now_time=datetime.datetime.now(pytz.timezone('America/New_York')))

                        if sig and isinstance(sig, dict) and sig.get('type') == 'WAIT_NEW_BAR':
                            continue

                        if sig and sig.get('type') == 'BUY':
                            _execute_buy_flow(sym, sig, selected_exchange)
                        elif sig and sig.get('type') == 'DROP':
                            logger.info(f"🗑️ [DROP] {sym} 추세 붕괴 확인 -> 감시 해제 및 당일 영구 밴 등록")
                            portfolio.ban_list.add(sym)
                            strategy.banned_tickers.add(sym)
                            active_candidates.pop(sym, None)
                            candle_cache.pop(sym, None)
                            target_dfs.pop(sym, None)
                            save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist, portfolio=portfolio)
                    except Exception as e_err:
                        logger.error(f"❌ EMA 매수 로직 에러 ({sym}): {e_err}")

            # ---------------------------------------------------------
            # 3단계: [Alpha 스윕 패스] 잔여 슬롯/가용 자본에 대해 전략2(Alpha) 신호 평가 (EMA 미점유 잔여자본 스윕)
            # ---------------------------------------------------------
            if is_alpha_active and alpha_strategy and portfolio.has_open_slot():
                for sym, (df, selected_exchange) in list(target_dfs.items()):
                    if not portfolio.has_open_slot():
                        break
                    if portfolio.is_holding(sym) or portfolio.is_banned(sym):
                        continue
                    try:
                        alpha_sig = alpha_strategy.check_entry(sym, df, now_time=now)
                        if alpha_sig and alpha_sig.get('type') == 'BUY':
                            _execute_buy_flow(sym, alpha_sig, selected_exchange)
                    except Exception as a_err:
                        logger.error(f"❌ Alpha 매수 로직 에러 ({sym}): {a_err}")

            # ---------------------------------------------------------
            # 4단계: [Virtual Combined Engine 훅] 가상 페이퍼 엔진 캔들 전달
            # ---------------------------------------------------------
            if virtual_combined_engine:
                for sym, (df, _) in target_dfs.items():
                    try:
                        virtual_combined_engine.on_candle(sym, df, now_time=now)
                    except Exception as ve_err:
                        logger.error(f"⚠️ [VirtualEngine Error] {sym}: {ve_err}")

            # ---------------------------------------------------------
            # 🧪 [VirtualTrack] 감시 외 보유 가상 포지션 청산 조건 추적
            # ---------------------------------------------------------
            if virtual_combined_engine and virtual_combined_engine.positions:
                for v_sym in list(virtual_combined_engine.positions.keys()):
                    if v_sym not in targets_to_check and v_sym in candle_cache:
                        v_df = candle_cache[v_sym].get('df')
                        if v_df is not None and not v_df.empty:
                            try:
                                virtual_combined_engine.on_candle(v_sym, v_df, now_time=now)
                            except Exception as ve_err:
                                logger.error(f"⚠️ [VirtualEngine Exit Error] {v_sym}: {ve_err}")

            # ---------------------------------------------------------
            # C. [스캔] 신규 급등주 포착 (02~05초 매수 타점 집행 완료 후 여유 시간에 백그라운드 스캔)
            # ---------------------------------------------------------
            is_market_open_minute = (now.hour == 9 and now.minute == 0)
            if not is_market_open_minute:
                fresh_targets = listener.scan_markets(
                    ban_list=portfolio.ban_list,
                    active_candidates=active_candidates
                )
                
                if fresh_targets:
                    for sym in fresh_targets:
                        candle_exporter.register_candidate(sym, exchange=listener.get_candidate_exchange(sym))
                        if sym not in active_candidates:
                            active_candidates[sym] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist, portfolio=portfolio)
            
            if not portfolio.positions and portfolio.balance < 10:
                logger.info("🔄 [Sync] 매도 후 잔고 재동기화 수행...")
                portfolio.sync_balance() 

            time.sleep(0.1)

        except KeyboardInterrupt:
            logger.info("🛑 관리자에 의한 수동 종료")
            bot.send_message("🛑 시스템을 종료합니다.")
            save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist, portfolio=portfolio)
            run_live_candle_export(current_date_str, reason="manual_shutdown")
            send_spread_analysis_log(current_date_str)
            break
            
        except Exception as e:
            error_msg = f"⚠️ [ERROR] 시스템 오류: {e}\n👉 10초 후 재시도..."
            logger.error(error_msg)
            time.sleep(10)

if __name__ == "__main__":
    main()