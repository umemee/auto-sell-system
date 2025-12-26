# strategy.py
import pandas as pd
import json
import os
import time
from config import Config
from utils import get_logger

logger = get_logger()

class GapZoneScalper:
    def __init__(self, api):
        self.api = api
        self.state_file = "trade_state.json"
        self.state = self._load_state()
        
        # 전략 파라미터
        self.symbol = Config.TARGET_SYMBOL
        self.exchange = Config.EXCHANGE_CD
        
    def _load_state(self):
        """매매 상태 로드"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except:
                pass
        return {"has_position": False, "buy_price": 0.0, "qty": 0, "highest_price": 0.0}

    def _save_state(self):
        """매매 상태 저장"""
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f)

    def calculate_indicators(self, df):
        """보조지표 계산 (SMA, EMA, RSI)"""
        if len(df) < 50:
            return df
        
        # 1. 이동평균선
        df['SMA_50'] = df['close'].rolling(window=50).mean()
        df['EMA_50'] = df['close'].ewm(span=50, adjust=False).mean()
        df['EMA_200'] = df['close'].ewm(span=200, adjust=False).mean()
        
        # 2. RSI (14) - 간단 구현
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        return df

    def update_market_data(self):
        """1분봉/5분봉 데이터 수집 및 지표 업데이트"""
        self.df_1m = self.api.get_candles(self.exchange, self.symbol, Config.TIMEFRAME_1M)
        self.df_5m = self.api.get_candles(self.exchange, self.symbol, Config.TIMEFRAME_5M)
        
        if self.df_1m.empty or self.df_5m.empty:
            logger.warning("캔들 데이터 부족으로 로직 수행 건너뜀")
            return False

        self.df_1m = self.calculate_indicators(self.df_1m)
        self.df_5m = self.calculate_indicators(self.df_5m)
        
        self.current_price = self.api.get_current_price(self.exchange, self.symbol)
        return True

    def check_entry_signal(self):
        """
        [매수 로직] Gap Zone & Confluence
        """
        if self.state["has_position"]:
            return False

        # 최신 데이터 추출
        last_1m = self.df_1m.iloc[-1]
        last_5m = self.df_5m.iloc[-1]
        
        # 1. Trend Filter: 정배열 (1분: EMA50 > SMA50, 5분: Price > EMA200)
        # NaN 체크
        if pd.isna(last_1m['EMA_50']) or pd.isna(last_5m['EMA_200']):
            return False

        trend_ok = (last_1m['EMA_50'] > last_1m['SMA_50']) and \
                   (self.current_price > last_5m['EMA_200'])
        
        if not trend_ok:
            # logger.debug("Trend Filter: Fail") # 너무 잦은 로그 방지
            return False

        # 2. Volume Filter: 거래량 급감 (최근 10개 봉 최대 거래량 대비 40% 미만)
        recent_vol = self.df_1m['volume'].tail(10)
        max_vol = recent_vol.max()
        vol_ok = last_1m['volume'] < (max_vol * 0.4)
        
        if not vol_ok:
            # logger.debug("Volume Filter: Fail (Not Dry-out)")
            return False

        # 3. Target Price & Buy Zone
        # Target = (1분 SMA50 + 5분 EMA200) / 2
        target_price = (last_1m['SMA_50'] + last_5m['EMA_200']) / 2
        
        # Buy Zone: Target ± 0.5%
        lower_bound = target_price * 0.995
        upper_bound = target_price * 1.005
        
        in_zone = lower_bound <= self.current_price <= upper_bound
        
        if in_zone:
            logger.info(f"⚡ 매수 신호 발생! Price: {self.current_price} (Target: {target_price:.2f})")
            return True
        
        return False

    def execute_buy(self):
        """매수 실행"""
        # 예산 기반 수량 계산
        qty = int(Config.TOTAL_BUDGET_USD // self.current_price)
        if qty < 1:
            return

        res = self.api.place_order_final(self.exchange, self.symbol, "BUY", qty, self.current_price)
        if res:
            self.state["has_position"] = True
            self.state["buy_price"] = self.current_price
            self.state["qty"] = qty
            self.state["highest_price"] = self.current_price
            self._save_state()

    def check_exit_signal(self):
        """
        [청산 로직] 
        1. 익절: +3% (50% 분할매도 구현은 복잡하므로 여기선 전량매도로 단순화하되 로직만 포함)
        2. 트레일링 스탑: 고점 대비 -2%
        3. 손절: -3%
        """
        if not self.state["has_position"]:
            return

        buy_price = self.state["buy_price"]
        curr_price = self.current_price
        
        # 고점 갱신
        if curr_price > self.state["highest_price"]:
            self.state["highest_price"] = curr_price
            self._save_state()
        
        highest = self.state["highest_price"]
        qty = self.state["qty"]

        # 수익률 계산
        pnl_rate = (curr_price - buy_price) / buy_price * 100
        drop_rate = (curr_price - highest) / highest * 100

        reason = ""
        
        # 1. 익절 (Take Profit)
        if pnl_rate >= 3.0:
            reason = "Take Profit (+3%)"
        
        # 2. 트레일링 스탑 (Trailing Stop)
        elif drop_rate <= -2.0:
            reason = "Trailing Stop (-2% from High)"
            
        # 3. 손절 (Stop Loss)
        elif pnl_rate <= -3.0:
            reason = "Stop Loss (-3%)"
            
        if reason:
            logger.info(f"📉 매도 신호 발생: {reason} | PnL: {pnl_rate:.2f}%")
            self.execute_sell(qty, reason)

    def execute_sell(self, qty, reason):
        """매도 실행"""
        # 시장가 매도 혹은 현재가 지정가 매도
        res = self.api.place_order_final(self.exchange, self.symbol, "SELL", qty, self.current_price)
        if res:
            logger.info(f"매도 완료: {reason}")
            # 상태 초기화
            self.state = {"has_position": False, "buy_price": 0.0, "qty": 0, "highest_price": 0.0}
            self._save_state()