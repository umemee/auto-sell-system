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
        
        self.symbol = Config.TARGET_SYMBOL
        self.exchange = Config.EXCHANGE_CD
        
        # 디버깅 정보
        self.debug_info = {
            "target_price": 0.0,
            "trend_ok": False,
            "reason": "초기화 중..."
        }

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except:
                pass
        return {"has_position": False, "buy_price": 0.0, "qty": 0, "highest_price": 0.0}

    def _save_state(self):
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f)

    def calculate_indicators(self, df):
        if len(df) < 50: return df
        df['SMA_50'] = df['close'].rolling(window=50).mean()
        df['EMA_50'] = df['close'].ewm(span=50, adjust=False).mean()
        df['EMA_100'] = df['close'].ewm(span=100, adjust=False).mean() 
        return df

    def update_market_data(self):
        self.df_1m = self.api.get_candles(self.exchange, self.symbol, Config.TIMEFRAME_1M)
        self.df_5m = self.api.get_candles(self.exchange, self.symbol, Config.TIMEFRAME_5M)
        
        if self.df_1m.empty or self.df_5m.empty:
            self.debug_info["reason"] = "캔들 데이터 수신 실패"
            return False

        self.df_1m = self.calculate_indicators(self.df_1m)
        self.df_5m = self.calculate_indicators(self.df_5m)
        self.current_price = self.api.get_current_price(self.exchange, self.symbol)
        return True

    def check_entry_signal(self):
        """[매수 로직] 🔥 테스트 모드: 무조건 매수 진입"""
        # 1. 이미 보유 중이면 패스
        if self.state["has_position"]:
            self.debug_info["reason"] = "이미 보유중"
            return False

        # 2. [테스트] 강제 매수 신호 발생!
        self.debug_info["trend_ok"] = True
        self.debug_info["target_price"] = self.current_price 
        self.debug_info["reason"] = "🔥 [테스트] 강제 매수 진입"
        
        logger.warning("🚨 TEST MODE: 강제 매수 신호를 생성합니다!")
        return True

    # [복구됨] 이 함수가 지워져서 에러가 났던 것일세!
    def execute_buy(self):
        qty = int(Config.TOTAL_BUDGET_USD // self.current_price)
        if qty < 1: 
            self.debug_info["reason"] = "예산 부족 (수량 0)"
            return False

        res = self.api.place_order_final(self.exchange, self.symbol, "BUY", qty, self.current_price)
        if res:
            self.state["has_position"] = True
            self.state["buy_price"] = self.current_price
            self.state["qty"] = qty
            self.state["highest_price"] = self.current_price
            self._save_state()
            return True 
        return False

    def check_exit_signal(self):
        if not self.state["has_position"]: return None

        buy_price = self.state["buy_price"]
        curr_price = self.current_price
        
        if curr_price > self.state["highest_price"]:
            self.state["highest_price"] = curr_price
            self._save_state()
        
        highest = self.state["highest_price"]
        pnl_rate = (curr_price - buy_price) / buy_price * 100
        drop_rate = (curr_price - highest) / highest * 100

        reason = ""
        if pnl_rate >= 3.0: reason = "Take Profit (+3%)"
        elif drop_rate <= -2.0: reason = "Trailing Stop (-2%)"
        elif pnl_rate <= -3.0: reason = "Stop Loss (-3%)"
            
        if reason:
            if self.execute_sell(self.state["qty"], reason):
                return f"{reason} (수익률: {pnl_rate:.2f}%)"
        return None

    def execute_sell(self, qty, reason):
        res = self.api.place_order_final(self.exchange, self.symbol, "SELL", qty, self.current_price)
        if res:
            logger.info(f"매도 완료: {reason}")
            self.state = {"has_position": False, "buy_price": 0.0, "qty": 0, "highest_price": 0.0}
            self._save_state()
            return True
        return False