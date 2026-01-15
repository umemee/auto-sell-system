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
        
        # [백테스트 로직 동기화]
        # iloc[-1]: 현재 실시간 진행 중인 봉 (Current Bar)
        # iloc[-2]: 직전에 완성된 봉 (Previous Bar)
        
        # 1. 현재 가격 정보
        curr_price = df['close'].iloc[-1] 
        curr_ema = ema.iloc[-1]
        
        # 2. 직전 봉 정보 (완성된 봉 기준)
        prev_low = df['low'].iloc[-2]  # 전 봉의 저가
        prev_ema = ema.iloc[-2]        # 전 봉의 EMA

        # [전략 핵심 로직]
        # 조건 1 (Dip): 전 봉의 저가가 EMA보다 낮았어야 함 (눌림목 발생)
        # 조건 2 (Rebound): 현재 가격이 EMA보다 높아야 함 (반등 성공)
        is_dip = prev_low < prev_ema
        is_rebound = curr_price > curr_ema
        
        if is_dip and is_rebound:
            # 매수 신호 발생
            return {
                'type': 'BUY',
                'strategy': self.name,
                'price': curr_price,
                'ticker': "UNKNOWN", 
                'time': df['time'].iloc[-1],
                'reason': f"Dip(Low {prev_low} < EMA) & Rebound(Price {curr_price} > EMA)"
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