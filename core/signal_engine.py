# core/signal_engine.py
import logging
import pandas as pd
import numpy as np
from typing import Optional
from core.action_plan import ActionPlan

class SignalEngine:
    def __init__(self):
        self.logger = logging.getLogger("SignalEngine")
        
        # [Phase 1] ATOM_SUP_EMA5 전략 파라미터
        self.STRATEGY_NAME = "ATOM_SUP_EMA5"
        self.SUPPORT_THRESHOLD = 0.01  # 지지 범위 (1%)
        self.STOP_LOSS_PCT = 0.015     # 손절 -1.5% (스캘핑이라 타이트하게)
        self.TAKE_PROFIT_PCT = 0.03    # 익절 +3.0%

    def analyze(self, symbol: str, candles: list, balance: float) -> Optional[ActionPlan]:
        """
        EMA 5일선 지지 패턴 분석
        :param candles: KIS API에서 가져온 캔들 리스트 (최신순)
        """
        if not candles or len(candles) < 20:
            return None

        # 1. 데이터프레임 변환 및 전처리
        try:
            df = pd.DataFrame(candles)
            # KIS 데이터 컬럼: last(종가), open, high, low
            df['close'] = pd.to_numeric(df['last'])
            df['low'] = pd.to_numeric(df['low'])
            
            # 시간순 정렬 (과거 -> 최신)
            df = df.iloc[::-1].reset_index(drop=True)
            
            # 2. 지표 계산 (EMA 5)
            df['ema_5'] = df['close'].ewm(span=5, adjust=False).mean()
            
            # 3. 최신 데이터(마지막 봉) 확인
            current_row = df.iloc[-1]
            price = current_row['close']
            low = current_row['low']
            ema_5 = current_row['ema_5']
            
            # 4. 전략 로직: 5선 지지 (Low가 EMA5 근처 1% 이내 접근)
            # 조건: 저가가 EMA5보다 살짝 아래거나 1% 위 이내
            #      AND 종가는 EMA5 위에 있어야 함 (지지에 성공한 모습)
            
            dist_pct = abs(low - ema_5) / ema_5
            is_support = (dist_pct <= self.SUPPORT_THRESHOLD) and (price >= ema_5)
            
            if is_support:
                self.logger.info(f"✨ [{self.STRATEGY_NAME}] {symbol} Signal! Price:{price}, EMA5:{ema_5:.2f}")
                
                # 수량 계산 (All-in 모드: RiskManager/Config에서 최종 결정하지만 여기서도 가이드)
                buy_qty = int((balance * 0.98) // price)
                
                return ActionPlan(
                    symbol=symbol,
                    signal_type='LONG',
                    confidence=0.9, # 높은 확신
                    reason=f"EMA5 Support (Dist: {dist_pct*100:.2f}%)",
                    entry_price=price,
                    quantity=buy_qty, 
                    stop_loss=price * (1 - self.STOP_LOSS_PCT),
                    take_profit=[price * (1 + self.TAKE_PROFIT_PCT)]
                )
                
        except Exception as e:
            self.logger.error(f"Analysis Error ({symbol}): {e}")
            
        return None