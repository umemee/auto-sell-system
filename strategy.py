import pandas as pd
import numpy as np

# ==========================================
# 🎯 GAPZONE STRATEGY LEGOS (Zone 1)
# ==========================================
class GapZoneStrategy:
    def __init__(self):
        self.strategies = {
            # [ACTIVE] 1. Premarket Support (승률 54.6%)
            'NEW_PRE': { 
                'enabled': True, 
                'priority': 1, 
                'stop_loss': -0.05, 
                'take_profit': 0.12, 
                'description': 'Premarket High Support'
            },
            # [OFF] 2. ROD_B (안정형) - 필요 시 True로 변경
            'ROD_B': {
                'enabled': False, 
                'priority': 2, 
                'stop_loss': -0.08, 
                'take_profit': 0.10
            },
            # ... 나머지 전략들 (기본 OFF)
        }

    def calculate_indicators(self, df):
        """지표 계산 (Shift 1 필수)"""
        df = df.copy()
        
        # 1. NEW_PRE용: 당일 시가(Day Open)
        if not df.empty:
            df['day_open'] = df['open'].iloc[0] 

        # 2. ROD_B용: SMA 200
        df['sma_200'] = df['close'].rolling(window=200).mean().shift(1)
        
        # (필요하면 다른 지표 추가)
        return df

    def get_buy_signal(self, df, symbol):
        """현재 데이터(df)를 보고 매수 신호가 있는지 판단"""
        if df.empty or len(df) < 5: return None
        
        # 지표 계산
        df = self.calculate_indicators(df)
        row = df.iloc[-1] # 현재 봉
        
        # 활성화된 전략만 체크
        for name, params in self.strategies.items():
            if not params['enabled']: continue
            
            # 전략별 진입가(Limit Price) 계산
            limit_price = 0
            if name == 'NEW_PRE': limit_price = row.get('day_open', 0)
            elif name == 'ROD_B': limit_price = row.get('sma_200', 0)
            
            # 유효성 체크
            if limit_price <= 0: continue
            
            # 진입 조건: 현재 저가(Low)가 지정가(Limit)를 건드렸는가? (0.5% 버퍼)
            if row['low'] <= limit_price * 1.005:
                # 갭 보정: 시가가 더 낮으면 시가 체결
                entry_price = min(limit_price, row['open'])
                return {
                    'strategy': name,
                    'symbol': symbol,
                    'price': entry_price,
                    'sl': params['stop_loss'],
                    'tp': params['take_profit']
                }
        return None