# infra/virtual_combined_engine.py
"""
[Virtual Combined Engine - Paper Trading Track]
전략1(EMA)과 전략2(Alpha)의 독립 신호를 단일 타임라인으로 통합하여,
SharedCapitalManager(FIFO 공유 자본 풀, seed=1.0)를 통해
실시간 1분봉 데이터로부터 가상 체결 및 스킵을 집행/영속화하는 엔진.

특징:
1. 실계좌 KIS 주문 API 전면 배제 (가상 시뮬레이터로만 구동)
2. 실전 주문 평가/전송이 완전히 끝난 직후 on_candle() 훅 실행 (실전 지연 0ms 보장)
3. 전략1(EMA) 슬롯 0.5 고정 vs 전략2(Alpha) 잔여 가용자본 스윕
4. ZERO_QTY_ROUNDING 및 INSUFFICIENT_CAPITAL 스킵 사유 정확히 기록
5. logs/paper/ 디렉터리에 paper_trades_YYYYMMDD.csv, paper_trades_YYYYMMDD.jsonl, skipped_signals.csv 영속화
"""

import os
import csv
import json
import time
import datetime
import pytz
from pathlib import Path
from typing import Dict, Any, Optional, List
import pandas as pd
import numpy as np

from config import Config
from infra.utils import get_logger, round_price
from infra.paper_execution_engine import VirtualExecutionEngine
from strategy import EmaStrategy

try:
    from infra.capital_manager import SharedCapitalManager
except ImportError:
    from capital_manager import SharedCapitalManager


class AlphaLiveAdapter:
    """
    [Alpha Live Strategy Adapter]
    Alpha01 (Pre-market Path Regime) 및 Alpha02 (OR-10 Breakout)의
    실시간 1분봉 진입/청산 조건을 평가하는 어댑터.
    (alpha_portfolio_engine.py 원본 로직 동결 이식)
    """
    def __init__(self):
        self.logger = get_logger("AlphaLiveAdapter")
        self.universe_min_price = 1.00
        self.universe_max_price = 5.00
        self.trading_window_start = "08:30:00"
        self.trading_window_end = "09:20:00"
        self.a01_cutoff_time = "08:45:00"
        self.a01_early_paths = ("C", "D")
        self.a01_late_paths = ("A", "C", "D")
        self.a02_or_window_bars = 10
        self.a02_max_range_pct = 4.00
        self.tp_pct = 0.035
        self.sl_pct = -0.10
        
        self.or10_cache = {}  # {ticker: {'or_high': float, 'or_low': float, 'range_pct': float, 'date': str}}
        self.triggered_today = set()

    def daily_reset(self):
        self.or10_cache.clear()
        self.triggered_today.clear()

    def check_entry(self, ticker: str, df: pd.DataFrame, now_time: datetime.datetime) -> Optional[Dict[str, Any]]:
        """실시간 1분봉 데이터(df)로부터 Alpha01 및 Alpha02 진입 타점을 확인"""
        if df is None or df.empty or len(df) < 5:
            return None

        # 타임존 보정 (미국 동부시간 기준)
        if hasattr(now_time, 'tzinfo') and now_time.tzinfo is not None:
            now_et = now_time.astimezone(pytz.timezone('US/Eastern'))
        else:
            now_et = pytz.timezone('US/Eastern').localize(now_time)

        t_str = now_et.strftime("%H:%M:%S")
        d_str = now_et.strftime("%Y-%m-%d")

        # 거래 윈도우 검사 (08:30:00 ~ 09:20:00)
        if not (self.trading_window_start <= t_str < self.trading_window_end):
            return None

        curr_bar = df.iloc[-1]
        curr_price = float(curr_bar['close'])

        # 유니버스 가격대 검사 ($1.00 ~ $5.00)
        if not (self.universe_min_price <= curr_price <= self.universe_max_price):
            return None

        key_today = f"{d_str}_{ticker}"

        # -----------------------------------------------------------------
        # 1. Alpha 02 검사 (OR-10 압축 후 상방 돌파)
        # -----------------------------------------------------------------
        if key_today not in self.triggered_today:
            # 08:30 ~ 08:40 사이 캔들로 OR-10 계산
            or_cache = self.or10_cache.get(ticker)
            if or_cache is None or or_cache.get('date') != d_str:
                # df에서 당일 08:30 ~ 08:39 구간 캔들 필터링
                if 'time' in df.columns:
                    time_col = df['time'].astype(str).str.replace(':', '').str.zfill(6)
                    or_bars = df[(time_col >= '083000') & (time_col < '084000')]
                else:
                    or_bars = pd.DataFrame()

                if len(or_bars) >= 5:
                    or_h = float(or_bars['high'].max())
                    or_l = float(or_bars['low'].min())
                    if or_l > 0:
                        rng_pct = ((or_h - or_l) / or_l) * 100.0
                        self.or10_cache[ticker] = {
                            'or_high': or_h,
                            'or_low': or_l,
                            'range_pct': rng_pct,
                            'date': d_str
                        }

            or_info = self.or10_cache.get(ticker)
            if or_info and or_info.get('date') == d_str:
                if or_info['range_pct'] < self.a02_max_range_pct and t_str >= "08:40:00":
                    if curr_price > or_info['or_high']:
                        self.triggered_today.add(key_today)
                        return {
                            'type': 'BUY',
                            'strategy': 'ALPHA',
                            'alpha_id': 'Alpha 02',
                            'ticker': ticker,
                            'price': curr_price,
                            'tp_pct': self.tp_pct,
                            'sl_pct': self.sl_pct,
                            'max_hold_min': 35,
                            'time': now_et
                        }

        # -----------------------------------------------------------------
        # 2. Alpha 01 검사 (Time x Path Regime)
        # -----------------------------------------------------------------
        if key_today not in self.triggered_today:
            b5_df = df.iloc[-5:].copy().reset_index(drop=True)
            if len(b5_df) >= 5:
                bar_rets, bar_u_wicks = [], []
                for i in range(5):
                    prev_c = b5_df.iloc[i - 1]['close'] if i > 0 else (df.iloc[-6]['close'] if len(df) >= 6 else b5_df.iloc[i]['open'])
                    ret_val = ((b5_df.iloc[i]['close'] - prev_c) / prev_c) * 100.0 if prev_c > 0 else 0.0
                    bar_rets.append(ret_val)

                    o_val = float(b5_df.iloc[i]['open'])
                    c_val = float(b5_df.iloc[i]['close'])
                    h_val = float(b5_df.iloc[i]['high'])
                    u_wick = h_val - max(o_val, c_val)
                    bar_u_wicks.append((u_wick / o_val * 100.0) if o_val > 0 else 0.0)

                c_start = float(df.iloc[-6]['close'] if len(df) >= 6 else b5_df.iloc[0]['open'])
                cum_ret = ((curr_price - c_start) / c_start) * 100.0 if c_start > 0 else 0.0
                h5 = float(b5_df['high'].max())
                low_idx = int(b5_df['low'].idxmin())
                sum_u = sum(bar_u_wicks)

                is_cont = bar_rets[2] > 0 and bar_rets[3] > 0 and bar_rets[4] > 0
                is_spk = bar_rets[4] >= 3.5

                if cum_ret > 2.0 and (is_spk or is_cont):
                    p_type = "B"
                elif sum_u >= 4.0 or (h5 > 0 and ((curr_price - h5) / h5 * 100.0 <= -2.5)):
                    p_type = "C"
                elif cum_ret <= 2.0 and low_idx in [2, 3, 4] and bar_rets[4] > 0:
                    p_type = "A"
                else:
                    p_type = "D"

                is_valid = False
                if t_str < self.a01_cutoff_time and p_type in self.a01_early_paths:
                    is_valid = True
                elif t_str >= self.a01_cutoff_time and p_type in self.a01_late_paths:
                    is_valid = True

                if is_valid:
                    self.triggered_today.add(key_today)
                    return {
                        'type': 'BUY',
                        'strategy': 'ALPHA',
                        'alpha_id': 'Alpha 01',
                        'ticker': ticker,
                        'price': curr_price,
                        'tp_pct': self.tp_pct,
                        'sl_pct': self.sl_pct,
                        'max_hold_min': 45,
                        'time': now_et
                    }

        return None

    def check_exit(self, ticker: str, position: Dict[str, Any], current_price: float, now_time: datetime.datetime) -> Optional[Dict[str, Any]]:
        """Alpha 포지션 청산 조건 확인 (익절 +3.5%, 손절 -10.0%, 타임컷)"""
        entry_price = position.get('entry_price', 0.0)
        if entry_price <= 0:
            return None

        pnl_pct = (current_price - entry_price) / entry_price
        tp_target = position.get('tp_pct', self.tp_pct)
        sl_target = position.get('sl_pct', self.sl_pct)

        if pnl_pct >= tp_target:
            return {'type': 'SELL', 'reason': 'TAKE_PROFIT', 'price': current_price}
        elif pnl_pct <= sl_target:
            return {'type': 'SELL', 'reason': 'STOP_LOSS', 'price': current_price}

        # 타임컷 검사
        entry_time = position.get('entry_time')
        if entry_time:
            if hasattr(now_time, 'tzinfo') and now_time.tzinfo is not None and hasattr(entry_time, 'tzinfo') and entry_time.tzinfo is None:
                entry_time = pytz.timezone('US/Eastern').localize(entry_time)
            elif hasattr(entry_time, 'tzinfo') and entry_time.tzinfo is not None and hasattr(now_time, 'tzinfo') and now_time.tzinfo is None:
                now_time = pytz.timezone('US/Eastern').localize(now_time)

            elapsed_minutes = (now_time - entry_time).total_seconds() / 60.0
            max_hold = position.get('max_hold_min', 45)
            if elapsed_minutes >= max_hold:
                return {'type': 'SELL', 'reason': 'TIME_CUT_EOS', 'price': current_price}

        return None


class VirtualCombinedEngine:
    """
    [Virtual Combined Paper Engine]
    전략1(EMA)과 전략2(Alpha)를 FIFO 공유 자본 풀(seed=1.0)로 결합하여
    실전봇 1분봉 루프 후단에서 실시간 가상 매매를 집행하고 기록하는 엔진.
    """
    def __init__(self, kis_api=None, initial_capital: float = 2000.0):
        self.kis = kis_api
        self.logger = get_logger("VirtualCombined")
        self.initial_capital = float(initial_capital)
        self.balance = float(initial_capital)
        self.total_equity = float(initial_capital)
        self.daily_realized_pnl = 0.0
        
        # 포지션 관리: {ticker: {...}}
        self.positions: Dict[str, Dict[str, Any]] = {}
        
        # 1. 공유 자본 관리자 (seed=1.0)
        self.capital_manager = SharedCapitalManager(seed=1.0)
        
        # 2. 가상 체결 엔진 (Paper Execution Engine)
        self.execution_engine = VirtualExecutionEngine(kis_api)
        
        # 3. 전략 서브모듈
        self.strategy_ema = EmaStrategy()
        self.strategy_alpha = AlphaLiveAdapter()
        
        # 4. 저장소 경로
        self.log_dir = Path(__file__).resolve().parent.parent / "logs" / "paper"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.skipped_signals_file = self.log_dir / "skipped_signals.csv"

        self.logger.info(
            f"🧪 [VirtualCombinedEngine] 가상 결합 엔진 초기화 완료 | "
            f"초기 시드: ${self.initial_capital:,.2f} | 공유자본풀: 1.0 (EMA: 0.5슬롯, Alpha: 전량스윕)"
        )

    def daily_reset(self):
        """[Daily Reset] 날짜 변경 시 가상 상태 초기화"""
        self.capital_manager.reset()
        self.positions.clear()
        self.daily_realized_pnl = 0.0
        self.strategy_ema.daily_reset()
        self.strategy_alpha.daily_reset()
        self.total_equity = self.balance
        self.logger.info("🔄 [VirtualCombinedEngine] 일일 자본 및 세션 상태 초기화 완료")

    def force_eod_exit(self, now_time: datetime.datetime):
        """[EOD Cutoff] 장 마감 강제 가상 청산"""
        if not self.positions:
            return
        self.logger.warning(f"⏰ [VirtualTrack EOD] 가상 포지션 {len(self.positions)}개 일괄 청산 실행")
        for ticker in list(self.positions.keys()):
            pos = self.positions[ticker]
            cur_price = pos.get('current_price', pos.get('entry_price', 0.0))
            self._execute_paper_sell(ticker, reason="FORCE_EOD_EXIT", signal_price=cur_price, now_time=now_time)

    def _log_skipped_signal(self, date_str: str, strategy: str, ticker: str, entry_time_str: str, reason: str, available_cap: float):
        """스킵 신호 로깅 (결합 벤치마크 규격 100% 호환)"""
        today_code = date_str.replace("-", "")
        daily_csv = self.log_dir / f"skipped_signals_{today_code}.csv"
        
        fieldnames = ["date", "strategy", "ticker", "entry_time", "reason", "available_capital_at_skip"]
        row = {
            "date": date_str,
            "strategy": strategy,
            "ticker": ticker,
            "entry_time": entry_time_str,
            "reason": reason,
            "available_capital_at_skip": round(available_cap, 4)
        }

        for target_file in [self.skipped_signals_file, daily_csv]:
            file_exists = target_file.exists()
            try:
                with open(target_file, "a", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    if not file_exists:
                        writer.writeheader()
                    writer.writerow(row)
            except Exception as e:
                self.logger.error(f"⚠️ [Skip Log Error] 스킵 로그 기록 실패: {e}")

        self.logger.info(
            f"🚫 [VIRTUAL SKIP] [{strategy}] {ticker} | 사유: {reason} | 잔여가용자본: {available_cap*100:.1f}%"
        )

    def _execute_paper_buy(self, ticker: str, strategy: str, qty: int, signal_price: float, capital_frac: float, now_time: datetime.datetime, extra_info: Optional[Dict] = None) -> bool:
        """가상 매수 집행 및 포트폴리오/자본풀 반영"""
        res = self.execution_engine.execute_paper_buy(
            ticker=ticker,
            qty=qty,
            signal_price=signal_price,
            exchange="NAS"
        )

        if not res or res.get('rt_cd') != '0':
            self.logger.error(f"❌ [Virtual Buy Fail] {ticker} 가상 체결 실패")
            return False

        output = res.get('output', {})
        fill_price = float(output.get('fill_price', signal_price))
        cost = fill_price * qty * 1.001  # 수수료 0.1% 반영

        self.balance -= cost
        pos_record = {
            'ticker': ticker,
            'strategy': strategy,
            'qty': qty,
            'entry_price': fill_price,
            'current_price': fill_price,
            'highest_price': fill_price,
            'entry_time': now_time,
            'capital_frac': capital_frac,
            'tp_pct': (extra_info.get('tp_pct', 0.07) if extra_info else 0.07),
            'sl_pct': (extra_info.get('sl_pct', -0.10) if extra_info else -0.10),
            'max_hold_min': (extra_info.get('max_hold_min', 0) if extra_info else 0)
        }
        self.positions[ticker] = pos_record

        # 자본 사용량 갱신
        self.capital_manager.used += capital_frac
        
        # 총자산 재계산
        curr_stock_val = sum(p['qty'] * p['current_price'] for p in self.positions.values())
        self.total_equity = self.balance + curr_stock_val

        self.logger.info(
            f"⚡ [VIRTUAL BUY FILLED] {ticker} ({strategy}) | {qty}주 @ ${fill_price:.4f} | "
            f"슬롯: {capital_frac*100:.1f}% | 자본사용률: {self.capital_manager.used*100:.1f}% | 잔고: ${self.balance:,.2f}"
        )
        return True

    def _execute_paper_sell(self, ticker: str, reason: str, signal_price: float, now_time: datetime.datetime):
        """가상 매도 집행 및 포트폴리오/자본풀 반환"""
        if ticker not in self.positions:
            return

        pos = self.positions[ticker]
        qty = pos['qty']
        entry_price = pos['entry_price']
        capital_frac = pos.get('capital_frac', 0.5)
        strat = pos.get('strategy', 'EMA')

        res = self.execution_engine.execute_paper_sell(
            ticker=ticker,
            qty=qty,
            entry_price=entry_price,
            signal_price=signal_price,
            reason=reason,
            exchange="NAS"
        )

        fill_price = signal_price
        if res and res.get('rt_cd') == '0':
            output = res.get('output', {})
            fill_price = float(output.get('fill_price', signal_price))

        revenue = fill_price * qty * 0.999  # 수수료 0.1% 반영
        pnl = revenue - (entry_price * qty)
        self.daily_realized_pnl += pnl
        self.balance += revenue

        # 자본풀 반환 (FIFO Release)
        self.capital_manager.used = max(0.0, self.capital_manager.used - capital_frac)
        del self.positions[ticker]

        # 총자산 재계산
        curr_stock_val = sum(p['qty'] * p['current_price'] for p in self.positions.values())
        self.total_equity = self.balance + curr_stock_val

        self.logger.info(
            f"🔴 [VIRTUAL SELL FILLED] {ticker} ({strat}) | {qty}주 @ ${fill_price:.4f} | "
            f"사유: {reason} | PnL: ${pnl:+,.2f} | 잔여 자본사용률: {self.capital_manager.used*100:.1f}% | 잔고: ${self.balance:,.2f}"
        )

    def on_candle(self, ticker: str, df: pd.DataFrame, now_time: datetime.datetime):
        """
        [1분봉 확정 이벤트 훅]
        실전 매수/매도 검사 및 주문 전송이 완전히 완료된 직후 호출됩니다.
        (실전 계좌 주문 지연 0ms 보장)
        """
        if df is None or df.empty or len(df) < 5:
            return

        curr_bar = df.iloc[-1]
        curr_price = float(curr_bar['close'])
        date_str = now_time.strftime("%Y-%m-%d")
        time_str = now_time.strftime("%Y-%m-%d %H:%M:%S")

        # =================================================================
        # 1. 기존 가상 포지션 청산 조건 검사 (Exit Check)
        # =================================================================
        if ticker in self.positions:
            pos = self.positions[ticker]
            pos['current_price'] = curr_price
            if curr_price > pos.get('highest_price', 0):
                pos['highest_price'] = curr_price

            strat = pos.get('strategy', 'EMA')
            exit_signal = None

            if strat == 'EMA':
                exit_signal = self.strategy_ema.check_exit(
                    ticker=ticker,
                    position=pos,
                    current_price=curr_price,
                    now_time=now_time
                )
            else:  # ALPHA
                exit_signal = self.strategy_alpha.check_exit(
                    ticker=ticker,
                    position=pos,
                    current_price=curr_price,
                    now_time=now_time
                )

            if exit_signal and exit_signal.get('type') == 'SELL':
                reason = exit_signal.get('reason', 'UNKNOWN_EXIT')
                self._execute_paper_sell(ticker, reason, curr_price, now_time)
                return

        # =================================================================
        # 2. 신규 가상 진입 신호 평가 (Entry Check)
        # =================================================================
        if ticker in self.positions:
            return  # 이미 보유 중이면 추가 진입 안 함

        # 후보 신호 수집 (동일 분 충돌 시 전략1 EMA 우선 순회 원칙)
        candidates = []

        # 2.1 전략1 (EMA) 신호 확인
        ema_sig = self.strategy_ema.check_entry(ticker, df, now_time=now_time)
        if ema_sig and isinstance(ema_sig, dict) and ema_sig.get('type') == 'BUY':
            candidates.append({
                'strategy': 'EMA',
                'ticker': ticker,
                'price': float(ema_sig.get('price', curr_price)),
                'extra': ema_sig
            })

        # 2.2 전략2 (Alpha) 신호 확인
        alpha_sig = self.strategy_alpha.check_entry(ticker, df, now_time=now_time)
        if alpha_sig and isinstance(alpha_sig, dict) and alpha_sig.get('type') == 'BUY':
            candidates.append({
                'strategy': 'ALPHA',
                'ticker': ticker,
                'price': float(alpha_sig.get('price', curr_price)),
                'extra': alpha_sig
            })

        if not candidates:
            return

        # =================================================================
        # 3. FIFO 공유 자본 풀 배분 및 스킵 판정 (SharedCapitalManager)
        # =================================================================
        for cand in candidates:
            strat = cand['strategy']
            p_entry = cand['price']
            extra = cand.get('extra', {})
            available = self.capital_manager.get_available_capital()

            if strat == 'EMA':
                # 전략1: 슬롯 0.5 고정 요구 (가용자본 >= 0.5)
                if available < 0.5 - self.capital_manager.epsilon:
                    self._log_skipped_signal(
                        date_str=date_str,
                        strategy=strat,
                        ticker=ticker,
                        entry_time_str=time_str,
                        reason="INSUFFICIENT_CAPITAL",
                        available_cap=available
                    )
                    continue

                size = 0.5
                alloc_dollar = self.total_equity * size
                qty = int(alloc_dollar // p_entry) if p_entry > 0 else 0

                if qty <= 0:
                    self._log_skipped_signal(
                        date_str=date_str,
                        strategy=strat,
                        ticker=ticker,
                        entry_time_str=time_str,
                        reason="ZERO_QTY_ROUNDING",
                        available_cap=available
                    )
                    continue

                # 체결 집행
                self._execute_paper_buy(
                    ticker=ticker,
                    strategy=strat,
                    qty=qty,
                    signal_price=p_entry,
                    capital_frac=size,
                    now_time=now_time,
                    extra_info=extra
                )
                break  # 한 종목에 대해 진입 성공 시 종료

            elif strat == 'ALPHA':
                # 전략2: 잔여 가용자본 전부 스윕 (available > 0)
                if available <= 0.0001:
                    self._log_skipped_signal(
                        date_str=date_str,
                        strategy=strat,
                        ticker=ticker,
                        entry_time_str=time_str,
                        reason="INSUFFICIENT_CAPITAL",
                        available_cap=available
                    )
                    continue

                size = available
                alloc_dollar = self.total_equity * size
                qty = int(alloc_dollar // p_entry) if p_entry > 0 else 0

                if qty <= 0:
                    self._log_skipped_signal(
                        date_str=date_str,
                        strategy=strat,
                        ticker=ticker,
                        entry_time_str=time_str,
                        reason="ZERO_QTY_ROUNDING",
                        available_cap=available
                    )
                    continue

                # 체결 집행
                self._execute_paper_buy(
                    ticker=ticker,
                    strategy=strat,
                    qty=qty,
                    signal_price=p_entry,
                    capital_frac=size,
                    now_time=now_time,
                    extra_info=extra
                )
                break
