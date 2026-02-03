# strategy.py
import pandas as pd
import datetime
import pytz
from config import Config
from infra.utils import get_logger

class EmaStrategy:
    """
    [EMA Deterministic Strategy V9.2]
    - 업데이트: 'DROP' 신호 추가 (좀비 감시 방지)
    - 차트 훼손 시 즉시 감시 해제 요청
    """
    def __init__(self):
        self.name = "EMA_Deterministic_V9"
        self.logger = get_logger("Strategy")
        
        # 설정값 로드
        self.ma_length = getattr(Config, 'EMA_LENGTH', 20) 
        self.tp_pct = getattr(Config, 'TARGET_PROFIT_PCT', 0.12)
        self.sl_pct = getattr(Config, 'STOP_LOSS_PCT', 0.40)
        self.dip_tolerance = getattr(Config, 'DIP_TOLERANCE', 0.005)
        # 타임 컷 설정값 로드
        self.max_holding_minutes = getattr(Config, 'MAX_HOLDING_MINUTES', 0) # 0=무제한
        
        # [GapZone V3.0 New Configs]
        self.entry_end_hour = getattr(Config, 'ENTRY_DEADLINE_HOUR_ET', 13)
        self.entry_start_time_str = getattr(Config, 'ENTRY_START_TIME', "04:10")
        self.upper_buffer = getattr(Config, 'UPPER_BUFFER', 0.02)
        self.activation_threshold = getattr(Config, 'ACTIVATION_THRESHOLD', 0.40)
        self.max_daily_change = getattr(Config, 'MAX_DAILY_CHANGE', 0.80)

        # 중복 진입 방지용 (마지막으로 신호 보낸 캔들 시간 저장)
        self.processed_candles = {}
        
    def check_entry(self, ticker, df):
        """
        [진입 신호 확인 - GapZone V3.0 Logic Injection]
        - [Fix] 데이터프레임 인덱스 자동 보정 기능 추가
        """
        # 데이터 개수 확인
        if len(df) < self.ma_length + 2:
            return None 

        # =========================================================
        # 🛠️ [CRITICAL FIX] 인덱스 보정 (Index Correction)
        # =========================================================
        # 인덱스가 날짜형식(DatetimeIndex)이 아니면(즉, 0,1,2 숫자라면) 변환 수행
        if not isinstance(df.index, pd.DatetimeIndex):
            try:
                # Case 1: 'date'와 'time' 컬럼이 존재 (가장 일반적)
                if 'date' in df.columns and 'time' in df.columns:
                    # time 컬럼을 문자열로 변환하고 자리수 맞춤 (HHMMSS or HHMM)
                    time_str = df['time'].astype(str).str.zfill(4)
                    
                    # 날짜 + 시간 문자열 합치기
                    # 예: 20260203 + 093000
                    datetime_str = df['date'].astype(str) + time_str
                    
                    # 포맷 자동 감지 (4자리는 HHMM, 6자리는 HHMMSS)
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
                return None

        # 변환 후에도 인덱스가 시간이 아니면 포기
        if not isinstance(df.index, pd.DatetimeIndex):
             # self.logger.error(f"❌ [Strategy] {ticker} 인덱스 변환 실패") 
             return None

        # =========================================================
        # ✅ 이하 기존 V3.0 로직 동일
        # =========================================================
        current_time = df.index[-1]

        # 1. 중복 진입 방지
        last_processed_time = self.processed_candles.get(ticker)
        if last_processed_time == current_time:
            return None

        # 2. 시간 제한 체크 (04:10 ~ 13:00)
        start_h, start_m = map(int, self.entry_start_time_str.split(':'))
        
        if (current_time.hour < start_h) or \
           (current_time.hour == start_h and current_time.minute < start_m):
            return None 

        if current_time.hour >= self.entry_end_hour:
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
            return None

        day_open = df_today['open'].iloc[0]
        day_high = df_today['high'].iloc[:-1].max()

        if day_open == 0: return None
        activation_ratio = (day_high - day_open) / day_open

        # 6. 진입 조건 검사
        if activation_ratio >= self.max_daily_change: return None 
        if activation_ratio < self.activation_threshold: return None

        lower_bound = prev_ema * (1 - self.dip_tolerance)
        upper_bound = prev_ema * (1 + self.upper_buffer) 

        is_supported = (prev_low >= lower_bound)      
        is_close_enough = (prev_low <= upper_bound)   
        is_above_ema = (prev_close > prev_ema)       

        if is_supported and is_close_enough and is_above_ema:
            self.processed_candles[ticker] = current_time
            return {
                'type': 'BUY',
                'ticker': ticker,
                'price': df.iloc[-1]['open'], 
                'time': datetime.datetime.now()
            }
        
        if prev_close < prev_ema * 0.98:
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
    
# Factory 함수
def get_strategy():
    return EmaStrategy()