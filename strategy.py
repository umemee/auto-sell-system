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
        self.ma_length = getattr(Config, 'EMA_LENGTH', 20) 
        self.tp_pct = getattr(Config, 'TP_PCT', 0.10)      # 익절 10%
        self.sl_pct = getattr(Config, 'STOP_LOSS_PCT', 0.45) # Config 변수명 변경 반영
        self.dip_tolerance = 0.005   # 0.5% (깻잎 한 장 차이 허용)
        self.max_daily_change = 0.80 # 80% (전일 종가 대비 80% 상승 시 진입 금지)
        
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
        # [Indicator] 지표 계산 (EMA)
        # -----------------------------------------------------------
        ema = df['close'].ewm(span=self.ma_length, adjust=False).mean()
        
        # 1. 현재 가격 정보 (Current Bar)
        curr_price = df['close'].iloc[-1] 
        curr_ema = ema.iloc[-1]
        
        # 2. 직전 봉 정보 (Previous Bar)
        prev_low = df['low'].iloc[-2]  
        prev_ema = ema.iloc[-2]        

        # -----------------------------------------------------------
        # [NEW Logic 2] 유연한 눌림목 (Flexible Dip)
        # -----------------------------------------------------------
        # 기존: is_dip = prev_low < prev_ema
        # 변경: EMA보다 0.5% 위까지만 내려와도 눌림목으로 인정 (깻잎 한 장)
        dip_threshold = prev_ema * (1.0 + self.dip_tolerance)
        
        is_dip = prev_low <= dip_threshold
        is_rebound = curr_price > curr_ema
        
        if is_dip and is_rebound:
            # 매수 신호 발생
            return {
                'type': 'BUY',
                'strategy': self.name,
                'price': curr_price,
                'ticker': ticker, 
                'time': df['time'].iloc[-1],
                'reason': f"Flexible Dip(Low {prev_low:.2f} <= {dip_threshold:.2f}) & Rebound"
            }
            
        return None
    
    def check_exit_signal(self, current_price, entry_price, highest_price):
        """
        [매도 신호 판정]
        백테스팅 로직: Stop Loss(-45%) OR Trailing Stop(7%↑, 2%↓)
        """
        if current_price <= 0 or entry_price <= 0:
            return None

        # 1. Stop Loss 체크
        loss_pct = (current_price - entry_price) / entry_price
        # 설정값(-0.45)보다 더 떨어지면(-0.50 등) 손절
        if loss_pct <= -self.sl_pct:
            return {
                'type': 'SELL',
                'reason': f"STOP_LOSS ({loss_pct*100:.2f}%)"
            }

        # 2. Trailing Stop 체크
        # (1) 최고가 갱신 로직은 Main/Portfolio에서 관리한다고 가정하고, 여기선 값만 받음
        # (2) 트레일링 발동 조건 확인
        max_profit_pct = (highest_price - entry_price) / entry_price
        
        # 아직 목표 수익(7%)에 도달한 적이 없으면 트레일링 체크 안 함
        config_ts_active = getattr(Config, 'TS_ACTIVATION_PCT', 0.07)
        if max_profit_pct < config_ts_active:
            return None
            
        # (3) 발동 후, 고점 대비 하락폭 체크
        # 고점 대비 현재가 하락률
        drawdown_from_high = (highest_price - current_price) / highest_price
        
        config_callback = getattr(Config, 'TS_CALLBACK_PCT', 0.02)
        if drawdown_from_high >= config_callback:
            return {
                'type': 'SELL',
                'reason': f"TRAILING_STOP (Max:{max_profit_pct*100:.1f}%, Drop:{drawdown_from_high*100:.1f}%)"
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