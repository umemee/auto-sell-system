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
    [EMA Deterministic Strategy V9.6 - Emergency Patch]
    - 원본 기능 100% 유지
    - [FIX] 과열 종목(Overheat) 진입 방지 로직 추가 (매뉴얼 F-01 준수)
    - 디버깅 기능 포함
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
        
        # [Emergency Fix] 과열 기준 (기본 150% = 1.5, OBAI 방어용)
        # Config에 없으면 3.0(300%)을 기본값으로 하여 안전장치 마련
        self.max_daily_change = getattr(Config, 'MAX_DAILY_CHANGE', 3.0)

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
        [진입 신호 확인 - GapZone V3.0 Final Logic + Emergency Fix]
        - 데이터 건전성 체크
        - 인덱스 보정
        - 시간 제한 체크
        - [NEW] 과열(Overheat) 체크 추가
        """
        # ======================================================================
        # 🕵️‍♂️ [DEBUG] 데이터 건전성 정밀 검사 (Data Sanity Check)
        # ======================================================================
        data_count = len(df)
        if data_count > 0:
            start_time = df.index[0]  # 데이터 시작 시간
            end_time = df.index[-1]   # 데이터 끝 시간
            
            # EMA 계산을 위해 최소한 ma_length(200)보다 넉넉한 데이터가 있는지 확인
            if data_count < self.ma_length + 200: 
                self.logger.warning(
                    f"⚠️ [DATA SHORTAGE] {ticker} 데이터 부족! "
                    f"Count: {data_count} (Require > {self.ma_length}) | "
                    f"Range: {start_time} ~ {end_time}"
                )
        else:
            self._log_rejection(ticker, "데이터 없음(Empty DataFrame)")
            return None

        # 데이터 개수 절대 부족 시 리턴
        if len(df) < self.ma_length + 2:
            return None 

        # =========================================================
        # 🛠️ [CRITICAL FIX] 인덱스 보정 (Index Correction)
        # =========================================================
        if not isinstance(df.index, pd.DatetimeIndex):
            try:
                # Case 1: 'date'와 'time' 컬럼 존재
                if 'date' in df.columns and 'time' in df.columns:
                    time_str = df['time'].astype(str).str.zfill(4)
                    datetime_str = df['date'].astype(str) + time_str
                    fmt = '%Y%m%d%H%M' if len(time_str.iloc[-1]) == 4 else '%Y%m%d%H%M%S'
                    df['datetime'] = pd.to_datetime(datetime_str, format=fmt, errors='coerce')
                    df.set_index('datetime', inplace=True)
                
                # Case 2: 한투 API 원본 컬럼
                elif 'stck_bsop_date' in df.columns and 'stck_cntg_hour' in df.columns:
                    time_str = df['stck_cntg_hour'].astype(str).str.zfill(6)
                    datetime_str = df['stck_bsop_date'].astype(str) + time_str
                    df['datetime'] = pd.to_datetime(datetime_str, format='%Y%m%d%H%M%S', errors='coerce')
                    df.set_index('datetime', inplace=True)

            except Exception as e:
                self.logger.error(f"❌ [Strategy] 인덱스 변환 중 에러({ticker}): {e}")
                return None
            
        # Timezone 처리
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC').tz_convert('America/New_York')
        elif str(df.index.tz) != 'America/New_York':
            df.index = df.index.tz_convert('America/New_York')

        if not isinstance(df.index, pd.DatetimeIndex):
             self._log_rejection(ticker, "인덱스 변환 실패") 
             return None

        # =========================================================
        # ✅ 진입 로직 시작
        # =========================================================
        current_time = datetime.datetime.now(pytz.timezone('America/New_York'))
        current_price = df['close'].iloc[-1]

        # 1. 중복 진입 방지
        last_processed_time = self.processed_candles.get(ticker)
        if last_processed_time == current_time:
            return None

        # 2. 시간 제한 체크
        # (1) 진입 시작 시간 체크
        start_h, start_m = map(int, self.entry_start_time_str.split(':'))
        if (current_time.hour < start_h) or \
           (current_time.hour == start_h and current_time.minute < start_m):
            self._log_rejection(ticker, f"시간 미달 ({current_time.strftime('%H:%M')} < {self.entry_start_time_str})", current_price)
            return None 

        # (2) 진입 마감 시간 체크
        if current_time.hour >= self.entry_end_hour:
            self._log_rejection(ticker, f"시간 초과 ({current_time.strftime('%H:%M')} >= {self.entry_end_hour}:00)", current_price)
            return None 

        # 🛡️ [New Rule] 장 시작 후 5분간 진입 금지 (Market Open Filter)
        # 미국 시간 09:30 ~ 09:35 (한국 23:30 ~ 23:35) 노이즈 및 API 오류 회피
        if current_time.hour == 9 and current_time.minute < 35:
             # 로그를 남기고 싶으면 주석 해제
             # self._log_rejection(ticker, "장 초반 대기 (Market Open Wait)", current_price)
             return None

        # 3. 지표 계산 (EMA)
        df['ema'] = df['close'].ewm(span=self.ma_length, adjust=False).mean()

        # 4. 데이터 격리 (T-1 시점 기준)
        prev_close = df['close'].iloc[-2]
        prev_low = df['low'].iloc[-2]
        prev_ema = df['ema'].iloc[-2]
        
        # =========================================================
        # 🛑 [Step 4.5] 추격 매수 방지 (Anti-Chasing Logic)
        # =========================================================
        chasing_threshold = prev_ema * 1.05 
        current_open = df['open'].iloc[-1]
        
        if current_open > chasing_threshold:
             self._log_rejection(ticker, f"🚀 [Anti-Chasing] 이평선 괴리 과다 (Open ${current_open} > EMA ${prev_ema:.2f} + 5%)", current_price)
             return None

        # =========================================================
        # 🔥 [Step 4.6] 과열 종목 방지 (Overheat Protection) - EMERGENCY ADD
        # =========================================================
        # 당일 시가(Day Open) 찾기: 현재 날짜와 같은 날짜의 첫 봉
        try:
            today_date = df.index[-1].normalize() # 시간 제거, 날짜만
            today_candles = df[df.index >= today_date]
            
            if len(today_candles) > 0:
                day_open = today_candles['open'].iloc[0]
                if day_open > 0:
                    daily_change_pct = (current_price - day_open) / day_open
                    
                    if daily_change_pct > self.max_daily_change:
                        self._log_rejection(
                            ticker, 
                            f"🔥 [OVERHEAT] 당일 등락률 초과 ({daily_change_pct*100:.1f}% > {self.max_daily_change*100:.0f}%)", 
                            current_price
                        )
                        return None
        except Exception as e:
            self.logger.error(f"⚠️ [Check Entry] 과열 체크 중 오류: {e}")
            # 에러 발생 시 안전을 위해 통과시키거나 보수적으로 차단 (여기선 로그만 남김)

        # 5. 진입 조건 검사
        lower_bound = prev_ema * (1 - self.dip_tolerance)
        upper_bound = prev_ema * (1 + self.upper_buffer) 

        is_supported = (prev_low >= lower_bound)      
        is_close_enough = (prev_low <= upper_bound)   
        is_above_ema = (prev_close > prev_ema)       

        if is_supported and is_close_enough and is_above_ema:
            self.processed_candles[ticker] = current_time
            
            self.logger.info(f"⚡ [BUY SIGNAL] {ticker} 조건 만족! (Data: {data_count} bars)")
            
            return {
                'type': 'BUY',
                'ticker': ticker,
                'price': df.iloc[-1]['open'], 
                'time': datetime.datetime.now()
            }
        
        # 조건 불만족 시 상세 로그
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