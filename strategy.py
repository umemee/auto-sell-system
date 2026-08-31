# strategy.py
import pandas as pd
import datetime
import pytz
import logging
import time
import os
import csv
from pathlib import Path
from config import Config
from infra.utils import get_logger

class EmaStrategy:
    """
    [EMA Deterministic Strategy V9.7 - B4 and Ban State Machine Integrated]
    - 백테스팅 ema_strategy.py와 100% 동기화된 결정론적 전략 엔진
    - 세션 상태머신(banned_tickers) 탑재: 지지선 붕괴 및 과열 종목 당일 재매수 영구 차단
    - B4 핀셋 필터: 윗꼬리 65.0% 상한, 고점 대비 최소 10.0% 눌림목 확인
    """
    def __init__(self):
        self.name = "EMA_Deterministic_V9"
        self.logger = get_logger("Strategy")
        
        # ------------------------------------------------------------------
        # [신규] 디버그 로거 설정 (1분 스로틀링용)
        # ------------------------------------------------------------------
        self.debug_logger = logging.getLogger("StrategyDebug")
        self.debug_logger.setLevel(logging.DEBUG)
        if not self.debug_logger.hasHandlers():
            log_dir = os.path.join(os.getcwd(), "logs")
            if not os.path.exists(log_dir): os.makedirs(log_dir)
            fh = logging.FileHandler(os.path.join(log_dir, "strategy_debug.log"), encoding='utf-8')
            fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
            self.debug_logger.addHandler(fh)
        
        # ------------------------------------------------------------------
        # 기존 설정값 로드
        # ------------------------------------------------------------------
        self.ma_length = getattr(Config, 'EMA_LENGTH', 400) 
        self.tp_pct = getattr(Config, 'TARGET_PROFIT_PCT', 0.07)
        self.sl_pct = getattr(Config, 'STOP_LOSS_PCT', 0.10)
        self.dip_tolerance = getattr(Config, 'DIP_TOLERANCE', 0.005)
        self.max_holding_minutes = getattr(Config, 'MAX_HOLDING_MINUTES', 0) # 0=무제한
        
        # [GapZone V3.0 New Configs]
        self.entry_end_hour = getattr(Config, 'ENTRY_DEADLINE_HOUR_ET', 10)
        self.entry_start_time_str = getattr(Config, 'ENTRY_START_TIME', "04:10")
        self.use_pause_window = getattr(Config, 'USE_PAUSE_WINDOW', True)
        self.pause_start_hour = getattr(Config, 'PAUSE_START_HOUR', 4)
        self.pause_end_hour = getattr(Config, 'PAUSE_END_HOUR', 9)
        self.upper_buffer = getattr(Config, 'UPPER_BUFFER', 0.015)
        self.activation_threshold = getattr(Config, 'ACTIVATION_THRESHOLD', 0.40)
        
        # [Emergency Fix] 과열 기준 (기본 500% = 5.0)
        self.max_daily_change = getattr(Config, 'MAX_DAILY_CHANGE', 5.0)

        # 🛡️ [F1 Crash Filter] 5분 급락 방어 필터 설정 로드
        self.chg_5m_crash_filter_enabled = getattr(Config, 'CHG_5M_CRASH_FILTER_ENABLED', True)
        self.chg_5m_crash_threshold = getattr(Config, 'CHG_5M_CRASH_THRESHOLD', -0.04)

        # ✅ [NEW] 하이브리드 필터 설정 로드
        self.gap_limit_global = getattr(Config, 'GAP_LIMIT_GLOBAL', 0.40)
        self.gap_limit_late = getattr(Config, 'GAP_LIMIT_LATE', 0.10)
        self.late_hour_start = getattr(Config, 'LATE_HOUR_START', 9)

        # 🛡️ [2026 Golden Spot] Upper Wick Filter 설정 로드
        self.upper_wick_filter_enabled = getattr(Config, 'UPPER_WICK_FILTER_ENABLED', True)
        self.upper_wick_filter_threshold_pct = getattr(Config, 'UPPER_WICK_FILTER_THRESHOLD_PCT', 65.0)
        self.upper_wick_filter_use_closed_candle_only = getattr(Config, 'UPPER_WICK_FILTER_USE_CLOSED_CANDLE_ONLY', True)
        
        # 🛡️ [2026 Golden Spot] 고점 대비 최소 눌림폭 필터 (Peak Drawdown Filter) 설정 로드
        self.enable_min_peak_drawdown_filter = getattr(Config, 'ENABLE_MIN_PEAK_DRAWDOWN_FILTER', True)
        self.min_peak_drawdown_pct = getattr(Config, 'MIN_PEAK_DRAWDOWN_PCT', 10.0)

        # ⚡ [세션 상태머신] 지지선 이탈 완충 버퍼 및 당일 영구 밴 세트 (백테스트 동기화)
        self.drop_slack = getattr(Config, 'SUPPORT_DROP_SLACK_PCT', 0.003)
        self.banned_tickers = set()

        # 윗꼬리 필터 전용 로그 폴더 생성
        self.upper_wick_skip_log_dir = Path(os.getcwd()) / "logs" / "live"
        self.upper_wick_skip_log_dir.mkdir(parents=True, exist_ok=True)

        # 상태 관리
        self.processed_candles = {}
        self.log_throttle_map = {} # 스로틀링 맵
    
    @staticmethod
    def calculate_upper_wick_pct(open_price, high_price, low_price, close_price):
        """윗꼬리 비율을 계산하는 함수"""
        candle_range = float(high_price) - float(low_price)
        if candle_range <= 0:
            return 0.0
        upper_wick = float(high_price) - max(float(open_price), float(close_price))
        if upper_wick <= 0:
            return 0.0
        return float(upper_wick / candle_range * 100.0)

    def _write_upper_wick_skip_log(self, decision_time, ticker, candle_time, candle_open, candle_high, candle_low, candle_close, upper_wick_pct, threshold_pct, action, reason):
        """윗꼬리 탈락 기록을 CSV 파일에 저장하는 함수"""
        log_date = decision_time.strftime("%Y%m%d")
        output_path = self.upper_wick_skip_log_dir / f"upper_wick_filter_skips_{log_date}.csv"
        file_exists = output_path.exists()
        
        fieldnames = ["date", "ticker", "decision_time_kst", "candle_time", "candle_open", "candle_high", "candle_low", "candle_close", "upper_wick_pct", "threshold_pct", "action", "reason", "strategy", "filter_name", "candle_role"]
        
        row = {
            "date": log_date, "ticker": ticker, "decision_time_kst": str(decision_time), "candle_time": str(candle_time),
            "candle_open": round(float(candle_open), 6), "candle_high": round(float(candle_high), 6), "candle_low": round(float(candle_low), 6), "candle_close": round(float(candle_close), 6),
            "upper_wick_pct": round(float(upper_wick_pct), 6), "threshold_pct": round(float(threshold_pct), 6),
            "action": action, "reason": reason, "strategy": "400EMA_baseline", "filter_name": "entry_candle_upper_wick_pct_high", "candle_role": "previous_closed_candle"
        }
        
        try:
            with open(output_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                if not file_exists:
                    writer.writeheader()
                writer.writerow(row)
        except Exception as e:
            self.logger.error(f"⚠️ [UpperWick] CSV 로깅 실패 ({ticker}): {e}")

    def _log_rejection(self, ticker, reason, current_price):
        """1분에 한 번만 거부 사유를 로그에 기록 (중복 방지)"""
        now = datetime.datetime.now()
        last_logged = self.log_throttle_map.get(ticker)
        
        if last_logged is None or (now - last_logged).total_seconds() >= 60:
            self.debug_logger.debug(f"📉 [REJECT] {ticker} | Price: ${current_price:.4f} | Reason: {reason}")
            self.log_throttle_map[ticker] = now
        
    def check_entry(self, ticker, df, now_time=None):
        """
        [진입 신호 확인 - GapZone 2026 Production Synchronized]
        1. 세션 상태머신 (banned_tickers 최우선 검사)
        2. 데이터 건전성 및 인덱스 정규화
        3. 분봉 1회 스냅샷 평가
        4. 지표 및 3중 필터(Upper Wick, Anti-Chasing, Activation, Peak-DD, Crash)
        5. 지지선 이탈(DROP) 및 눌림목 반등 판정
        """
        # ======================================================================
        # 0. 🚫 [세션 상태머신] 당일 밴 등록 종목 최우선 원천 차단
        # ======================================================================
        if ticker in self.banned_tickers:
            return None

        # ======================================================================
        # 🕵️‍♂️ [DEBUG] 데이터 건전성 정밀 검사 (Data Sanity Check)
        # ======================================================================
        data_count = len(df)
        if data_count > 0:
            start_time = df.index[0]
            end_time = df.index[-1]
            if data_count < self.ma_length + 50: 
                self.logger.warning(
                    f"⚠️ [DATA SHORTAGE] {ticker} 데이터 부족! "
                    f"Count: {data_count} (Require > {self.ma_length}) | "
                    f"Range: {start_time} ~ {end_time}"
                )
        else:
            self._log_rejection(ticker, "데이터 없음(Empty DataFrame)", 0.0)
            return None

        if len(df) < self.ma_length + 2:
            self._log_rejection(ticker, f"데이터 부족 ({len(df)} < {self.ma_length + 2})", df.iloc[-1].get('close', 0.0) if len(df)>0 else 0.0)
            return None

        # =========================================================
        # 🛠️ [CRITICAL FIX] 인덱스 보정 (Index Correction)
        # =========================================================
        if not isinstance(df.index, pd.DatetimeIndex):
            try:
                if 'date' in df.columns and 'time' in df.columns:
                    time_str = df['time'].astype(str).str.zfill(4)
                    datetime_str = df['date'].astype(str) + time_str
                    fmt = '%Y%m%d%H%M' if len(time_str.iloc[-1]) == 4 else '%Y%m%d%H%M%S'
                    df['datetime'] = pd.to_datetime(datetime_str, format=fmt, errors='coerce')
                    df.set_index('datetime', inplace=True)
                elif 'stck_bsop_date' in df.columns and 'stck_cntg_hour' in df.columns:
                    time_str = df['stck_cntg_hour'].astype(str).str.zfill(6)
                    datetime_str = df['stck_bsop_date'].astype(str) + time_str
                    df['datetime'] = pd.to_datetime(datetime_str, format='%Y%m%d%H%M%S', errors='coerce')
                    df.set_index('datetime', inplace=True)
            except Exception as e:
                self.logger.error(f"❌ [Strategy] 인덱스 변환 중 에러({ticker}): {e}")
                return None
            
        if df.index.tz is None:
            df.index = df.index.tz_localize('America/New_York')
        elif str(df.index.tz) != 'America/New_York':
            df.index = df.index.tz_convert('America/New_York')

        if not isinstance(df.index, pd.DatetimeIndex):
             self._log_rejection(ticker, "인덱스 변환 실패", df.iloc[-1]['close']) 
             return None

        # =========================================================
        # ✅ 진입 로직 시작
        # =========================================================
        if now_time is not None:
            current_time = now_time
            if current_time.tzinfo is None:
                current_time = pytz.timezone('America/New_York').localize(current_time)
            elif str(current_time.tzinfo) != 'America/New_York':
                current_time = current_time.astimezone(pytz.timezone('America/New_York'))
        else:
            current_time = datetime.datetime.now(pytz.timezone('America/New_York'))
        
        # 🛡️ [분봉 단위 1회 스냅샷 평가 강제 (Timing Sync)]
        latest_candle_time = df.index[-1]
        if self.processed_candles.get(ticker) == latest_candle_time:
            return None
        self.processed_candles[ticker] = latest_candle_time

        # 1. 시간 제한 체크
        hour = current_time.hour
        minute = current_time.minute
        entry_start_h, entry_start_m = map(int, self.entry_start_time_str.split(":"))

        if (hour < entry_start_h) or (hour == entry_start_h and minute < entry_start_m):
            return None

        # 🛑 [Pause Window] 특정 프리마켓 구간 진입 일시정지 (04:00:00 ~ 08:59:59 차단 - 백테스트 100% 동기화)
        if self.use_pause_window and (self.pause_start_hour <= hour < self.pause_end_hour):
            self._log_rejection(ticker, f"프리마켓 일시정지 대기 ({self.pause_start_hour}시~{self.pause_end_hour}시, 현재 {hour}:{minute:02d})", df.iloc[-1]['close'])
            return None

        if hour >= self.entry_end_hour:
            self._log_rejection(ticker, f"진입 마감 시간 초과 ({hour}시 >= {self.entry_end_hour}시)", df.iloc[-1]['close'])
            return None
            
        if 9 == hour and 30 <= minute <= 34:
            self._log_rejection(ticker, "장 초반 대기 (Market Open Wait)", df.iloc[-1]['close'])
            return None

        # 2. 현재 가격 가져오기
        current_price = df['close'].iloc[-1]
        if current_price <= 0:
            return None

        # 3. EMA 계산 (최근 1,200개 분봉 윈도우 동기화)
        df['ema'] = df['close'].ewm(span=self.ma_length, adjust=False).mean()

        # 4. 데이터 격리 (T-1 시점 기준: 직전 완성 캔들)
        prev_open = df['open'].iloc[-2]
        prev_high = df['high'].iloc[-2]
        prev_low = df['low'].iloc[-2]
        prev_close = df['close'].iloc[-2]
        prev_ema = df['ema'].iloc[-2]
        
        # =========================================================
        # 🛡️ [Step 4.1] Upper Wick Filter (직전 완성봉 윗꼬리 검사)
        # =========================================================
        if self.upper_wick_filter_enabled:
            upper_wick_pct = self.calculate_upper_wick_pct(
                prev_open, prev_high, prev_low, prev_close
            )
            
            if upper_wick_pct >= self.upper_wick_filter_threshold_pct:
                candle_time = df.index[-2]
                self._write_upper_wick_skip_log(
                    decision_time=current_time,
                    ticker=ticker,
                    candle_time=candle_time,
                    candle_open=prev_open,
                    candle_high=prev_high,
                    candle_low=prev_low,
                    candle_close=prev_close,
                    upper_wick_pct=upper_wick_pct,
                    threshold_pct=self.upper_wick_filter_threshold_pct,
                    action="SKIP",
                    reason="SKIP_UPPER_WICK_FILTER"
                )
                self._log_rejection(ticker, f"🚫 [UPPER_WICK] 윗꼬리 과다 차단 ({upper_wick_pct:.2f}% >= {self.upper_wick_filter_threshold_pct:.2f}%)", current_price)
                return None
        
        # =========================================================
        # 🛑 [Step 4.5] 추격 매수 방지 (Anti-Chasing Logic: Open > EMA + 3%)
        # =========================================================
        chasing_threshold = prev_ema * 1.03
        current_open = df['open'].iloc[-1]
        
        if current_open > chasing_threshold:
             self._log_rejection(ticker, f"🚀 [Anti-Chasing] 이평선 괴리 과다 (Open ${current_open:.4f} > EMA ${prev_ema:.4f} + 3%)", current_price)
             return None

        # =========================================================
        # 🔥 [Step 4.6] Activation & Peak-DD & 과열 종목 방지
        # =========================================================
        try:
            today_date = df.index[-1].normalize()
            past_data = df[df.index < today_date]
            
            if not past_data.empty:
                regular_session_past = past_data.between_time('04:00', '15:59')
                if not regular_session_past.empty:
                    ref_price = regular_session_past.iloc[-1]['close']
                else:
                    ref_price = past_data.iloc[-1]['close']
            else:
                today_candles = df[df.index >= today_date]
                ref_price = today_candles.iloc[0]['close'] if not today_candles.empty else 0

            if ref_price > 0:
                today_candles = df[df.index >= today_date]
                today_so_far = today_candles[today_candles.index < df.index[-1]]
                
                if not today_so_far.empty:
                    max_price_so_far = today_so_far['close'].max()
                else:
                    max_price_so_far = ref_price

                max_change_ratio = (max_price_so_far - ref_price) / ref_price
                
                # 🛡️ 1. [Activation] 40% 이상 상승 이력 없으면 진입 금지
                if max_change_ratio < self.activation_threshold:
                    self._log_rejection(
                        ticker,
                        f"🛡️ [ACTIVATION] 상승 이력 부족 ({max_change_ratio*100:.1f}% < {self.activation_threshold*100:.0f}%)",
                        current_price
                    )
                    return None

                # 🛡️ 1.1 [B4 Peak Drawdown] 당일 고점 대비 최소 10% 이상 정상 눌림목 확인
                if self.enable_min_peak_drawdown_filter and max_price_so_far > 0:
                    entry_price_for_filter = df['open'].iloc[-1]
                    peak_dd_pct = (max_price_so_far - entry_price_for_filter) / max_price_so_far * 100.0
                    if peak_dd_pct < self.min_peak_drawdown_pct:
                        self._log_rejection(
                            ticker,
                            f"🚫 [PEAK-DD] 고점 눌림 부족 ({peak_dd_pct:.1f}% < {self.min_peak_drawdown_pct:.1f}%)",
                            current_price
                        )
                        return None
                
                # 🛡️ 2. [Global Safety] 당일 과열 폭등 (500% 이상 시 영구 밴 등록)
                if max_change_ratio >= self.max_daily_change:
                    self.banned_tickers.add(ticker)
                    self._log_rejection(
                        ticker,
                        f"🛡️ [GAP_GLOBAL] 과열 폭등 ({max_change_ratio*100:.1f}% >= {self.max_daily_change*100:.0f}%)",
                        current_price
                    )
                    return {'type': 'DROP', 'reason': 'Overheated'}

        except Exception as e:
            self.logger.error(f"⚠️ [Check Entry] 과열/눌림목 체크 중 오류: {e}")

        # =========================================================
        # 🔥 [Step 4.7] 최근 10봉 내 3% 급등(모멘텀) 이력 확인
        # =========================================================
        recent_highs = df['high'].iloc[-11:-1]
        if not recent_highs.empty:
            recent_peak = recent_highs.max()
            if recent_peak < prev_ema * 1.03:
                self._log_rejection(ticker, f"모멘텀 부족 (최고점 {recent_peak:.2f} < EMA 3% {prev_ema*1.03:.2f})", current_price)
                return None
            
        # =========================================================
        # 🛡️ [Step 4.8] F1 Crash Filter (5분 급락 방어: -4.0% 이하 차단)
        # =========================================================
        if self.chg_5m_crash_filter_enabled:
            if len(df) >= 6:
                price_5m_ago = df['close'].iloc[-6]
                if price_5m_ago > 0:
                    chg_5m = (current_price - price_5m_ago) / price_5m_ago
                    if chg_5m <= self.chg_5m_crash_threshold:
                        self._log_rejection(
                            ticker,
                            f"🚫 [F1-CRASH] 5분 급락 차단 (chg_5m={chg_5m*100:.2f}% <= {self.chg_5m_crash_threshold*100:.1f}%)",
                            current_price
                        )
                        return None

        # =========================================================
        # 5. 지지선 및 눌림목 조건 검사 (백테스트 ema_strategy.py 100% Parity)
        # =========================================================
        lower_bound = prev_ema * (1.0 - self.dip_tolerance)
        upper_bound = prev_ema * (1.0 + self.upper_buffer)
        drop_cutoff = lower_bound * (1.0 - self.drop_slack)

        # 🛑 [Step 5.1] 지지선 하방 이탈 및 추세 붕괴(DROP) 확정 시 영구 밴 등록
        if prev_low < drop_cutoff or prev_close < drop_cutoff:
            self.banned_tickers.add(ticker)
            self._log_rejection(ticker, f"지지선 이탈 (Low {prev_low:.4f} < Bound {lower_bound:.4f})", current_price)
            self.debug_logger.debug(f"🗑️ [DROP] {ticker} 추세 붕괴 -> 당일 영구 밴 등록")
            self.logger.warning(f"🚫 [DROP-PERMANENT] {ticker} 지지선 붕괴(Low {prev_low:.4f} < Cutoff {drop_cutoff:.4f})로 당일 진입 영구 차단")
            return {'type': 'DROP', 'reason': 'Trend Broken'}

        # 🛑 [Step 5.2] 눌림목 범위 벗어남 (Low > UpperBound)
        if not (lower_bound <= prev_low <= upper_bound):
            self._log_rejection(ticker, f"눌림목 범위 벗어남 (Low {prev_low:.4f} > Upper {upper_bound:.4f})", current_price)
            return None

        # 🛑 [Step 5.3] 지지선 위 종가 마감 실패 (Close <= EMA)
        if prev_close <= prev_ema:
            self._log_rejection(ticker, f"지지선 위 종가 마감 실패 (Close {prev_close:.4f} <= EMA {prev_ema:.4f})", current_price)
            return None

        # ⚡ [BUY SIGNAL] 모든 조건 만족 시 매수 신호 생성
        self.logger.info(f"⚡ [BUY SIGNAL] {ticker} 조건 만족! (Data: {data_count} bars, EMA: ${prev_ema:.4f}, Price: ${df.iloc[-1]['open']:.4f})")
        return {
            'type': 'BUY',
            'ticker': ticker,
            'price': df.iloc[-1]['open'],
            'time': datetime.datetime.now()
        }

    def daily_reset(self):
        """매일 장 시작 전 세션 상태 초기화"""
        self.banned_tickers.clear()
        self.processed_candles.clear()
        self.log_throttle_map.clear()
        self.logger.info("🔄 [Strategy] 일별 세션 상태(banned_tickers 등) 초기화 완료")

    def check_exit(self, ticker, position, current_price, now_time):
        """청산 로직 (고정 익절/손절/타임컷)"""
        entry_price = position['entry_price']
        pnl_pct = (current_price - entry_price) / entry_price
        
        # 1. 🎯 고정 익절 (Fixed Take Profit)
        if pnl_pct >= abs(self.tp_pct):
            return {'type': 'SELL', 'reason': 'TAKE_PROFIT'}
        
        # 2. 고정 손절 (Stop Loss)
        if pnl_pct <= -abs(self.sl_pct):
            return {'type': 'SELL', 'reason': 'STOP_LOSS'}
            
        # 3. 🔴 타임 컷 (Time Cut)
        if 'entry_time' in position and position['entry_time']:
            entry_time = position['entry_time']
            if entry_time.tzinfo is None:
                 entry_time = pytz.timezone('US/Eastern').localize(entry_time)
            
            elapsed_minutes = (now_time - entry_time).total_seconds() / 60
            if self.max_holding_minutes > 0 and elapsed_minutes >= self.max_holding_minutes:
                return {'type': 'SELL', 'reason': 'TIME_CUT'}
                
        return None
    
# Factory 함수 (필수 연동)
def get_strategy():
    return EmaStrategy()
