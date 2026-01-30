# strategy.py
import pandas as pd
import numpy as np
import datetime
import pytz
from config import Config
from infra.utils import get_logger

class EmaStrategy:
    """
    [EMA Strategy - Production Version v6.0]
    
    업그레이드 사항:
    1. Time Cut (시간 제한): 진입 후 240분(4시간) 경과 시 강제 청산하여 기회비용 확보.
    2. Entry Limit (진입 제한): 승률이 떨어지는 오전 10시(ET) 이후 신규 진입 차단.
    """
    def __init__(self):
        self.name = "EMA_Dip_Rebound_v6"
        self.logger = get_logger("Strategy")
        
        # [Config에서 파라미터 로드]
        self.ma_length = getattr(Config, 'EMA_LENGTH', 10) 
        self.tp_pct = getattr(Config, 'TARGET_PROFIT_PCT', 0.10)      # 익절 10%
        self.sl_pct = getattr(Config, 'STOP_LOSS_PCT', 0.40)          # 손절 40%
        
        # [전략 세부 파라미터]
        self.dip_tolerance = getattr(Config, 'DIP_TOLERANCE', 0.005)     # 0.5% 오차
        self.hover_tolerance = getattr(Config, 'HOVER_TOLERANCE', 0.002) # 0.2% 반등
        self.max_daily_change = 1.00 # 100% 폭등 종목 제외
        
        # [v6.0 신규 파라미터]
        self.entry_deadline = getattr(Config, 'ENTRY_DEADLINE_HOUR_ET', 10) # 10시
        self.max_holding_minutes = getattr(Config, 'MAX_HOLDING_MINUTES', 240) # 240분

    def _get_current_et_time(self):
        """현재 미국 동부 시간(ET) 반환"""
        return datetime.datetime.now(pytz.timezone('US/Eastern'))

    def check_buy_signal(self, df: pd.DataFrame, ticker=None):
        """
        매수 신호 확인 (백테스팅 로직 100% 이식 버전)
        - 변경점: 실시간 호가(Current Tick)가 아닌, '직전 완성된 봉(Closed Candle)'을 기준으로 판단
        """
        if df.empty or len(df) < self.ma_length + 3:
            return None

        # -----------------------------------------------------------
        # 🚫 [Time Filter] 진입 시간 제한 (10:00 AM ET 이후 금지)
        # -----------------------------------------------------------
        now_et = self._get_current_et_time()
        
        # [Warm-up Guard] 장 시작(04:00) 후 10분간 대기 (지표 안정화)
        if now_et.hour == 4 and now_et.minute < 10:
             return None
             
        # [Entry Deadline] 10:00 ET 이후 진입 금지
        if now_et.hour >= self.entry_deadline:
            return None

        # -----------------------------------------------------------
        # 📊 [Core Logic] EMA 및 캔들 패턴 분석
        # -----------------------------------------------------------
        # 데이터 전처리 (SettingWithCopyWarning 방지)
        df = df.copy()
        
        # EMA 계산 (전체 데이터 기준)
        df['EMA'] = df['close'].ewm(span=self.ma_length, adjust=False).mean()
        
        # [핵심 수정 1] '진행 중인 봉'이 아니라 '방금 완성된 봉'을 가져옵니다.
        # df.iloc[-1]은 현재 변동 중인 봉이므로 신뢰할 수 없습니다.
        # df.iloc[-2]가 '직전에 마감된 확정 봉'입니다.
        #target_row = df.iloc[-2]
        
        # [참고] 매수 가격은 '현재가(iloc[-1])'로 잡되, 판단은 '과거(iloc[-2])'로 합니다.
        #current_market_price = df.iloc[-1]['close']
        # [기존 코드 삭제]

        # [새로 작성] 백테스팅과 동일한 '2 Candle' 정의
        # iloc[-1]: 현재 진행 중인 봉 (무시)
        confirm_candle = df.iloc[-2]  # 방금 마감된 봉 (T) -> 지지 확인용
        dip_candle     = df.iloc[-3]  # 그 전 봉 (T-1) -> 눌림 발생용

        current_market_price = df.iloc[-1]['close'] # 주문용 현재가
        # 지표 추출 (완성된 봉 기준)
        prev_close = confirm_candle['close'] # 종가 (Rebound 확인용)
        prev_low = confirm_candle['low']     # 저가 (Dip 확인용)
        ema_value = confirm_candle['EMA']    # 당시의 EMA
        
        # -----------------------------------------------------------
        # 🧬 [DNA 이식] 백테스팅 조건과 완벽 일치시키기
        # -----------------------------------------------------------
        
        # 1. 눌림목(Dip) 확인: 해당 봉의 저가가 EMA를 터치했었는가?
        # 조건: Low <= EMA * (1 + 0.5%)
        dip_threshold = ema_value * (1 + self.dip_tolerance)
        is_dip = prev_low <= dip_threshold
        
        # 2. 반등(Rebound) 확인: 하지만 종가는 EMA 위(혹은 근처)에서 마감했는가?
        # 조건: Close >= EMA * (1 - 0.2%)
        # 이 조건이 '하락 돌파'와 '지지 반등'을 구분하는 핵심 필터입니다.
        rebound_threshold = ema_value * (1 - self.hover_tolerance)
        is_rebound = prev_close >= rebound_threshold
        
        # [디버깅용 로그] (필요 시 주석 해제)
        # self.logger.debug(f"🔍 {ticker} | Low:{prev_low} vs Dip:{dip_threshold:.2f} | Close:{prev_close} vs Reb:{rebound_threshold:.2f}")

        # [새로 작성] 
        # 1. Dip(눌림) 조건: T-1 봉이 '음봉'이면서 저가가 EMA를 찍었어야 함
        # (백테스팅: Low <= EMA * 1.005)
        ema_prev = dip_candle['EMA']
        is_dip = (dip_candle['close'] < dip_candle['open']) and \
                 (dip_candle['low'] <= ema_prev * (1 + self.dip_tolerance))

        # 2. Rebound(지지) 조건: T 봉(방금 마감)은 EMA 위에서 종가 마감했어야 함
        # (백테스팅: Close >= EMA * 0.998)        
        ema_curr = confirm_candle['EMA']
        is_hold = confirm_candle['close'] >= ema_curr * (1 - self.hover_tolerance)

        # [최종 판단]
        if is_dip and is_hold:
            return {
                'price': current_market_price,
                'stop_loss': current_market_price * (1 - self.sl_pct),
                'target_price': current_market_price * (1 + self.tp_pct),
                # 로그에 이유를 명확히 남김 (Red Dip -> Green Hold)
                'reason': f"EMA_PATTERN (Dip:Low${dip_candle['low']:.2f} -> Hold:Close${confirm_candle['close']:.2f})"
            }

        return None
    
    def check_exit_signal(self, current_price, entry_price, entry_time=None):
        """
        매도 신호 확인
        [추가된 로직] 타임 컷 (Time Cut): 진입 후 4시간 경과 시 청산
        """
        if current_price <= 0 or entry_price <= 0:
            return None

        # 수익률 계산
        pnl_pct = (current_price - entry_price) / entry_price

        # -----------------------------------------------------------
        # 🕒 [Time Cut] 좀비 트레이딩 방지 (핵심 로직)
        # -----------------------------------------------------------
        if entry_time is not None:
            now_et = self._get_current_et_time()
            
            # entry_time이 문자열이거나 타임존 정보가 없을 경우 안전하게 변환
            if isinstance(entry_time, str):
                try:
                    entry_time = pd.to_datetime(entry_time)
                except:
                    pass # 변환 실패 시 타임컷 무시
            
            # datetime 객체인지 확인 후 계산
            if isinstance(entry_time, datetime.datetime):
                # entry_time에 타임존이 없으면 ET로 가정하고 설정
                if entry_time.tzinfo is None:
                    entry_time = pytz.timezone('US/Eastern').localize(entry_time)
                
                # 시간 차이 계산 (분 단위)
                time_diff = now_et - entry_time
                minutes_held = time_diff.total_seconds() / 60
                
                # 4시간(240분) 초과 시 무조건 매도
                if minutes_held >= self.max_holding_minutes:
                    return {
                        'type': 'SELL',
                        'reason': f"TIME_CUT (보유 {int(minutes_held)}분 > {self.max_holding_minutes}분)"
                    }

        # -----------------------------------------------------------
        # A. [익절] Target Profit (10%)
        # -----------------------------------------------------------
        if pnl_pct >= self.tp_pct:
            return {
                'type': 'SELL',
                'reason': f"TAKE_PROFIT ({pnl_pct*100:.2f}% >= {self.tp_pct*100:.1f}%)"
            }

        # -----------------------------------------------------------
        # B. [손절] Stop Loss (-40%)
        # -----------------------------------------------------------
        if pnl_pct <= -self.sl_pct:
            return {
                'type': 'SELL',
                'reason': f"STOP_LOSS ({pnl_pct*100:.2f}%)"
            }

        return None
# Factory 함수
def get_strategy():
    return EmaStrategy()