# strategy.py
import pandas as pd
import numpy as np
from datetime import datetime
import pytz # 시간대 계산을 위해 필요
from config import Config
from infra.utils import get_logger

class EmaStrategy:
    """
    [EMA Strategy - Production Version V2.0]
    백테스팅에서 검증된 '황금 비율' 로직 반영:
    1. 120분 타임컷 (Zombie Cut)
    2. 오전 10시(ET) 이후 신규 진입 금지
    """
    def __init__(self):
        self.name = "EMA_Dip_Rebound"
        self.logger = get_logger("Strategy")
        
        # [Config 및 최적화 파라미터]
        self.ma_length = getattr(Config, 'EMA_LENGTH', 10) 
        self.tp_pct = getattr(Config, 'TARGET_PROFIT_PCT', 0.10)      
        self.sl_pct = getattr(Config, 'STOP_LOSS_PCT', 0.40) 
        self.max_daily_change = 0.80 
        
        self.dip_tolerance = getattr(Config, 'DIP_TOLERANCE', 0.005)     
        self.hover_tolerance = getattr(Config, 'HOVER_TOLERANCE', 0.002) 

        # [백테스팅 검증된 황금 비율 설정값]
        # -------------------------------------------------------------
        self.max_holding_minutes = 120  # [수정] 90분보다 성적이 좋았던 120분 적용
        self.entry_end_hour = 10       # [수정] 미국 시간(ET) 기준 오전 10시 마감
        # -------------------------------------------------------------
        
        self.banned_tickers = set()

    def _get_current_et_time(self):
        """미국 동부 시간(ET) 현재 시각을 반환"""
        et_tz = pytz.timezone('US/Eastern')
        return datetime.now(et_tz)

    def check_buy_signal(self, df: pd.DataFrame, ticker: str = "Unknown") -> dict:
        """
        신규 매수 신호 포착
        """
        # [추가된 필터: 진입 시간 제한]
        # -------------------------------------------------------------
        now_et = self._get_current_et_time()
        # 미국 시간 기준 10시가 넘었으면 신규 진입을 즉시 차단합니다.
        if now_et.hour >= self.entry_end_hour:
            # 너무 자주 찍히지 않게 로깅은 생략하거나 디버그 모드에서만 사용
            return None 
        # -------------------------------------------------------------

        if len(df) < self.ma_length + 10:
            return None

        # 1. 과열 방지 필터
        if ticker in self.banned_tickers:
            return None 

        day_open = df['open'].iloc[0] 
        curr_high = df['high'].iloc[-1]
        
        if day_open > 0:
            daily_change = (curr_high - day_open) / day_open
            if daily_change >= self.max_daily_change:
                self.logger.warning(f"🚫 [Overheat Ban] {ticker} 급등({daily_change*100:.1f}%)으로 인한 진입 금지")
                self.banned_tickers.add(ticker)
                return None

        # 2. 폭락 방지 (Crash Protection)
        lookback = 5
        if len(df) > lookback:
            recent_candles = df.iloc[-lookback-1:-1]
            for _, row in recent_candles.iterrows():
                if row['open'] > 0:
                    change_pct = (row['close'] - row['open']) / row['open']
                    if change_pct <= -0.15: 
                        return None

        # 3. EMA 지표 계산 및 시그널 체크
        ema = df['close'].ewm(span=self.ma_length, adjust=False).mean()
        curr_row = df.iloc[-1]
        prev_row = df.iloc[-2]
        
        curr_price = curr_row['close']
        curr_ema = ema.iloc[-1]
        
        # 눌림목(Dip) 조건
        dip_threshold = ema.iloc[-2] * (1.0 + self.dip_tolerance)
        is_deep_enough = prev_row['low'] <= dip_threshold
        is_bearish_dip = prev_row['close'] < prev_row['open'] # 음봉 확인
        
        # 안착(Hover) 조건
        hover_threshold = curr_ema * (1.0 - self.hover_tolerance)
        is_hovering = curr_price >= hover_threshold
        
        if is_deep_enough and is_bearish_dip and is_hovering:
            return {
                'type': 'BUY',
                'strategy': self.name,
                'price': curr_price,
                'ticker': ticker, 
                'time': curr_row['time'],
                'reason': f"Bearish Dip & Hover (Time: {now_et.strftime('%H:%M')})"
            }
            
        return None
    
    def check_exit_signal(self, current_price, entry_price, entry_time=None):
        """
        [수정된 로직] 타임컷(120분) 기능을 실전 매매에 추가
        entry_time: 포지션 진입 시각 (datetime 객체여야 함)
        """
        if current_price <= 0 or entry_price <= 0:
            return None

        pnl_pct = (current_price - entry_price) / entry_price

        # -----------------------------------------------------------
        # 1. [신규 추가] 타임컷 (120분 좀비 제거)
        # -----------------------------------------------------------
        if entry_time is not None:
            # entry_time이 문자열인 경우를 대비한 변환 (실전용 안전장치)
            if isinstance(entry_time, str):
                entry_time = pd.to_datetime(entry_time)
            
            # 현재 시각과의 차이 계산 (분 단위)
            now_et = self._get_current_et_time()
            
            # entry_time에 시간대 정보가 없다면 ET로 간주하여 비교
            if entry_time.tzinfo is None:
                entry_time = pytz.timezone('US/Eastern').localize(entry_time)

            duration_mins = (now_et - entry_time).total_seconds() / 60

            if duration_mins >= self.max_holding_minutes:
                return {
                    'type': 'SELL',
                    'reason': f"TIME_CUT_STALE ({int(duration_mins)}min passed)"
                }

        # 2. [익절] Target Profit (10%)
        if pnl_pct >= self.tp_pct:
            return {
                'type': 'SELL',
                'reason': f"TAKE_PROFIT ({pnl_pct*100:.2f}%)"
            }

        # 3. [손절] Stop Loss (-40%)
        if pnl_pct <= -self.sl_pct:
            return {
                'type': 'SELL',
                'reason': f"STOP_LOSS ({pnl_pct*100:.2f}%)"
            }

        return None

# Factory 함수
def get_strategy():
    return EmaStrategy()