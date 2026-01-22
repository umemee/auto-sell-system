# strategy.py
import pandas as pd
import numpy as np
from config import Config
from infra.utils import get_logger

class EmaStrategy:
    """
    [EMA Strategy - Production Version]
    백테스팅 'EmaStrategy'의 로직을 실전용으로 포팅.
    """
    def __init__(self):
        self.name = "EMA_Dip_Rebound"
        self.logger = get_logger("Strategy")
        
        # [Config에서 최적화된 파라미터 로드]
        # 최적화가 끝나면 Config.py에 이 값들을 업데이트해야 함
        self.ma_length = getattr(Config, 'EMA_LENGTH', 10) 
        self.tp_pct = getattr(Config, 'TARGET_PROFIT_PCT', 0.10)      # 익절 10%
        self.sl_pct = getattr(Config, 'STOP_LOSS_PCT', 0.40) # Config 변수명 변경 반영
        self.max_daily_change = 0.80 # 80% (전일 종가 대비 80% 상승 시 진입 금지)
        # [추가] Hover Tolerance (반등 인정 범위)
        # 0.002 (0.2%) -> EMA보다 0.2% 낮아도 매수 인정
        self.dip_tolerance = getattr(Config, 'DIP_TOLERANCE', 0.005)     # Config 연결
        self.hover_tolerance = getattr(Config, 'HOVER_TOLERANCE', 0.002) # Config 연결
        # 금일 과열로 인해 영구 퇴출된 종목을 기록할 집합 (메모리 캐싱)
        self.banned_tickers = set()

    def check_buy_signal(self, df: pd.DataFrame, ticker: str = "Unknown") -> dict:
        """
        [수정된 로직]
        1. 80% 과열 종목 필터링 (Overheating Filter)
        2. 0.5% 오차 범위 내 눌림목 인정 (Flexible Dip)
        """
        # [안전 장치] 데이터 부족 시 패스
        if len(df) < self.ma_length + 10:
            return None

        # -----------------------------------------------------------
        # [NEW Logic 1] 80% 과열 방지 (Overheating Filter)
        # -----------------------------------------------------------
        if ticker in self.banned_tickers:
            return None # 이미 밴 당한 종목은 연산조차 하지 않음

        # 전일 종가 계산 (데이터프레임 날짜 변경선 기준)
        # 실전 데이터프레임에는 'date' 컬럼이 있거나, 날짜가 바뀌는 지점을 찾아야 함.
        # 가장 간단하게는: 오늘의 시가(Open)를 전일 종가 대용으로 쓰거나(갭상승 포함), 
        # 혹은 API에서 별도로 전일 종가를 받아와야 하지만, 
        # 여기서는 df 상의 '당일 시초가' 근처 가격을 기준으로 약식 계산합니다.
        
        # (더 정확한 방법) df의 첫 번째 데이터가 당일 장 시작이라면 df.iloc[0]['open'] 사용
        # 당일 고점 확인
        curr_high = df['high'].iloc[-1]
        day_open = df['open'].iloc[0] # 데이터프레임의 시작이 장 시작이라고 가정
        
        if day_open > 0:
            daily_change = (curr_high - day_open) / day_open
            
            # 만약 당일 시초가 대비 고점이 80% 이상 치솟았다면?
            if daily_change >= self.max_daily_change:
                self.logger.warning(f"🚫 [Overheat Ban] {ticker} 급등({daily_change*100:.1f}%)으로 인한 진입 금지")
                self.banned_tickers.add(ticker)
                return None

        # -----------------------------------------------------------
        # [NEW Logic 0] 폭락 방지 (Crash Protection) - JEM 사례 방지
        # -----------------------------------------------------------
        # 최근 5개 봉(현재 봉 제외) 중 하나라도 -15% 이상 폭락한 음봉이 있다면 진입 금지
        # 이유: JEM처럼 -23% 하락 후 기술적 반등이 나와도 십중팔구 더 떨어짐
        
        # 최근 5분간의 데이터 확인 (인덱스 에러 방지 위해 길이 체크)
        lookback = 5
        if len(df) > lookback:
            recent_candles = df.iloc[-lookback-1:-1] # 현재 봉(-1) 제외한 직전 5개
            
            for idx, row in recent_candles.iterrows():
                open_p = row['open']
                close_p = row['close']
                
                if open_p > 0:
                    change_pct = (close_p - open_p) / open_p
                    
                    # -15% 이상 하락한 '장대 음봉' 발견 시
                    if change_pct <= -0.15: 
                        self.logger.warning(f"📉 [Crash Protect] {ticker} 최근 폭락 감지({change_pct*100:.1f}%) -> 진입 보류")
                        return None

        # -----------------------------------------------------------
        # [Indicator] 지표 계산 (EMA)
        # -----------------------------------------------------------
        ema = df['close'].ewm(span=self.ma_length, adjust=False).mean()
        
        curr_row = df.iloc[-1]
        prev_row = df.iloc[-2] # 눌림목 후보 (Dip Candle)
        
        curr_price = curr_row['close']
        curr_ema = ema.iloc[-1]
        
        prev_open = prev_row['open']   # [NEW] 시가
        prev_close = prev_row['close'] # [NEW] 종가
        prev_low = prev_row['low']
        prev_ema = ema.iloc[-2]        

        # -----------------------------------------------------------
        # [NEW Logic 2] 유연한 눌림목 & 안착 (Flexible Dip & Hover)
        # -----------------------------------------------------------
        
        # 1. Dip (눌림목): 
        #    A) 이전 저가가 EMA 근처까지 내려왔는가? (기존)
        dip_threshold = prev_ema * (1.0 + self.dip_tolerance)
        is_deep_enough = prev_low <= dip_threshold
        
        #    B) [수정] 눌림목 캔들은 반드시 '음봉(Bearish)'이어야 함 - GLSI 사례 방지
        #       양봉이라면 '눌림'이 아니라 '상승 중 잠시 저가만 찍은 것'일 수 있음
        is_bearish_dip = prev_close < prev_open 
        
        # 2. Hover (안착): 현재가가 EMA 근처에서 버티고 있는가?
        hover_threshold = curr_ema * (1.0 - self.hover_tolerance)
        is_hovering = curr_price >= hover_threshold
        
        # [최종 판단]
        # 깊이(Dip) + 음봉(Bearish) + 지지(Hover) 3박자가 맞아야 함
        if is_deep_enough and is_bearish_dip and is_hovering:
            
            return {
                'type': 'BUY',
                'strategy': self.name,
                'price': curr_price,
                'ticker': ticker, 
                'time': curr_row['time'],
                'reason': f"Bearish Dip(Low {prev_low:.2f} <= {dip_threshold:.2f}) & Hover"
            }
            
        return None
    
    def check_exit_signal(self, current_price, entry_price, highest_price=None):
        """
        [수정 2] 매도 로직 변경: Trailing Stop -> Target Profit
        highest_price 인자는 이제 사용하지 않습니다.
        """
        if current_price <= 0 or entry_price <= 0:
            return None

        pnl_pct = (current_price - entry_price) / entry_price

        # -----------------------------------------------------------
        # A. [익절] Target Profit (10%)
        # -----------------------------------------------------------
        if pnl_pct >= self.tp_pct:
            return {
                'type': 'SELL',
                'reason': f"TAKE_PROFIT ({pnl_pct*100:.2f}% >= {self.tp_pct*100:.1f}%)"
            }

        # -----------------------------------------------------------
        # B. [손절] Stop Loss (-40%)
        # -----------------------------------------------------------
        if pnl_pct <= -self.sl_pct:
            return {
                'type': 'SELL',
                'reason': f"STOP_LOSS ({pnl_pct*100:.2f}%)"
            }

        return None
    
    def check_sell_signal(self, portfolio):
        """
        (옵션) 만약 main.py의 단순 SL/TP 외에
        전략적 청산(지표 하향 돌파 등)을 원하면 여기에 구현.
        현재는 main.py가 SL/TP를 전담하므로 비워둠.
        """
        pass

# Factory 함수
def get_strategy():
    return EmaStrategy()