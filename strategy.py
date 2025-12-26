# strategy.py
import pandas as pd
import json
import os
from config import Config
from utils import get_logger

logger = get_logger()

class GapZoneScalper:
    def __init__(self, api):
        self.api = api
        self.state_file = "trade_state.json"
        self.states = self._load_states() # [변경] 단일 state -> states (Dictionary)
        self.exchange = Config.EXCHANGE_CD
        
        # 디버깅 정보도 종목별로 관리
        self.debug_info = {} 

    def _load_states(self):
        """파일에서 모든 종목의 상태를 로드"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except:
                pass
        return {} # 파일 없으면 빈 딕셔너리

    def _save_states(self):
        """상태 파일 저장"""
        with open(self.state_file, 'w') as f:
            json.dump(self.states, f)

    def _get_symbol_state(self, symbol):
        """특정 종목의 상태 가져오기 (없으면 초기화)"""
        if symbol not in self.states:
            self.states[symbol] = {
                "has_position": False, 
                "buy_price": 0.0, 
                "qty": 0, 
                "highest_price": 0.0
            }
        return self.states[symbol]

    def calculate_indicators(self, df):
        if len(df) < 50: return df
        df['SMA_50'] = df['close'].rolling(window=50).mean()
        df['EMA_50'] = df['close'].ewm(span=50, adjust=False).mean()
        df['EMA_100'] = df['close'].ewm(span=100, adjust=False).mean() 
        return df

    def process_symbol(self, symbol):
        """
        [핵심] Main에서 호출하는 함수
        한 종목에 대해 데이터 수집 -> 매수/매도 판단 -> 실행까지 한 번에 수행
        """
        # 1. 상태 및 디버그 초기화
        state = self._get_symbol_state(symbol)
        self.debug_info[symbol] = {"target_price": 0, "reason": "대기중"}

        # 2. 데이터 수집
        df_1m = self.api.get_candles(self.exchange, symbol, Config.TIMEFRAME_1M)
        df_5m = self.api.get_candles(self.exchange, symbol, Config.TIMEFRAME_5M)
        current_price = self.api.get_current_price(self.exchange, symbol)
        
        if df_1m.empty or df_5m.empty or current_price == 0:
            self.debug_info[symbol]["reason"] = "데이터 수신 실패"
            return

        # 3. 지표 계산
        df_1m = self.calculate_indicators(df_1m)
        df_5m = self.calculate_indicators(df_5m)

        # -----------------------------
        # [매수 로직] (포지션 없을 때만)
        # -----------------------------
        if not state["has_position"]:
            # 지표 부족 체크
            last_1m = df_1m.iloc[-1]
            last_5m = df_5m.iloc[-1]
            if pd.isna(last_1m.get('EMA_50')) or pd.isna(last_5m.get('EMA_100')):
                self.debug_info[symbol]["reason"] = "지표 계산 중"
                return

            # (1) 추세 필터
            trend_ok = (last_1m['EMA_50'] > last_1m['SMA_50']) and \
                       (current_price > last_5m['EMA_100'])
            
            if not trend_ok:
                self.debug_info[symbol]["reason"] = "추세 필터 미달"
                return

            # (2) 목표가 계산
            target_price = (last_1m['SMA_50'] + last_5m['EMA_100']) / 2
            self.debug_info[symbol]["target_price"] = target_price

            # (3) Zone 진입
            lower = target_price * 0.995
            upper = target_price * 1.005
            
            if lower <= current_price <= upper:
                # 매수 실행
                self.execute_buy(symbol, current_price)
            else:
                diff = ((current_price - target_price) / target_price) * 100
                self.debug_info[symbol]["reason"] = f"진입 대기 ({diff:.2f}%)"

        # -----------------------------
        # [매도 로직] (포지션 있을 때만)
        # -----------------------------
        else:
            self.debug_info[symbol]["reason"] = "매도 감시 중"
            buy_price = state["buy_price"]
            
            # 고점 갱신
            if current_price > state["highest_price"]:
                state["highest_price"] = current_price
                self.states[symbol] = state # 값 업데이트
                self._save_states()
            
            highest = state["highest_price"]
            pnl_rate = (current_price - buy_price) / buy_price * 100
            drop_rate = (current_price - highest) / highest * 100
            
            reason = ""
            if pnl_rate >= 3.0: reason = "Take Profit (+3%)"
            elif drop_rate <= -2.0: reason = "Trailing Stop (-2%)"
            elif pnl_rate <= -3.0: reason = "Stop Loss (-3%)"
            
            if reason:
                self.execute_sell(symbol, state["qty"], current_price, reason, pnl_rate)

    def execute_buy(self, symbol, price):
        # 종목당 예산 배분 (간단하게 N빵)
        # budget_per_stock = Config.TOTAL_BUDGET_USD / len(Config.TARGET_SYMBOLS)
        # 일단은 전체 예산 사용 (스캐너용 대비)
        qty = int(Config.TOTAL_BUDGET_USD // price)
        if qty < 1: return

        res = self.api.place_order_final(self.exchange, symbol, "BUY", qty, price)
        if res:
            self.states[symbol]["has_position"] = True
            self.states[symbol]["buy_price"] = price
            self.states[symbol]["qty"] = qty
            self.states[symbol]["highest_price"] = price
            self._save_states()
            # 텔레그램 알림은 리턴값 대신 Main에서 상태 변화를 감지하거나 여기서 로그로 처리

    def execute_sell(self, symbol, qty, price, reason, pnl):
        res = self.api.place_order_final(self.exchange, symbol, "SELL", qty, price)
        if res:
            logger.info(f"[{symbol}] 매도 완료: {reason}")
            # 상태 초기화
            self.states[symbol] = {
                "has_position": False, "buy_price": 0.0, "qty": 0, "highest_price": 0.0
            }
            self._save_states()