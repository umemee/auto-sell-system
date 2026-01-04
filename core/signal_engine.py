# core/signal_engine.py
import logging
import pandas as pd
import numpy as np
from typing import Optional
from core.action_plan import ActionPlan
from config import Config

class SignalEngine:
    def __init__(self):
        self.logger = logging.getLogger("SignalEngine")
        
        # [Strategy] ROD_B (SMA 200 Deep Value)
        self.STRATEGY_NAME = Config.STRATEGY_NAME
        self.STOP_LOSS_PCT = Config.STOP_LOSS_PCT
        self.TAKE_PROFIT_PCT = Config.TAKE_PROFIT_PCT
        
        # 스캐닝 조건
        self.SCAN_MIN_CHANGE = Config.SCAN_MIN_CHANGE # 0.40
        self.SCAN_DELAY_MIN = Config.SCAN_DELAY_MIN   # 10분

    def analyze(self, symbol: str, candles: list, balance: float) -> Optional[ActionPlan]:
        """
        ROD_B 전략 분석: 40% 급등 후 10분 지연 -> SMA 200 지지 매수
        """
        if not candles or len(candles) < 200:
            self.logger.debug(f"{symbol}: 데이터 부족 ({len(candles)} < 200)")
            return None

        try:
            # 1. 데이터프레임 변환
            df = pd.DataFrame(candles)
            df['close'] = pd.to_numeric(df['last'])
            df['open'] = pd.to_numeric(df['open'])
            df['high'] = pd.to_numeric(df['high'])
            df['low'] = pd.to_numeric(df['low'])
            
            # 시간순 정렬 (과거 -> 최신)
            # KIS API는 최신이 [0]일 수 있으므로 확인 필요 (보통 최신이 앞이면 역순 정렬)
            # 여기서는 입력이 최신순이라고 가정하고 과거->최신으로 뒤집음
            df = df.iloc[::-1].reset_index(drop=True)
            
            # 2. 지표 계산 (SMA 200)
            df['sma_200'] = df['close'].rolling(window=200).mean()
            
            # 3. 데이터 추출
            current_row = df.iloc[-1]
            price = current_row['close']
            sma_200 = current_row['sma_200']
            day_open = df['open'].iloc[0] # 데이터 범위 내 시가 (주의: 장시작 시가여야 정확함)
            
            if pd.isna(sma_200): return None

            # 4. [Rule 2] 스캐닝 조건 확인 (40% 급등 & 10분 지연)
            # (1) 40% 급등 여부 확인
            surge_mask = (df['close'] >= day_open * (1 + self.SCAN_MIN_CHANGE))
            
            if not surge_mask.any():
                return None # 급등한 적 없음
                
            # (2) 첫 급등 시점 확인 및 10분 지연 체크
            first_surge_idx = surge_mask.idxmax()
            bars_since_surge = len(df) - 1 - first_surge_idx
            
            if bars_since_surge < self.SCAN_DELAY_MIN:
                # 급등은 했으나 아직 10분이 안 지남 -> 대기
                return None

            # 5. [ROD_B Logic] 진입 판단
            # 현재가가 SMA 200 근처에 도달했는지 확인 (Limit Order 개념)
            # 실전 봇은 지정가를 걸어두는 방식 or 근접 시 진입
            # 여기서는 SMA 200 가격 자체를 Entry Price로 제안
            
            # 현재가가 SMA 200 대비 1% 이내로 근접했거나, 이미 아래로 뚫고 내려갔을 때 신호 발생
            dist_pct = (price - sma_200) / sma_200
            
            # 조건: 가격이 SMA 200보다 낮거나(이미 뚫음), 아주 살짝 위(0.5%)일 때
            if dist_pct <= 0.005: 
                self.logger.info(f"🎯 [{self.STRATEGY_NAME}] {symbol} ROD_B Signal! Price:{price}, SMA200:{sma_200:.2f}")
                
                # 수량 계산 (All-in Mode)
                buy_qty = Config.get_order_qty(sma_200, balance)
                
                return ActionPlan(
                    symbol=symbol,
                    signal_type='LONG',
                    confidence=0.95, 
                    reason=f"ROD_B (SMA200 Support), Surge detected {bars_since_surge}m ago",
                    entry_price=sma_200, # 지정가 = SMA 200
                    quantity=buy_qty, 
                    stop_loss=sma_200 * (1 - self.STOP_LOSS_PCT),
                    take_profit=[sma_200 * (1 + self.TAKE_PROFIT_PCT)]
                )
                
        except Exception as e:
            self.logger.error(f"Analysis Error ({symbol}): {e}")
            
        return None