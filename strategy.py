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
        - 데이터 격리 (Daily Isolation): 당일 데이터만으로 활성화 판단
        - 천장 확인 (Upper Bound): 이평선보다 너무 높은 가격 추격 매수 방지
        - 시간 제한: 04:10 ~ 13:00 사이만 진입
        """
        # 데이터 개수 확인
        if len(df) < self.ma_length + 2:
            return None 

        current_time = df.index[-1]

        # 1. 중복 진입 방지 (이미 매수 신호 보낸 캔들이면 패스)
        last_processed_time = self.processed_candles.get(ticker)
        if last_processed_time == current_time:
            return None

        # 2. 시간 제한 체크 (04:10 ~ 13:00)
        # 문자열 "04:10"을 시/분으로 분리
        start_h, start_m = map(int, self.entry_start_time_str.split(':'))
        
        # 장 초반(노이즈 구간) 대기
        if (current_time.hour < start_h) or \
           (current_time.hour == start_h and current_time.minute < start_m):
            return None 

        # 13시 이후 진입 금지 (오후장 리스크 관리)
        if current_time.hour >= self.entry_end_hour:
            return None 

        # 3. 지표 계산 (MA)
        # 전체 데이터에 대해 계산하지만, 판단은 T-1 기준으로 수행
        df['ema'] = df['close'].ewm(span=self.ma_length, adjust=False).mean()

        # 4. 데이터 격리 (T-1 시점 기준 판단)
        # ⚠️ 현재 봉(iloc[-1])은 형성 중이므로 절대 사용 금지 -> 직전 봉(iloc[-2]) 사용
        prev_close = df['close'].iloc[-2]
        prev_low = df['low'].iloc[-2]
        prev_ema = df['ema'].iloc[-2]
        
        # 5. Daily Isolation (오늘 데이터만 분리하여 고가 계산)
        # 어제 데이터 오염 방지: '오늘 장 시작' ~ '직전 봉(T-1)'까지의 고가만 사용
        today_date = current_time.date()
        df_today = df[df.index.date == today_date]
        
        # 오늘 데이터가 충분하지 않으면 패스
        if df_today.empty or len(df_today) < 2: 
            return None

        day_open = df_today['open'].iloc[0]
        # 현재 봉(마지막 봉)을 제외한 오늘 고가 (iloc[:-1].max())
        day_high = df_today['high'].iloc[:-1].max()

        if day_open == 0: return None
        activation_ratio = (day_high - day_open) / day_open

        # ==========================================
        # 🎯 진입 조건 검사 (3단계 필터)
        # ==========================================

        # (A) 과열 방지 (Overheat Breaker)
        # 당일 80% 이상 폭등한 종목은 설거지 위험 -> 진입 금지
        if activation_ratio >= self.max_daily_change:
            return None 

        # (B) 활성화 확인 (Activation Check)
        # 당일 최소 40% 이상 상승한 이력이 있어야 함 (주도주 확인)
        if activation_ratio < self.activation_threshold:
            return None

        # (C) 눌림목(Dip) & 천장(Upper Bound) 확인 - 핵심 로직!
        # 하한선: EMA - 0.5% (지지선)
        lower_bound = prev_ema * (1 - self.dip_tolerance)
        # 상한선: EMA + 2.0% (천장 - 이보다 높으면 추격 매수)
        upper_bound = prev_ema * (1 + self.upper_buffer) 

        is_supported = (prev_low >= lower_bound)      # 지지선 침범 안 함 (너무 깊게 안 빠짐)
        is_close_enough = (prev_low <= upper_bound)   # 천장 아래에 있음 (이평선에 충분히 근접)
        is_above_ema = (prev_close > prev_ema)        # 종가는 이평선 위에 안착 (지지 성공)

        # ✅ 매수 신호 발생
        if is_supported and is_close_enough and is_above_ema:
            # 처리 완료 기록 업데이트
            self.processed_candles[ticker] = current_time
            
            # (로그는 실전 봇의 로거 설정에 따라 출력됨)
            # self.logger.info(f"⚡ [BUY] {ticker} | Active: {activation_ratio:.1%} | GapZone Hit")
            
            return {
                'type': 'BUY',
                'ticker': ticker,
                'price': df.iloc[-1]['open'], # 현재 봉의 시가로 진입 시도
                'time': datetime.datetime.now()
            }
        
        # 🗑️ [Drop 조건] 추세 붕괴 감지 (좀비 감시 해제)
        # 종가가 이평선보다 2% 이상 아래로 깨지면 상승 추세 끝난 것으로 간주
        if prev_close < prev_ema * 0.98:
             return {'type': 'DROP', 'reason': 'Trend Broken (Close < EMA -2%)'}

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