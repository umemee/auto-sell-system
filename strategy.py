# strategy.py
import pandas as pd
import datetime
import pytz
from config import Config
from infra.utils import get_logger

class EmaStrategy:
    """
    [EMA Deterministic Strategy V9.2]
    - 업데이트: 'DROP' 신호 추가 (좀비 감시 방지)
    - 차트 훼손 시 즉시 감시 해제 요청
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
        Return:
          - {'type': 'BUY', ...}: 매수 진입
          - {'type': 'DROP', ...}: 감시 포기 (차트 훼손)
          - None: 관망 (아직 조건 안 맞음, 계속 감시)
        """
        # 데이터 개수 확인 (최소 EMA 길이 + 2개 필요)
        if len(df) < self.ma_length + 2:
            return None 

        # 1. EMA 계산
        df['ema'] = df['close'].ewm(span=self.ma_length, adjust=False).mean()
        
        # 2. 분석 대상 캔들 인덱스 (뒤에서부터)
        t1 = df.iloc[-2] # T-1 (직전 확정 봉)
        t2 = df.iloc[-3] # T-2 (전전 확정 봉)
        
        # [중복 방지] 이미 처리한 캔들인지 확인
        last_processed_time = self.processed_candles.get(ticker)
        if last_processed_time == t1['datetime']:
            return None

        # ==========================================
        # 🎯 전략 로직 (T-1 확정 봉 기준)
        # ==========================================
        
        # 🗑️ [Drop 조건 1] 추세가 이미 꺾임 (T-2가 이미 역배열)
        # 상승 추세가 아니므로 감시할 가치가 없음 -> 삭제
        if t2['close'] < t2['ema']:
            return {'type': 'DROP', 'reason': 'No Uptrend (T-2 < EMA)'}

        # 🛡️ [Drop 조건 2] 지지 실패 (Close Defense Fail)
        # 눌림목인 줄 알았으나 종가가 EMA 밑으로 뚫고 내려감 -> 지지선 붕괴 -> 삭제
        if t1['close'] <= t1['ema']:
            return {'type': 'DROP', 'reason': 'Support Broken (Close <= EMA)'}

        # ⏳ [Wait 조건] 아직 안 눌림 (Deep Dip Check)
        # 추세는 살아있으나(Close > EMA), 우리가 원하는 타점(EMA 근접)까지 안 옴
        touch_price = t1['ema'] * (1.0 + self.dip_tolerance)
        if t1['low'] > touch_price:
            return None # 아직 타점 안 옴 -> 계속 감시(Keep Watching)

        # ==========================================
        # ✅ 매수 신호 발생 (모든 조건 통과)
        # ==========================================
        # 조건: T-2 정배열 AND T-1 눌림 발생 AND T-1 종가 지지 성공
        
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
        if 'entry_time' in position and position['entry_time']:
            entry_time = position['entry_time']
            # Timezone 처리
            if entry_time.tzinfo is None:
                 entry_time = pytz.timezone('US/Eastern').localize(entry_time)
            
            # 경과 시간(분) 계산
            elapsed_minutes = (now_time - entry_time).total_seconds() / 60
            
            if elapsed_minutes >= self.max_holding_minutes:
                # 지정된 시간(90분) 경과 시 강제 청산
                return {'type': 'SELL', 'reason': 'TIME_CUT'}
                
        return None
    
# Factory 함수
def get_strategy():
    return EmaStrategy()