# strategy.py
import pandas as pd
import datetime
import pytz
from config import Config
from infra.utils import get_logger

class EmaStrategy:
    """
    [EMA Deterministic Strategy V9.1]
    - 공식 문서 데이터 포맷 호환 완료
    """
    def __init__(self):
        self.name = "EMA_Deterministic_V9"
        self.logger = get_logger("Strategy")
        
        # 설정값 로드
        self.ma_length = getattr(Config, 'EMA_LENGTH', 20) 
        self.tp_pct = getattr(Config, 'TARGET_PROFIT_PCT', 0.12)
        self.sl_pct = getattr(Config, 'STOP_LOSS_PCT', 0.40)
        self.dip_tolerance = getattr(Config, 'DIP_TOLERANCE', 0.005)
        # 타임 컷 설정값 로드
        self.max_holding_minutes = getattr(Config, 'MAX_HOLDING_MINUTES', 90)
        # 중복 진입 방지용 (마지막으로 신호 보낸 캔들 시간 저장)
        self.processed_candles = {} 

    def check_entry(self, ticker, df):
        """
        [진입 신호 확인]
        df columns: date, time, open, high, low, close, volume, datetime
        """
        # 데이터 개수 확인 (최소 EMA 길이 + 2개 필요)
        if len(df) < self.ma_length + 2:
            return None 

        # 1. EMA 계산
        df['ema'] = df['close'].ewm(span=self.ma_length, adjust=False).mean()
        
        # 2. 분석 대상 캔들 인덱스 (뒤에서부터)
        # -1: 현재 진행 중인 봉 (사용 안 함)
        # -2: 직전 완성된 봉 (T-1) -> 분석 대상
        # -3: 전전 완성된 봉 (T-2) -> 분석 대상
        
        t1 = df.iloc[-2] # T-1
        t2 = df.iloc[-3] # T-2
        
        # [중복 방지] 이미 처리한 캔들인지 확인 (시간 기준)
        last_processed_time = self.processed_candles.get(ticker)
        if last_processed_time == t1['datetime']:
            return None

        # ==========================================
        # 🎯 전략 로직 (T-1 확정 봉 기준)
        # ==========================================
        
        # 조건 1: T-2 시점 정배열 (종가가 EMA 위에 있었음)
        if t2['close'] < t2['ema']:
            return None

        # 조건 2: T-1 시점 눌림목 발생 (Deep Dip)
        # 저가가 EMA 근처까지 내려왔는가?
        touch_price = t1['ema'] * (1.0 + self.dip_tolerance)
        if t1['low'] > touch_price:
            return None # 충분히 눌리지 않음

        # 조건 3: T-1 시점 지지 성공 (Close Defense)
        # 종가가 EMA를 크게 이탈하지 않고 지켜냈는가? (0.1% 오차 허용)
        # 백테스트 로직: 종가가 EMA보다 확실히 위에 있어야 함 (Strict Support)
        if t1['close'] <= t1['ema']:
            return None # 지지 실패 (무너짐)

        # 조건 4: (옵션) T-1은 음봉이어야 더 신뢰도 높음 (눌림목의 정석)
        # if t1['close'] > t1['open']: return None 

        # ==========================================
        # ✅ 매수 신호 발생
        # ==========================================
        # 처리 완료 기록 업데이트
        self.processed_candles[ticker] = t1['datetime']
        
        return {
            'type': 'BUY',
            'ticker': ticker,
            'price': df.iloc[-1]['open'], # 현재 봉의 시가로 진입 시도
            'time': datetime.datetime.now()
        }

    def check_exit(self, ticker, position, current_price, now_time):
        """청산 로직 (익절/손절/타임컷)"""
        entry_price = position['entry_price']
        pnl_pct = (current_price - entry_price) / entry_price
        
        # 1. 익절 (Take Profit)
        if pnl_pct >= self.tp_pct:
            return {'type': 'SELL', 'reason': 'TAKE_PROFIT'}
        
        # 2. 손절 (Stop Loss)
        if pnl_pct <= -self.sl_pct:
            return {'type': 'SELL', 'reason': 'STOP_LOSS'}
            
        # 3. 🔴 [추가] 타임 컷 (Time Cut)
        # 진입 시간이 기록되어 있다면 경과 시간 체크
        if 'entry_time' in position and position['entry_time']:
            entry_time = position['entry_time']
            # entry_time이 timezone 정보가 없을 수 있으므로 안전하게 처리
            if entry_time.tzinfo is None:
                 entry_time = pytz.timezone('US/Eastern').localize(entry_time)
            
            # 경과 시간(분) 계산
            elapsed_minutes = (now_time - entry_time).total_seconds() / 60
            
            if elapsed_minutes >= self.max_holding_minutes:
                # 90분 지났으면 무조건 청산 (시장가 매도를 위해 특별 이유코드 부여)
                return {'type': 'SELL', 'reason': 'TIME_CUT'}
                
        return None
    
    # Factory 함수
def get_strategy():
    return EmaStrategy()