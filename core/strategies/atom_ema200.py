import pandas as pd
from core.strategy_interface import IStrategy

class AtomSupEma200(IStrategy):
    def __init__(self):
        self.name = "ATOM_SUP_EMA200"
        # 백테스트 최적 파라미터
        self.tp_pct = 0.15       # +15%
        self.sl_pct = 0.08       # -8%
        self.trailing_dist = 0.05 # 고점 대비 5% 하락
        
    def calculate_indicators(self, df):
        # EMA 200 계산
        df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()
        return df

    def check_entry(self, df):
        if len(df) < 205: return None
        
        current_close = df.iloc[-1]['close']
        current_low = df.iloc[-1]['low']
        ema_200 = df.iloc[-1]['ema_200']
        
        # 진입: 저가가 EMA 200 터치 (0.5% 버퍼)
        if current_low <= ema_200 * 1.005:
            return {
                'price': ema_200,
                'comment': f"EMA200 Touch ({ema_200:.2f})"
            }
        return None

    def check_exit(self, df, entry_price, max_price, entry_time):
        current_price = df.iloc[-1]['close']
        
        # 1. Trailing Stop (추세 추종)
        if max_price > entry_price:
            trail_price = max_price * (1 - self.trailing_dist)
            if current_price <= trail_price:
                return {'type': 'MARKET', 'reason': 'EXIT_TRAILING'}
        
        # 2. Hard SL
        sl_price = entry_price * (1 - self.sl_pct)
        if current_price <= sl_price:
            return {'type': 'MARKET', 'reason': 'SL'}
            
        # 3. Hard TP
        tp_price = entry_price * (1 + self.tp_pct)
        if current_price >= tp_price:
            return {'type': 'MARKET', 'reason': 'TP'}
            
        return None