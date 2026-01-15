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
        self.sl_pct = getattr(Config, 'SL_PCT', 0.05)      # 손절 5%

    def check_buy_signal(self, df: pd.DataFrame) -> dict:
        """
        데이터프레임(1분봉)을 받아 매수 신호를 판정
        df columns: ['time', 'open', 'high', 'low', 'close', 'volume']
        """
        if len(df) < self.ma_length + 5:
            return None

        # 1. 지표 계산 (EMA)
        # 실전에서는 속도를 위해 TA-Lib 대신 Pandas ewm 사용 (충분히 빠름)
        ema = df['close'].ewm(span=self.ma_length, adjust=False).mean()
        
        # 2. 로직 검증 (백테스팅과 100% 동일해야 함)
        # Condition:
        #  (1) 이전 봉 저가 < 이전 EMA (Dip 발생)
        #  (2) 현재 봉 종가 > 현재 EMA (Rebound 성공)
        
        prev_close = df['close'].iloc[-2]
        prev_low = df['low'].iloc[-2]
        prev_ema = ema.iloc[-2]

        curr_close = df['close'].iloc[-1]
        curr_ema = ema.iloc[-1]
        curr_time = df['time'].iloc[-1] # 혹은 index

        # [Logic Core]
        is_dip = prev_low < prev_ema
        is_rebound = curr_close > curr_ema
        
        # 추가 필터: 거래량이 너무 없으면 제외 (선택 사항)
        # if df['volume'].iloc[-1] < 1000: return None

        if is_dip and is_rebound:
            self.logger.info(f"✨ [Signal] {self.name} Dip & Rebound Confirmed!")
            self.logger.info(f"   Prev Low(${prev_low:.2f}) < EMA(${prev_ema:.2f})")
            self.logger.info(f"   Curr Close(${curr_close:.2f}) > EMA(${curr_ema:.2f})")
            
            return {
                'type': 'BUY',
                'strategy': self.name,
                'price': curr_close,
                'time': curr_time,
                'reason': f"Dip({prev_low} < {prev_ema:.2f}) -> Rebound"
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