import pandas as pd
import numpy as np
from config import Config


# ==========================================
# 🎯 GAPZONE STRATEGY LEGOS (Zone 1)
# ==========================================
class GapZoneStrategy:
    def __init__(self):
        # 🏆 챔피언십 리포트 기반 11개 전략 전체 로드
        self.strategies = {
            # 1. NEW_PRE (현재 우승 전략)
            'NEW_PRE': { 'enabled': True, 'priority': 1, 'stop_loss': -0.05, 'take_profit': 0.07 },
            
            # 2. ATOM_SUP_EMA200 (안정성)
            'ATOM_SUP_EMA200': { 'enabled': True, 'priority': 2, 'stop_loss': -0.05, 'take_profit': 0.10 },

            # 3. NEW_ORB (돌파)
            'NEW_ORB': { 'enabled': True, 'priority': 3, 'stop_loss': -0.04, 'take_profit': 0.15 },
            
            # 4. DIP_SNIPER (낙주)
            'DIP_SNIPER': { 'enabled': True, 'priority': 4, 'stop_loss': -0.05, 'take_profit': 0.10 },

            # 5. ROD_B (균형)
            'ROD_B': { 'enabled': True, 'priority': 5, 'stop_loss': -0.08, 'take_profit': 0.10 },
            
            # 기타 전략들 (필요시 활성화)
            'ATOM_SUP_EMA50': { 'enabled': True, 'priority': 6, 'stop_loss': -0.05, 'take_profit': 0.10 },
            'ATOM_SUP_VWAP': { 'enabled': True, 'priority': 7, 'stop_loss': -0.03, 'take_profit': 0.08 },
            'ROD_A': { 'enabled': True, 'priority': 8, 'stop_loss': -0.05, 'take_profit': 0.10 },
            'MOL_CONFLUENCE': { 'enabled': True, 'priority': 9, 'stop_loss': -0.05, 'take_profit': 0.12 },
            'ATOM_SUP_EMA20': { 'enabled': True, 'priority': 10, 'stop_loss': -0.05, 'take_profit': 0.10 },
            'ROD_C': { 'enabled': True, 'priority': 11, 'stop_loss': -0.05, 'take_profit': 0.10 },
            'ATOM_SUP_EMA5': { 'enabled': True, 'priority': 12, 'stop_loss': -0.08, 'take_profit': 0.10 },
        }

    def calculate_indicators(self, df):
        """지표 계산 (Shift 1 필수: 움직이는 골대 방지)"""
        df = df.copy()
        
        if df.empty: return df

        # [공통] 당일 시가
        df['day_open'] = df['open'].iloc[0] 

        # [지표 1] EMA (5종)
        df['ema_5'] = df['close'].ewm(span=5, adjust=False).mean().shift(1)
        df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean().shift(1)
        df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean().shift(1)
        df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean().shift(1)
        
        # [지표 2] SMA (2종)
        df['sma_50'] = df['close'].rolling(window=50).mean().shift(1)
        df['sma_200'] = df['close'].rolling(window=200).mean().shift(1)

        # [지표 3] Bollinger Bands (DIP_SNIPER용)
        sma_20 = df['close'].rolling(window=20).mean().shift(1)
        std_20 = df['close']. rolling(window=20).std().shift(1)
        df['bb_lower'] = sma_20 - (2 * std_20)

        # [지표 4] VWAP
        try:
            # 일별로 VWAP 계산 (volume weighted average price)
            df['cum_vol'] = df['volume'].cumsum()
            df['cum_vol_price'] = (df['volume'] * (df['high'] + df['low'] + df['close']) / 3).cumsum()
            df['vwap'] = (df['cum_vol_price'] / df['cum_vol']).shift(1)
        except:
            df['vwap'] = np.nan

        # [지표 5] ORB (Opening Range Breakout) - NEW_ORB용
        # 프리마켓/오프닝 30분간의 최고가
        try:
            # 간단 구현:  첫 30개 봉의 최고가
            if len(df) >= 30:
                df['orb_high'] = df['high'].iloc[:30].max()
            else:
                df['orb_high'] = df['high'].max()
        except:
            df['orb_high'] = np.nan
        
        return df

    def get_buy_signal(self, df, symbol, current_price_data=None):
        """현재 데이터(df)를 보고 매수 신호가 있는지 판단"""
        if df.empty or len(df) < 5: return None
        
        # 지표 계산
        df = self.calculate_indicators(df)
        row = df.iloc[-1]  # 현재 봉
        
        # 활성화된 전략만 체크
        for name, params in self.strategies.items():
            if not params['enabled']: continue
            
            # 전략별 진입가(Limit Price) 계산
            limit_price = 0
            
            # === [Momentum Group] ===
            if name == 'NEW_ORB': 
                # ORB High (Opening Range Breakout)
                orb_high = row.get('orb_high', 0)
                if orb_high > 0:
                    limit_price = orb_high
                    
            elif name == 'NEW_PRE':  
                # 프리마켓 시가
                if current_price_data and 'open' in current_price_data:
                    limit_price = current_price_data['open']
                else: 
                    limit_price = row.get('day_open', 0)
            
            # === [Support Group:  Moving Averages] ===
            elif name == 'ATOM_SUP_EMA5': 
                limit_price = row.get('ema_5', 0)
                
            elif name == 'ATOM_SUP_EMA20':
                limit_price = row.get('ema_20', 0)
                
            elif name == 'ATOM_SUP_EMA50':
                limit_price = row.get('ema_50', 0)
                
            elif name == 'ATOM_SUP_EMA200':
                limit_price = row.get('ema_200', 0)

            # === [Support Group: VWAP & BB] ===
            elif name == 'ATOM_SUP_VWAP':
                limit_price = row.get('vwap', 0)
                
            elif name == 'DIP_SNIPER':
                # Bollinger Lower Band
                limit_price = row.get('bb_lower', 0)

            # === [Mean Reversion / Value] ===
            elif name == 'MOL_CONFLUENCE':
                # EMA 20 Confluence
                limit_price = row.get('ema_20', 0)
                
            elif name == 'ROD_A':
                # SMA 50 / EMA 50 Confluence (더 높은 값)
                sma_50 = row.get('sma_50', 0)
                ema_50 = row.get('ema_50', 0)
                limit_price = max(sma_50, ema_50)
                
            elif name == 'ROD_B':
                # SMA 200 Deep Value
                limit_price = row.get('sma_200', 0)
                
            elif name == 'ROD_C':
                # SMA 50 Value
                limit_price = row.get('sma_50', 0)
            
            # 유효성 체크
            if pd.isna(limit_price) or limit_price <= 0: 
                continue
            
            # 매수 버퍼
            BUY_TOLERANCE = Config.BUY_TOLERANCE
            
            # 진입 조건: 현재 저가(Low)가 지정가(Limit)를 건드렸는가? 
            current_low = row['low']
            
            if current_low <= limit_price * BUY_TOLERANCE:
                return {
                    'price': limit_price,
                    'strategy': name,
                    'comment': f"{name} Signal"
                }
        
        return None


