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
        매수 신호 확인
        [추가된 로직] 오전 10시(ET) 이후 진입 금지
        """
        if df.empty or len(df) < self.ma_length + 2:
            return None

        # -----------------------------------------------------------
        # 🚫 [Time Filter] 진입 시간 제한 (10:00 AM ET 이후 금지)
        # -----------------------------------------------------------
        now_et = self._get_current_et_time()
        # 정규장 시작(09:30) 이후 10시가 넘었는지 체크
        # (프리마켓 04:00 ~ 09:30은 진입 허용)
        if now_et.hour >= self.entry_deadline:
            # self.logger.debug(f"⏳ [Time Limit] {ticker} 진입 불가 (Current {now_et.strftime('%H:%M')} >= Limit {self.entry_deadline}:00)")
            return None

        # -----------------------------------------------------------
        # [기존 로직] EMA 및 캔들 패턴 분석
        # -----------------------------------------------------------
        # 데이터 전처리
        df = df.copy()
        df['EMA'] = df['close'].ewm(span=self.ma_length, adjust=False).mean()
        
        last_row = df.iloc[-1]
        prev_row = df.iloc[-2]
        
        current_price = last_row['close']
        ema_value = last_row['EMA']
        
        # 1. 과열 종목 필터링 (전일 종가 대비 80% 이상 폭등 시 제외)
        try:
            # 전일 종가를 구하기 위해 일봉 데이터가 필요하지만, 
            # 여기서는 분봉 데이터 내에서 대략적인 시가(Open) 대비 상승률로 대체하거나
            # market_listener에서 이미 필터링된 종목임을 가정합니다.
            pass 
        except:
            pass

        # 2. 눌림목(Dip) 확인: 가격이 EMA 근처까지 내려왔는가?
        # EMA보다 살짝 낮거나(Dip), 아주 살짝 높은(Hover) 구간
        dip_threshold = ema_value * (1 + self.dip_tolerance)  # EMA + 0.5%
        
        # 이전 캔들의 저가가 EMA 근처였는지 확인
        prev_low = prev_row['low']
        is_dip = prev_low <= dip_threshold
        
        # 3. 반등(Rebound) 확인: 현재가가 다시 EMA 위로 올라가거나 지지받는가?
        # 현재가는 EMA - 0.2% 보다는 높아야 함 (너무 깊게 빠진 건 제외)
        rebound_threshold = ema_value * (1 - self.hover_tolerance)
        is_rebound = current_price >= rebound_threshold
        
        # 4. 거래량 확인 (직전 5개봉 평균보다 튀었는지 확인 - 선택사항)
        # vol_ma = df['volume'].iloc[-6:-1].mean()
        # is_vol_up = last_row['volume'] > vol_ma
        
        if is_dip and is_rebound:
            return {
                'price': current_price,
                'stop_loss': current_price * (1 - self.sl_pct),
                'target_price': current_price * (1 + self.tp_pct),
                'reason': f"EMA Dip & Rebound (P:${current_price:.2f} > EMA:${ema_value:.2f})"
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
