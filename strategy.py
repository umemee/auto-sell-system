# strategy.py
import time
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
        """
        스마트 주문 실행: 지정가 주문 -> 3초 대기 -> 미체결 시 취소 후 급하게 재주문
        """
        # 1. 예산 계산 (기존 로직 유지)
        qty = int(Config.TOTAL_BUDGET_USD // price)
        if qty < 1: 
            logger.warning(f"[{symbol}] 예산 부족으로 매수 불가 (필요: ${price}, 예산: ${Config.TOTAL_BUDGET_USD})")
            return

        logger.info(f"⚡ [{symbol}] 1차 지정가 매수 시도: {qty}주 @ ${price}")
        
        # 2. 1차 주문 전송 (지정가)
        # place_order_final이 아닌 place_order를 사용 (api 래퍼가 있다면 확인 필요, 여기선 place_order로 가정)
        # 만약 kis_api.py에 place_order_final만 있다면 그것을 사용하세요.
        res = self.api.place_order(self.exchange, symbol, "BUY", qty, price)
        
        if not res:
            logger.error(f"[{symbol}] 1차 주문 실패")
            return

        order_no = res.get("ODNO") # 주문 번호 획득
        
        # 3. 3초간 체결 대기 (시장의 반응 기다림)
        time.sleep(3)
        
        # 4. 미체결 잔량 확인 (새로 만든 기능!)
        remained_qty = self.api.get_unfilled_qty(symbol, order_no)
        
        # 5. 결과에 따른 분기 처리
        if remained_qty > 0:
            logger.info(f"⚠️ [{symbol}] 미체결 {remained_qty}주 발생 -> 기존 주문 취소 후 재진입 시도")
            
            # (1) 기존 주문 취소
            self.api.revise_order(self.exchange, symbol, order_no, "02", remained_qty, 0)
            time.sleep(0.5) # 취소 처리 대기
            
            # (2) 확실하게 잡기 위해 가격을 높여서 재주문 (현재가 + 2%)
            # 스캘핑에서는 놓치는 것보다 약간 비싸게라도 잡는 것이 나을 때가 있음
            final_price = round(price * 1.02, 2) 
            
            logger.info(f"🚀 [{symbol}] 2차 재주문 (Market-like): {remained_qty}주 @ ${final_price}")
            res_retry = self.api.place_order(self.exchange, symbol, "BUY", remained_qty, final_price)
            
            if res_retry:
                self._update_state(symbol, final_price, remained_qty) # 상태 업데이트
        else:
            logger.info(f"✅ [{symbol}] 1차 주문 전량 체결 완료!")
            self._update_state(symbol, price, qty) # 상태 업데이트

    def _update_state(self, symbol, buy_price, qty):
        """매수 성공 시 상태 파일 업데이트 (코드 중복 방지용 헬퍼)"""
        self.states[symbol]["has_position"] = True
        self.states[symbol]["buy_price"] = buy_price
        self.states[symbol]["qty"] = qty
        self.states[symbol]["highest_price"] = buy_price
        self._save_states()

    def execute_sell(self, symbol, qty, price, reason, pnl):
        res = self.api.place_order_final(self.exchange, symbol, "SELL", qty, price)
        if res:
            logger.info(f"[{symbol}] 매도 완료: {reason}")
            # 상태 초기화
            self.states[symbol] = {
                "has_position": False, "buy_price": 0.0, "qty": 0, "highest_price": 0.0
            }
            self._save_states()