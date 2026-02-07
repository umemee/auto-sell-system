# strategy.py
import pandas as pd
import datetime
import pytz
import logging
import time
import os
from config import Config
from infra.utils import get_logger

class EmaStrategy:
    """
    [EMA Deterministic Strategy V9.5 - Full Logic + Debug Logging]
    - 원본 기능 100% 유지 (인덱스 보정, 일봉 격리, GapZone 로직)
    - 디버깅 기능 추가: 진입 실패 사유 정밀 기록
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
        self.ma_length = getattr(Config, 'EMA_LENGTH', 200) 
        self.tp_pct = getattr(Config, 'TARGET_PROFIT_PCT', 0.12)
        self.sl_pct = getattr(Config, 'STOP_LOSS_PCT', 0.40)
        self.dip_tolerance = getattr(Config, 'DIP_TOLERANCE', 0.005)
        self.max_holding_minutes = getattr(Config, 'MAX_HOLDING_MINUTES', 0) # 0=무제한
        
        # [GapZone V3.0 New Configs]
        self.entry_end_hour = getattr(Config, 'ENTRY_DEADLINE_HOUR_ET', 10)
        self.entry_start_time_str = getattr(Config, 'ENTRY_START_TIME', "04:10")
        self.upper_buffer = getattr(Config, 'UPPER_BUFFER', 0.02)
        self.activation_threshold = getattr(Config, 'ACTIVATION_THRESHOLD', 0.40)
        self.max_daily_change = getattr(Config, 'MAX_DAILY_CHANGE', 1.5)

        # 상태 관리
        self.processed_candles = {}
        self.log_throttle_map = {} # 스로틀링 맵

    def _log_rejection(self, ticker, reason, price=0):
        """[내부 함수] 거절 사유를 1분에 한 번만 기록"""
        now = time.time()
        last_log = self.log_throttle_map.get(ticker, 0)
        if now - last_log > 60:
            self.debug_logger.debug(f"📉 [REJECT] {ticker} | Price: ${price} | Reason: {reason}")
            self.log_throttle_map[ticker] = now
        
    def check_entry(self, ticker, df):
        """
        [진입 신호 확인 - GapZone V3.0 Logic Injection]
        - [Fix] 데이터프레임 인덱스 자동 보정 기능 추가
        """
        # 데이터 개수 확인
        if len(df) < self.ma_length + 2:
            self._log_rejection(ticker, f"데이터 부족 (Len {len(df)} < {self.ma_length+2})")
            return None 

        # =========================================================
        # 🛠️ [CRITICAL FIX] 인덱스 보정 (Index Correction)
        # =========================================================
        # 인덱스가 날짜형식(DatetimeIndex)이 아니면(즉, 0,1,2 숫자라면) 변환 수행
        if not isinstance(df.index, pd.DatetimeIndex):
            try:
                # Case 1: 'date'와 'time' 컬럼이 존재 (가장 일반적)
                if 'date' in df.columns and 'time' in df.columns:
                    time_str = df['time'].astype(str).str.zfill(4)
                    datetime_str = df['date'].astype(str) + time_str
                    fmt = '%Y%m%d%H%M' if len(time_str.iloc[-1]) == 4 else '%Y%m%d%H%M%S'
                    df['datetime'] = pd.to_datetime(datetime_str, format=fmt, errors='coerce')
                    df.set_index('datetime', inplace=True)
                
                # Case 2: 'stck_bsop_date' 등 한투 API 원본 컬럼
                elif 'stck_bsop_date' in df.columns and 'stck_cntg_hour' in df.columns:
                    time_str = df['stck_cntg_hour'].astype(str).str.zfill(6)
                    datetime_str = df['stck_bsop_date'].astype(str) + time_str
                    df['datetime'] = pd.to_datetime(datetime_str, format='%Y%m%d%H%M%S', errors='coerce')
                    df.set_index('datetime', inplace=True)

            except Exception as e:
                self.logger.error(f"❌ [Strategy] 인덱스 변환 중 에러({ticker}): {e}")
                self._log_rejection(ticker, f"인덱스 변환 에러: {e}")
                return None

        # 변환 후에도 인덱스가 시간이 아니면 포기
        if not isinstance(df.index, pd.DatetimeIndex):
             self._log_rejection(ticker, "인덱스 변환 실패(Not DatetimeIndex)") 
             return None

        # =========================================================
        # ✅ 이하 기존 V3.0 로직 동일
        # =========================================================
        current_time = df.index[-1]
        current_price = df['close'].iloc[-1] # For logging

        # 1. 중복 진입 방지
        last_processed_time = self.processed_candles.get(ticker)
        if last_processed_time == current_time:
            return None

        # 2. 시간 제한 체크 (04:10 ~ 13:00)
        start_h, start_m = map(int, self.entry_start_time_str.split(':'))
        
        if (current_time.hour < start_h) or \
           (current_time.hour == start_h and current_time.minute < start_m):
            self._log_rejection(ticker, f"시간 미달 ({current_time.strftime('%H:%M')} < {self.entry_start_time_str})", current_price)
            return None 

        if current_time.hour >= self.entry_end_hour:
            self._log_rejection(ticker, f"시간 초과 ({current_time.strftime('%H:%M')} >= {self.entry_end_hour}:00)", current_price)
            return None 

        # 3. 지표 계산
        df['ema'] = df['close'].ewm(span=self.ma_length, adjust=False).mean()

        # 4. 데이터 격리 (T-1 시점 기준)
        prev_close = df['close'].iloc[-2]
        prev_low = df['low'].iloc[-2]
        prev_ema = df['ema'].iloc[-2]
        
        # 5. Daily Isolation
        today_date = current_time.date()
        df_today = df[df.index.date == today_date]
        
        if df_today.empty or len(df_today) < 2: 
            self._log_rejection(ticker, "당일 데이터 부족", current_price)
            return None

        current_time = df.index[-1]
        today_date = current_time.date()
    
        # 전체 데이터에서 "오늘 이전 날짜"의 데이터만 추출
        prev_data = df[df.index.date < today_date]
    
        if prev_data.empty:
            # 전일 데이터가 없으면(신규 상장 등) 어쩔 수 없이 당일 시가 사용
            ref_price = df[df.index.date == today_date]['open'].iloc[0]
        else:
            # 전일 데이터의 마지막 종가를 기준가로 설정
            ref_price = prev_data['close'].iloc[-1]

        # 당일 고가 (현재 봉 제외)
        day_high = df_today['high'].iloc[:-1].max()

        if ref_price == 0: 
            self._log_rejection(ticker, "기준가(ref_price) 0", current_price)
            return None
        
        # [핵심 변경] 시가(day_open)가 아닌 '전일 종가(ref_price)' 대비 상승률 계산
        activation_ratio = (day_high - ref_price) / ref_price

        # 6. 진입 조건 검사
        if activation_ratio >= self.max_daily_change: 
            self._log_rejection(ticker, f"일간 등락폭 과다({activation_ratio*100:.1f}% >= {self.max_daily_change*100}%)", current_price)
            return None 
            
        if activation_ratio < self.activation_threshold: 
            self._log_rejection(ticker, f"변동성 부족({activation_ratio*100:.1f}% < {self.activation_threshold*100}%)", current_price)
            return None

        lower_bound = prev_ema * (1 - self.dip_tolerance)
        upper_bound = prev_ema * (1 + self.upper_buffer) 

        is_supported = (prev_low >= lower_bound)      
        is_close_enough = (prev_low <= upper_bound)   
        is_above_ema = (prev_close > prev_ema)       

        if is_supported and is_close_enough and is_above_ema:
            self.processed_candles[ticker] = current_time
            self.logger.info(f"⚡ [BUY SIGNAL] {ticker} 조건 만족! 진입 시도.")
            return {
                'type': 'BUY',
                'ticker': ticker,
                'price': df.iloc[-1]['open'], 
                'time': datetime.datetime.now()
            }
        
        # 조건 불만족 시 상세 로그 (이유 분석용)
        if not is_supported:
            self._log_rejection(ticker, f"지지선 이탈 (Low {prev_low} < Bound {lower_bound:.2f})", current_price)
        elif not is_close_enough:
            self._log_rejection(ticker, f"눌림목 범위 벗어남 (Low {prev_low} > Upper {upper_bound:.2f})", current_price)
        elif not is_above_ema:
             self._log_rejection(ticker, f"EMA 하향 이탈 (Close {prev_close} <= EMA {prev_ema:.2f})", current_price)
        
        if prev_close < prev_ema * 0.98:
             self.debug_logger.debug(f"🗑️ [DROP] {ticker} 추세 붕괴")
             return {'type': 'DROP', 'reason': 'Trend Broken'}

        return None

    def check_exit(self, ticker, position, current_price, now_time):
        """청산 로직 (익절/손절/타임컷)"""
        entry_price = position['entry_price']
        pnl_pct = (current_price - entry_price) / entry_price
        
        # 1. 익절 (Take Profit)
        if pnl_pct >= self.tp_pct:
            return {'type': 'SELL', 'reason': 'TAKE_PROFIT'}
        
        # 2. 손절 (Stop Loss)
        if pnl_pct <= -self.sl_pct:
            return {'type': 'SELL', 'reason': 'STOP_LOSS'}
            
        # 3. 🔴 [추가] 타임 컷 (Time Cut)
        if 'entry_time' in position and position['entry_time']:
            entry_time = position['entry_time']
            # Timezone 처리
            if entry_time.tzinfo is None:
                 entry_time = pytz.timezone('US/Eastern').localize(entry_time)
            
            # 경과 시간(분) 계산
            elapsed_minutes = (now_time - entry_time).total_seconds() / 60
            
            # [V3.0 Fix] 설정값이 0보다 클 때만 타임컷 작동 (0이면 무제한)
            if self.max_holding_minutes > 0 and elapsed_minutes >= self.max_holding_minutes:
                return {'type': 'SELL', 'reason': 'TIME_CUT'}
                
        return None
    
# Factory 함수 (필수 연동)
def get_strategy():
    return EmaStrategy()