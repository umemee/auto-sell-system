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
from infra.utils import (
    get_logger, round_price, get_trade_date_key, format_est_seed,
    format_pre_order_ack_message, format_exit_message,
    format_heartbeat_message, format_eod_summary_message
)
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
        session_start_equity = 0.0
        eod_summary_sent_date = ""
        bot_managed_tickers = []
        if portfolio is not None:
            initial_seed_today = getattr(portfolio, 'initial_seed_today', 0.0)
            session_start_equity = getattr(portfolio, 'session_start_equity', 0.0)
            eod_summary_sent_date = getattr(portfolio, 'eod_summary_sent_date', "")
            bot_managed_tickers = list(getattr(portfolio, 'bot_managed_tickers', set()))
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
                            if session_start_equity == 0.0:
                                session_start_equity = old_s.get("session_start_equity", 0.0)
                            if not eod_summary_sent_date:
                                eod_summary_sent_date = old_s.get("eod_summary_sent_date", "")
                            if not bot_managed_tickers:
                                bot_managed_tickers = old_s.get("bot_managed_tickers", [])
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
                    'is_bot_managed': p_data.get('is_bot_managed', True),
                    'is_manual': p_data.get('is_manual', False),
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
            "session_start_equity": float(session_start_equity),
            "eod_summary_sent_date": str(eod_summary_sent_date),
            "bot_managed_tickers": bot_managed_tickers,
            "trade_date": trade_date
        }
        
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)
            
    except Exception as e:
        logger.error(f"⚠️ 상태 저장 실패: {e}")

def load_state():
    """[설명] 저장된 상태 파일이 있다면 불러옵니다."""
    if not os.path.exists(STATE_FILE):
        return set(), {}, set(), 0.0, 0.0, {}, None, 0.0, 0.0, "", []
    
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
            
        current_trade_date = get_trade_date_key()
        saved_trade_date = state.get("trade_date")
        if not saved_trade_date:
            logger.info("📅 [상태 파일] trade_date 키가 없는 구버전 파일이므로 상태를 초기화합니다.")
            return set(), {}, set(), 0.0, 0.0, {}, None, 0.0, 0.0, "", []
            
        if saved_trade_date != current_trade_date:
            logger.info(f"📅 거래일 변경 감지(저장일: {saved_trade_date} vs 현재: {current_trade_date})으로 저장된 상태를 초기화합니다.")
            return set(), {}, set(), 0.0, 0.0, {}, saved_trade_date, 0.0, 0.0, "", []
            
        loaded_ban = set(state.get("ban_list", []))
        loaded_loss = set(state.get("loss_blacklist", []))
        daily_pnl = float(state.get("daily_realized_pnl", 0.0))
        unsettled = float(state.get("unsettled_sell_amount", 0.0))
        loaded_seed = float(state.get("initial_seed_today", 0.0))
        session_start_equity = float(state.get("session_start_equity", 0.0))
        eod_summary_sent_date = str(state.get("eod_summary_sent_date", ""))
        bot_managed_tickers = list(state.get("bot_managed_tickers", []))
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
            
        return loaded_ban, loaded_candidates, loaded_loss, daily_pnl, unsettled, loaded_positions_meta, saved_trade_date, loaded_seed, session_start_equity, eod_summary_sent_date, bot_managed_tickers
    
    except Exception as e:
        logger.error(f"⚠️ 상태 로드 실패: {e}")
        return set(), {}, set(), 0.0, 0.0, {}, None, 0.0, 0.0, "", []

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

def send_heartbeat_report(bot, portfolio, risk_filter, active_candidates, tz_kst, tz_et, trigger_reason="생존", is_premarket_1700=False):
    """
    [하트비트 생존 신고 및 매매 가능 시드 보고]
    - 표기 내용: 현재 매매 가능 시드, 총 자산, 계좌 기준 손익, 감시 종목(최대 8개), 차단 개수
    - 17:00 프리마켓 하트비트 시 전일 Ban 및 당일 손익 미표시(0)
    """
    try:
        portfolio.sync_balance()
        buyable_cash = portfolio.get_effective_balance() if hasattr(portfolio, 'get_effective_balance') else portfolio.balance
        now_et_hb = datetime.datetime.now(tz_et)
        ema_start_h = getattr(Config, 'EMA_ENTRY_START_HOUR_ET', 9)
        alpha_start_h = getattr(Config, 'ALPHA_ENTRY_START_HOUR_ET', 8)
        hb_strat = 'ALPHA' if ("Alpha" in trigger_reason or (alpha_start_h <= now_et_hb.hour < ema_start_h)) else 'EMA'
        order_limit = portfolio.get_max_order_amount(strategy=hb_strat)
        total_eq = portfolio.total_equity
        cur_k = datetime.datetime.now(tz_kst).strftime("%H:%M")

        watching_list = list(active_candidates)
        banned_list = list(portfolio.ban_list) if not is_premarket_1700 else []
        loss_list = list(risk_filter.loss_blacklist) if not is_premarket_1700 else []

        account_pnl = portfolio.get_account_pnl() if hasattr(portfolio, 'get_account_pnl') and not is_premarket_1700 else 0.0
        is_delayed = getattr(portfolio, 'is_delayed_settlement', False)

        from infra.utils import format_heartbeat_message
        msg = format_heartbeat_message(
            kst_time_str=cur_k,
            trigger_reason=trigger_reason,
            cash=buyable_cash,
            total_equity=total_eq,
            holdings_list=list(portfolio.positions.keys()),
            daily_account_pnl=account_pnl,
            watchlist=watching_list,
            ban_count=len(banned_list),
            loss_count=len(loss_list),
            is_delayed=is_delayed,
            is_premarket_startup=is_premarket_1700
        )
        bot.send_message(msg)
        logger.info(f"💓 [Heartbeat Sent] {trigger_reason} | 현금: ${buyable_cash:,.2f} | 총자산: ${total_eq:,.2f} | 손익: ${account_pnl:+,.2f}")
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
        loaded_start_equity = 0.0
        loaded_eod_sent_date = ""
        loaded_bot_managed = []
        if len(loaded_state_res) >= 11:
            loaded_ban, loaded_candidates, loaded_loss, loaded_daily_pnl, loaded_unsettled, loaded_positions_meta, saved_date, loaded_seed, loaded_start_equity, loaded_eod_sent_date, loaded_bot_managed = loaded_state_res[:11]
        elif len(loaded_state_res) == 8:
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
            portfolio.validate_and_apply_state(
                saved_date, loaded_daily_pnl, loaded_unsettled, loaded_seed,
                session_start_equity=loaded_start_equity,
                bot_managed_tickers=loaded_bot_managed
            )
        else:
            portfolio.daily_realized_pnl = loaded_daily_pnl
            portfolio.unsettled_sell_amount = loaded_unsettled
            portfolio.initial_seed_today = loaded_seed

        if loaded_eod_sent_date:
            portfolio.eod_summary_sent_date = loaded_eod_sent_date

        # 복구된 포지션 메타데이터(전략명, 봇 관리 태그, 목표가, 알파 청산 정보) 복원
        for t_sym, p_meta in loaded_positions_meta.items():
            if t_sym in portfolio.positions:
                strat_saved = p_meta.get('strategy', p_meta.get('strategy_name', 'EMA'))
                portfolio.positions[t_sym]['strategy'] = strat_saved
                portfolio.positions[t_sym]['strategy_name'] = p_meta.get('strategy_name', strat_saved)
                if 'is_bot_managed' in p_meta:
                    portfolio.positions[t_sym]['is_bot_managed'] = p_meta['is_bot_managed']
                    portfolio.positions[t_sym]['is_manual'] = p_meta.get('is_manual', not p_meta['is_bot_managed'])
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
                            try:
                                portfolio.positions[t_sym]['entry_time'] = datetime.datetime.fromisoformat(entry_t_str)
                            except Exception:
                                try:
                                    from dateutil.parser import parse as parse_date
                                    portfolio.positions[t_sym]['entry_time'] = parse_date(entry_t_str)
                                except Exception:
                                    pass
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
    watchdog_last_check = 0.0
    watchdog_alert_throttle = {}
    watchdog_fail_counts = {}
    watchdog_backoff_until = {}
    watchdog_backoff_level = {}
    alpha_err_throttle = {}

    # ---------------------------------------------------------
    # [메인 루프]
    # ---------------------------------------------------------
    while True:
        try:
            now = datetime.datetime.now(pytz.timezone('America/New_York'))
            current_minute_str = now.strftime("%H:%M")

            # =========================================================
            # 🚀 [초고속 매도 전용 차선] 보유 종목 실시간 1초 감시 (손절/익절 최우선)
            # =========================================================
            if portfolio.positions:
                for ticker in list(portfolio.positions.keys()):
                    pos = portfolio.positions.get(ticker)
                    if not pos:
                        continue
                    pos_exch = pos.get('exchange', 'NAS')
                    real_time_price = kis.get_current_price(ticker, exchange=pos_exch)
                    
                    if real_time_price and real_time_price > 0:
                        strat_type = pos.get('strategy', pos.get('strategy_name', 'EMA'))
                        is_alpha_pos = (strat_type == 'ALPHA')

                        # 🛡️ [목표가 복원 폴백] 메타데이터 유실 대비 설정값 기반 자동 복원
                        if not pos.get('target_price') or pos.get('target_price', 0) <= 0:
                            entry_p_rec = pos.get('entry_price', real_time_price)
                            tp_pct_rec = getattr(Config, 'ALPHA_TP_PCT', 0.035) if is_alpha_pos else getattr(Config, 'TARGET_PROFIT_PCT', 0.07)
                            pos['target_price'] = round_price(entry_p_rec * (1.0 + tp_pct_rec))
                            pos['tp_pct'] = tp_pct_rec
                            logger.info(f"🔄 [{ticker}] 목표가 메타데이터 복원 완료: ${pos['target_price']:.4f} (평단: ${entry_p_rec:.4f})")

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
                            entry_p = pos.get('entry_price', real_time_price)
                            trade_pnl = (real_time_price - entry_p) / entry_p if entry_p > 0 else -0.01

                            # 🛡️ [작업 E-5 / O-3 소프트웨어 익절 폴백] 실전 목표가 도달 시 execute_sell로 위임 (이중 취소 제거)
                            if not is_paper_mode and (reason in ['TAKE_PROFIT', 'TARGET_PROFIT_0.035']):
                                logger.info(f"🎯 [소프트웨어 익절 가동] {ticker} 현재가(${real_time_price:.4f}) >= 목표가 도달 -> 즉시 매도 집행")

                            result = order_manager.execute_sell(portfolio, ticker, reason, price=real_time_price)
                            if result:
                                bot.send_message(result['msg'])
            
                                # 🛑 손절 발생 즉시 3중 필터 블랙리스트에 추가 (손절일 경우만)
                                if trade_pnl < 0:
                                    risk_filter.register_trade_result(ticker, trade_pnl, reason=reason)
                                
                                if ticker in active_candidates:
                                    del active_candidates[ticker]
                                    
                                save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist, portfolio=portfolio)
                    
                    time.sleep(0.5)

            # =========================================================
            # 🛡️ [작업 E-4 / I / G / Q / 작업 1-3] 익절 선주문 워치독 & 실제 평단가 정정
            # (보유 종목 실시간 1초 손절/청산 감시 루프 뒤로 배치)
            # =========================================================
            if not is_paper_mode and portfolio.positions and (time.time() - watchdog_last_check >= 15.0):
                watchdog_last_check = time.time()
                try:
                    pending_sells = kis.get_pending_orders(side="SELL")
                    pending_sell_syms = {p['symbol'] for p in pending_sells}
                    now_ts = time.time()
                    for w_sym, w_pos in list(portfolio.positions.items()):
                        # [작업 I-1] 매도 진행 중인 종목은 워치독 재발주 배제
                        if w_sym in getattr(order_manager, 'selling_in_progress', set()):
                            logger.info(f"⏸️ [익절 워치독] {w_sym} 현재 매도 진행 중 -> 워치독 재발주 스킵")
                            continue

                        # [작업 Q-1] 연속 5회 실패 백오프 중인 종목 스킵
                        if now_ts < watchdog_backoff_until.get(w_sym, 0.0):
                            continue

                        # [작업 Q-3] 봇 생성 포지션만 대상, 수동 매수 포지션 재발주 제외
                        if w_pos.get('is_manual', False) or not w_pos.get('is_bot_managed', True):
                            logger.info(f"👤 [익절 워치독] {w_sym} 수동 매수 포지션 -> 익절 선주문 재발주 제외")
                            continue

                        # [작업 I-3] 발주 직후 30초 유예 시간 (미체결 미반영 구간 중복 방지)
                        last_ord_t = getattr(order_manager, 'recent_order_time', {}).get(w_sym, 0.0)
                        if now_ts - last_ord_t < 30.0:
                            continue

                        w_qty = w_pos.get('qty', 0)
                        w_entry = w_pos.get('entry_price', 0.0)
                        w_is_alpha = (w_pos.get('strategy') == 'ALPHA' or w_pos.get('strategy_name') == 'ALPHA')
                        w_tp_pct = w_pos.get('tp_pct', getattr(Config, 'ALPHA_TP_PCT', 0.035) if w_is_alpha else getattr(Config, 'TARGET_PROFIT_PCT', 0.07))
                        w_tgt = w_pos.get('target_price') or round_price(w_entry * (1.0 + w_tp_pct))
                        w_exch = w_pos.get('exchange', 'NASD')

                        if w_qty <= 0 or w_tgt <= 0:
                            continue

                        # [작업 Q-4] 발주 직전 브로커 잔고 수량과 로컬 수량 불일치 대조
                        actual_broker_qty = None
                        try:
                            broker_holdings = kis.get_balance()
                            if broker_holdings:
                                for b_item in broker_holdings:
                                    if b_item.get('symbol') == w_sym:
                                        actual_broker_qty = int(float(b_item.get('qty', 0)))
                                        break
                        except Exception as b_err:
                            logger.warning(f"⚠️ [익절 워치독] {w_sym} 브로커 잔고 수량 확인 중 오류: {b_err}")

                        if actual_broker_qty is not None and actual_broker_qty > 0 and actual_broker_qty != w_qty:
                            logger.warning(
                                f"⚠️ [익절 워치독 수량 불일치] {w_sym}: 로컬 수량({w_qty}주) != 브로커 잔고 수량({actual_broker_qty}주) "
                                f"-> 브로커 잔고 수량({actual_broker_qty}주) 우선 적용"
                            )
                            w_qty = actual_broker_qty
                            w_pos['qty'] = actual_broker_qty

                        if w_sym not in pending_sell_syms:
                            # 1) 미체결 주문이 누락된 경우 -> 재발주
                            logger.warning(f"⚠️ [익절 워치독] {w_sym} 미체결 익절 선주문 부재 감지! 즉시 재발주 (${w_tgt:.2f})")
                            re_res = kis.send_order(w_sym, "SELL", w_qty, w_tgt, "00", exchange=w_exch)
                            if re_res and re_res.get('rt_cd') == '0':
                                order_manager.recent_order_time[w_sym] = time.time()
                                watchdog_fail_counts.pop(w_sym, None)
                                watchdog_backoff_level.pop(w_sym, None)
                                watchdog_backoff_until.pop(w_sym, None)
                                logger.info(f"✅ [익절 워치독] {w_sym} 익절 재발주 접수 성공")
                            else:
                                err_m = re_res.get('msg1') if re_res else '무응답'
                                fail_cnt = watchdog_fail_counts.get(w_sym, 0) + 1
                                watchdog_fail_counts[w_sym] = fail_cnt
                                if fail_cnt >= 5:
                                    lvl = watchdog_backoff_level.get(w_sym, 0) + 1
                                    watchdog_backoff_level[w_sym] = lvl
                                    backoff_sec = 60 * (2 ** (min(lvl, 3) - 1)) # lvl 1: 60s, lvl 2: 120s, lvl 3: 240s
                                    watchdog_backoff_until[w_sym] = now_ts + backoff_sec
                                    watchdog_fail_counts[w_sym] = 0
                                    bot.send_message(
                                        f"🚨 [익절 워치독 경보] {w_sym} 연속 재발주 5회 실패!\n"
                                        f"• 백오프 가동: {backoff_sec // 60}분간 재발주 중단\n"
                                        f"• 사유: {err_m}"
                                    )
                                    logger.error(f"🚨 [익절 워치독] {w_sym} 연속 5회 재발주 실패 -> 백오프 {backoff_sec}초 설정")
                                else:
                                    last_alert_t = watchdog_alert_throttle.get(w_sym, 0)
                                    if now_ts - last_alert_t >= 300: # 5분 스로틀링
                                        watchdog_alert_throttle[w_sym] = now_ts
                                        bot.send_message(f"🚨 [익절 워치독 경보] {w_sym} 익절 선주문 누락 감지 및 재발주 실패({fail_cnt}/5)! 수동 확인 필요: {err_m}")
                        else:
                            # 2) [작업 G-1] 미체결 주문이 이미 존재하지만 실제 평단가 목표가와 오차가 있는 경우 정정
                            w_pendings = [p for p in pending_sells if p.get('symbol') == w_sym]
                            for p_ord in w_pendings:
                                try:
                                    ord_p = float(p_ord.get('ord_unpr') or p_ord.get('price') or 0.0)
                                except Exception:
                                    ord_p = 0.0

                                if ord_p > 0 and abs(ord_p - w_tgt) >= 0.005:
                                    logger.warning(f"⚠️ [익절 워치독] {w_sym} 기존 선주문가(${ord_p:.4f}) != 실제 평단 목표가(${w_tgt:.4f}) 오차 감지")
                                    curr_p = kis.get_current_price(w_sym, exchange=w_exch)
                                    if curr_p and curr_p >= w_tgt:
                                        logger.info(f"🎯 [소프트웨어 익절 전환] {w_sym} 현재가(${curr_p:.4f}) >= 목표가(${w_tgt:.4f}) -> 정정 대신 즉시 청산")
                                        order_manager.execute_sell(portfolio, w_sym, "TAKE_PROFIT", price=curr_p)
                                        break
                                    else:
                                        logger.info(f"🔄 [선주문 정정 발주] {w_sym} 기존 주문 취소 후 신규 목표가(${w_tgt:.4f})로 재발주")
                                        if order_manager._clear_pending_orders(w_sym):
                                            re_res = kis.send_order(w_sym, "SELL", w_qty, w_tgt, "00", exchange=w_exch)
                                            if re_res and re_res.get('rt_cd') == '0':
                                                order_manager.recent_order_time[w_sym] = time.time()
                                                watchdog_fail_counts.pop(w_sym, None)
                                                watchdog_backoff_level.pop(w_sym, None)
                                                watchdog_backoff_until.pop(w_sym, None)
                                                bot.send_message(f"🔄 [익절가 정정 완료/REAL] {w_sym} 실제 평단가 반영 정정 발주 성공 (${ord_p:.2f} -> ${w_tgt:.2f})")
                                                break
                                            else:
                                                err_m = re_res.get('msg1') if re_res else '무응답'
                                                fail_cnt = watchdog_fail_counts.get(w_sym, 0) + 1
                                                watchdog_fail_counts[w_sym] = fail_cnt
                                                if fail_cnt >= 5:
                                                    lvl = watchdog_backoff_level.get(w_sym, 0) + 1
                                                    watchdog_backoff_level[w_sym] = lvl
                                                    backoff_sec = 60 * (2 ** (min(lvl, 3) - 1))
                                                    watchdog_backoff_until[w_sym] = now_ts + backoff_sec
                                                    watchdog_fail_counts[w_sym] = 0
                                                    bot.send_message(
                                                        f"🚨 [익절 워치독 경보] {w_sym} 정정 재발주 연속 5회 실패!\n"
                                                        f"• 백오프 가동: {backoff_sec // 60}분간 재발주 중단\n"
                                                        f"• 사유: {err_m}"
                                                    )
                                                    logger.error(f"🚨 [익절 워치독] {w_sym} 정정 재발주 5회 실패 -> 백오프 {backoff_sec}초")
                                                break
                except Exception as wd_err:
                    logger.error(f"⚠️ [익절 워치독] 감시 중 예외 발생: {wd_err}")

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
                if getattr(portfolio, 'session_start_equity', 0.0) == 0.0:
                    portfolio.session_start_equity = portfolio.total_equity
                    logger.info(f"🌱 [Premarket Latch] 세션 시작 총자산 래치 고정: ${portfolio.session_start_equity:,.2f}")
                send_heartbeat_report(bot, portfolio, risk_filter, active_candidates, tz_kst, tz_et, trigger_reason="17:00 프리마켓 개시", is_premarket_1700=True)
                save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist, portfolio=portfolio)

            # ---------------------------------------------------------
            # 🛑 [EOD] 장 마감 강제 청산
            # ---------------------------------------------------------
            cutoff_time_str = getattr(Config, 'TIME_HARD_CUTOFF', "15:54")
            cutoff_h, cutoff_m = map(int, cutoff_time_str.split(':'))
            
            is_after_cutoff = (now.hour > cutoff_h) or (now.hour == cutoff_h and now.minute >= cutoff_m)
            
            if is_after_cutoff and not eod_processed:
                logger.warning(f"⏰ [장 마감] 강제 청산 체크 (Current: {now.strftime('%H:%M')} >= Cutoff: {cutoff_time_str})")
                
                # [작업 3-2] 청산할 포지션이 있을 때만 알림 발송 (없으면 무알림)
                if portfolio.positions:
                    bot.send_message(f"🚨 [장 마감] 강제 청산 실행")
                    for ticker in list(portfolio.positions.keys()):
                        order_manager.execute_sell(portfolio, ticker, "FORCE_EOD_EXIT", price=0)
                        time.sleep(0.2)

                if virtual_combined_engine:
                    virtual_combined_engine.force_eod_exit(now)
                
                save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist, portfolio=portfolio)
                run_live_candle_export(current_date_str, reason="eod")
                send_spread_analysis_log(current_date_str)
                logger.info("👋 [System] 장 마감 강제 청산 완료.")
                eod_processed = True

            # ---------------------------------------------------------
            # 🌙 [작업 3-1] 장 마감 직전 시드 알림 (15:55 ET / 04:55 KST)
            # ---------------------------------------------------------
            if (now.hour == 15 and now.minute >= 55) or (now.hour > 15):
                eod_sent_date = getattr(portfolio, 'eod_summary_sent_date', "")
                if eod_sent_date != current_date_str:
                    from infra.utils import format_eod_summary_message
                    portfolio.sync_balance()
                    cur_k_str = now.astimezone(tz_kst).strftime("%H:%M")
                    closed_list = getattr(portfolio, 'closed_trades_today', [])
                    w_cnt = sum(1 for t in closed_list if t.get('pnl', 0) > 0)
                    l_cnt = sum(1 for t in closed_list if t.get('pnl', 0) <= 0)
                    acct_pnl = portfolio.get_account_pnl()
                    eod_msg = format_eod_summary_message(
                        kst_time_str=cur_k_str,
                        total_equity=portfolio.total_equity,
                        cash=portfolio.balance,
                        holdings_dict=portfolio.positions,
                        daily_account_pnl=acct_pnl,
                        trade_count=len(closed_list),
                        win_count=w_cnt,
                        loss_count=l_cnt
                    )
                    bot.send_message(eod_msg)
                    logger.info(f"🌙 [EOD Summary Sent] {eod_msg}")
                    portfolio.eod_summary_sent_date = current_date_str
                    save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist, portfolio=portfolio)
            
            if not is_after_cutoff:
                eod_processed = False

            # =========================================================
            # 💓 [Heartbeat] 전략 매매 개시 생존 신고 (Alpha 21:00 / EMA 22:00)
            # =========================================================
            if now.hour >= 8 and not alpha_entry_heartbeat_sent and getattr(Config, 'ENABLE_ALPHA_STRATEGY', True):
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
                        reason=exit_reason,
                        is_broker_execution=True
                    )
                    pnl = record.get('pnl', 0.0)
                    ret_pct = record.get('return_pct', 0.0)

                    eff_cash = portfolio.get_effective_balance() if hasattr(portfolio, 'get_effective_balance') else portfolio.balance
                    tot_eq = getattr(portfolio, 'total_equity', eff_cash)
                    acct_pnl = portfolio.get_account_pnl() if hasattr(portfolio, 'get_account_pnl') else getattr(portfolio, 'daily_realized_pnl', 0.0)
                    is_del = getattr(portfolio, 'is_delayed_settlement', False)

                    from infra.utils import format_exit_message
                    msg = format_exit_message(
                        ticker=ticker,
                        strategy=strat_name,
                        entry_price=entry_p,
                        exit_price=sell_p,
                        ret_pct=ret_pct,
                        pnl=pnl,
                        cash=eff_cash,
                        total_equity=tot_eq,
                        daily_account_pnl=acct_pnl,
                        is_win=(pnl >= 0),
                        is_fill_price=True,
                        is_delayed=is_del,
                        qty=qty
                    )
                else:
                    logger.info(f"🎉 [익절 감지] {ticker} 목표가 도달 확인!")
                    strat_name = pos_info.get('strategy_name', pos_info.get('strategy', 'EMA'))
                    eff_cash = portfolio.get_effective_balance() if hasattr(portfolio, 'get_effective_balance') else portfolio.balance
                    tot_eq = getattr(portfolio, 'total_equity', eff_cash)
                    acct_pnl = portfolio.get_account_pnl() if hasattr(portfolio, 'get_account_pnl') else getattr(portfolio, 'daily_realized_pnl', 0.0)
                    is_del = getattr(portfolio, 'is_delayed_settlement', False)
                    from infra.utils import format_exit_message
                    msg = format_exit_message(
                        ticker=ticker,
                        strategy=strat_name,
                        entry_price=entry_p,
                        exit_price=sell_p,
                        ret_pct=0.0,
                        pnl=0.0,
                        cash=eff_cash,
                        total_equity=tot_eq,
                        daily_account_pnl=acct_pnl,
                        is_win=True,
                        is_fill_price=False,
                        is_delayed=is_del,
                        qty=qty
                    )
                
                bot.send_message(msg)
                portfolio.ban_list.add(ticker)
                if hasattr(portfolio, 'bot_managed_tickers'):
                    portfolio.bot_managed_tickers.discard(ticker)
                
                if ticker in active_candidates:
                    del active_candidates[ticker]
                    
                save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist, portfolio=portfolio)

            # ---------------------------------------------------------
            # D. [매수] 진입 타점 확인 (Fast-Path 최우선 집행 & 전략별 시간 분리)
            # 02~05초 골든 타임 보장을 위해 신규 급등주 스캔보다 매수 타점을 먼저 즉시 평가!
            # ---------------------------------------------------------
            ema_start_hour = getattr(Config, 'EMA_ENTRY_START_HOUR_ET', 9)
            alpha_start_hour = getattr(Config, 'ALPHA_ENTRY_START_HOUR_ET', 8)
            is_ema_active = (now.hour >= ema_start_hour)
            is_alpha_active = getattr(Config, 'ENABLE_ALPHA_STRATEGY', True) and (now.hour >= alpha_start_hour)

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
                sig_strat = sig.get('strategy_name', sig.get('strategy', 'EMA'))
                if not portfolio.has_open_slot(strategy=sig_strat):
                    logger.warning(f"🚌 [Missed Bus] {sym} ({sig_strat}) 진입 신호 왔으나 자리 없음 -> 영구 제외")
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

                if portfolio.has_open_slot(strategy=sig_strat):
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
                                                pos_exch = portfolio.positions.get(sym, {}).get('exchange') or getattr(kis, 'last_order_exchange', 'NASD')
                                                logger.info(f"⚡ [REAL Pre-Order] [EMA] {sym} 실제 평단가(${buy_p:.3f}) 기반 익절 주문 전송 ({pos_exch}): ${tgt_p:.2f}")
                                                order_sent = False
                                                last_err = ""
                                                for attempt in range(1, 4):
                                                    sell_res = kis.send_order(sym, "SELL", qty, tgt_p, "00", exchange=pos_exch)
                                                    if sell_res and sell_res.get('rt_cd') == '0':
                                                        time.sleep(1.0)
                                                        try:
                                                            pending_sells = kis.get_pending_orders(sym, side="SELL")
                                                            odno = sell_res.get('output', {}).get('ODNO')
                                                            is_confirmed = any(p.get('odno') == odno or p.get('symbol') == sym for p in pending_sells)
                                                        except Exception as chk_e:
                                                            logger.warning(f"⚠️ [EMA Pre-Order] {sym} 미체결 검증 중 오류: {chk_e}")
                                                            is_confirmed = True
                                                        
                                                        if not is_confirmed:
                                                            time.sleep(1.0)
                                                            try:
                                                                pending_sells = kis.get_pending_orders(sym, side="SELL")
                                                                is_confirmed = any(p.get('symbol') == sym for p in pending_sells)
                                                            except Exception:
                                                                is_confirmed = True

                                                        if is_confirmed:
                                                            order_sent = True
                                                            bot.send_message(format_pre_order_ack_message(sym, target_price=tgt_p, is_success=True))
                                                            break
                                                        else:
                                                            last_err = "주문 접수 응답 성공했으나 미체결 목록 미확인 (누락 의심)"
                                                            logger.warning(f"⚠️ [EMA Pre-Order] {sym} 시도 #{attempt}: {last_err}")
                                                            time.sleep(0.5)
                                                    else:
                                                        last_err = sell_res.get('msg1', '알 수 없는 오류') if sell_res else '무응답'
                                                        logger.warning(f"⚠️ [EMA Pre-Order] {sym} 익절 주문 시도 #{attempt} 실패: {last_err}")
                                                        time.sleep(0.5)
                                                if not order_sent:
                                                    logger.error(f"❌ [EMA Pre-Order] {sym} 익절 주문 최종 실패: {last_err}")
                                                    bot.send_message(format_pre_order_ack_message(sym, target_price=tgt_p, is_success=False, err_msg="HTS에서 확인 필요"))
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
                                                pos_exch = portfolio.positions.get(sym, {}).get('exchange') or getattr(kis, 'last_order_exchange', 'NASD')
                                                logger.info(f"⚡ [REAL Pre-Order] [ALPHA] {sym} 실제 평단가(${buy_p:.3f}) 기반 익절 주문 전송 ({pos_exch}): ${tgt_p:.2f} (사전 예약)")
                                                order_sent = False
                                                last_err = ""
                                                for attempt in range(1, 4):
                                                    sell_res = kis.send_order(sym, "SELL", qty, tgt_p, "00", exchange=pos_exch)
                                                    if sell_res and sell_res.get('rt_cd') == '0':
                                                        time.sleep(1.0)
                                                        try:
                                                            pending_sells = kis.get_pending_orders(sym, side="SELL")
                                                            odno = sell_res.get('output', {}).get('ODNO')
                                                            is_confirmed = any(p.get('odno') == odno or p.get('symbol') == sym for p in pending_sells)
                                                        except Exception as chk_e:
                                                            logger.warning(f"⚠️ [ALPHA Pre-Order] {sym} 미체결 검증 중 오류: {chk_e}")
                                                            is_confirmed = True

                                                        if not is_confirmed:
                                                            time.sleep(1.0)
                                                            try:
                                                                pending_sells = kis.get_pending_orders(sym, side="SELL")
                                                                is_confirmed = any(p.get('symbol') == sym for p in pending_sells)
                                                            except Exception:
                                                                is_confirmed = True

                                                        if is_confirmed:
                                                            order_sent = True
                                                            bot.send_message(format_pre_order_ack_message(sym, target_price=tgt_p, is_success=True))
                                                            break
                                                        else:
                                                            last_err = "주문 접수 응답 성공했으나 미체결 목록 미확인 (누락 의심)"
                                                            logger.warning(f"⚠️ [ALPHA Pre-Order] {sym} 시도 #{attempt}: {last_err}")
                                                            time.sleep(0.5)
                                                    else:
                                                        last_err = sell_res.get('msg1', '알 수 없는 오류') if sell_res else '무응답'
                                                        logger.warning(f"⚠️ [ALPHA Pre-Order] {sym} 익절 주문 시도 #{attempt} 실패: {last_err}")
                                                        time.sleep(0.5)
                                                if not order_sent:
                                                    logger.error(f"❌ [ALPHA Pre-Order] {sym} 익절 주문 최종 실패: {last_err}")
                                                    bot.send_message(format_pre_order_ack_message(sym, target_price=tgt_p, is_success=False, err_msg="HTS에서 확인 필요"))
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
            if is_ema_active and portfolio.has_open_slot(strategy='EMA'):
                for sym, (df, selected_exchange) in list(target_dfs.items()):
                    if not portfolio.has_open_slot(strategy='EMA'):
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
            if is_alpha_active and alpha_strategy and portfolio.has_open_slot(strategy='ALPHA'):
                for sym, (df, selected_exchange) in list(target_dfs.items()):
                    if not portfolio.has_open_slot(strategy='ALPHA'):
                        break
                    if portfolio.is_holding(sym) or portfolio.is_banned(sym):
                        continue
                    try:
                        alpha_sig = alpha_strategy.check_entry(sym, df, now_time=now)
                        if alpha_sig and alpha_sig.get('type') == 'BUY':
                            _execute_buy_flow(sym, alpha_sig, selected_exchange)
                    except Exception as a_err:
                        logger.error(f"❌ Alpha 매수 로직 에러 ({sym}): {a_err}")
                        err_key = f"{type(a_err).__name__}_{str(a_err)}"
                        now_ts = time.time()
                        last_err_alert = alpha_err_throttle.get(err_key, 0)
                        if now_ts - last_err_alert >= 300: # 5분 스로틀링
                            alpha_err_throttle[err_key] = now_ts
                            bot.send_message(f"🚨 [Alpha 전략 에러 경보] ({sym}): {a_err}")

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