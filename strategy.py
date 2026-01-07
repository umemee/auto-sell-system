import pandas as pd
import numpy as np

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

        # [지표] EMA / SMA 계산 (Shift 1 적용)
        df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean().shift(1)
        df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean().shift(1)
        df['sma_200'] = df['close'].rolling(window=200).mean().shift(1)
        
        # 추가 지표 (VWAP 등 필요시 여기에 구현)
        return df

    def get_buy_signal(self, df, symbol, current_price_data=None):
        """현재 데이터(df)를 보고 매수 신호가 있는지 판단"""
        if df.empty or len(df) < 5: return None
        
        # 지표 계산
        df = self.calculate_indicators(df)
        row = df.iloc[-1] # 현재 봉
        
        # 활성화된 전략만 체크
        for name, params in self.strategies.items():
            # config.ACTIVE_STRATEGY와 일치하는 전략만 실행하도록 외부에서 제어하지만,
            # 혹시 모를 내부 필터링을 위해 enabled 체크 유지
            if not params['enabled']: continue
            
            # 전략별 진입가(Limit Price) 계산
            limit_price = 0
            
            if name == 'NEW_PRE': 
                # [논리 수정] 캔들(df)의 첫 값이 아니라, API가 준 '진짜 시가'를 사용
                if current_price_data and 'open' in current_price_data:
                    limit_price = current_price_data['open']
                else:
                    # 데이터가 없으면 기존 방식(불완전하지만) 사용
                    limit_price = row.get('day_open', 0)
            
            elif name == 'ATOM_SUP_EMA200':
                limit_price = row.get('ema_200', 0)

            elif name == 'ROD_B':
                limit_price = row.get('sma_200', 0)

            elif name == 'ATOM_SUP_EMA50':
                limit_price = row.get('ema_50', 0)
                
            # (나머지 전략들은 기본 로직이 비슷하므로 필요시 추가)
            
            # 유효성 체크
            if limit_price <= 0: continue
            
            # [수정] 매수 버퍼 (변수화)
            BUY_TOLERANCE = 1.005 # 0.5% 위까지는 매수 허용
            
            # 진입 조건: 현재 저가(Low)가 지정가(Limit)를 건드렸는가?
            current_low = row['low']
            
            if current_low <= limit_price * BUY_TOLERANCE:
                return {
                    'price': limit_price,
                    'strategy': name,
                    'comment': f"{name} Signal"
                }
        
        return None