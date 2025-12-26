# scanner.py
import time
import pandas as pd
import numpy as np
from config import Config
from utils import get_logger

logger = get_logger()

class MarketScanner:
    def __init__(self, api):
        self.api = api
        # [Scanner Spec: Universe Filter]
        self.min_price = 0.2        # 최소 주가 $0.2
        self.max_price = 15.0       # 최대 주가 $15 (자본 $30 고려)
        self.min_volume = 1000000   # 최소 거래량 100만 주
        self.min_change = 10.0      # 당일 상승률 10% 이상 (주도주 판별)
        
        # [Filter: ETF/ETN Keywords to Exclude]
        self.etf_keywords = ['ETF', 'ETN', 'BULL', 'BEAR', '2X', '3X', 'ULTRA', 'PROSHARES']

    def is_etf(self, name):
        """종목명에 ETF 관련 키워드가 있는지 확인"""
        name_upper = name.upper()
        for kw in self.etf_keywords:
            if kw in name_upper:
                return True
        return False

    def scan_and_filter(self):
        logger.info("📡 [Scanner] 스마트 필터링 가동 (Logic: Gap-Zone Scalping)...")
        
        # 1. 랭킹 데이터 가져오기 (상승률 상위)
        raw_list = self.api.get_ranking(sort_type="fluct") # API 수정됨 (상승률 기준)
        
        if not raw_list:
            logger.warning("⚠️ 랭킹 데이터를 받아오지 못했습니다.")
            return []

        candidates = []
        
        # 2. [Step 1] Universe Filter (1차 필터링)
        for item in raw_list:
            try:
                symb = item.get('symb')
                name = item.get('name', '')
                price = float(item.get('last', 0))
                rate = float(item.get('rate', 0))
                vol = int(item.get('tvol', 0))
                
                # A. 가격 필터 ($1 ~ $15)
                if not (self.min_price <= price <= self.max_price):
                    continue
                    
                # B. 거래량 필터 (> 100만 주)
                if vol < self.min_volume:
                    continue
                    
                # C. 상승률 필터 (> 10%) - Momentum Check
                if rate < self.min_change:
                    continue
                    
                # D. ETF/ETN 필터
                if self.is_etf(name):
                    continue
                
                candidates.append(symb)
                
            except Exception as e:
                continue
                
        logger.info(f"🔍 1차 필터 통과(Universe): {len(candidates)}개 종목 -> 정밀 차트 분석 시작")
        
        final_targets = []
        
        # 3. [Step 2] Setup Filter (2차 기술적 분석)
        for symbol in candidates[:10]: # API 호출 제한 고려, 상위 10개만 정밀 분석
            try:
                time.sleep(0.5) # API Rate Limit 보호
                
                # 5분봉 조회 (Trend Check용)
                df = self.api.get_candles(Config.EXCHANGE_CD, symbol, "5M")
                
                # 데이터 부족 시 스킵 (200 EMA 계산을 위해 최소 데이터 필요)
                if df is None or df.empty or len(df) < 120: 
                    continue
                
                # 지표 계산
                # (API 데이터 제한으로 120 EMA를 추세선으로 대용하여 최적화)
                df['EMA_120'] = df['close'].ewm(span=120, adjust=False).mean()
                df['Vol_Avg_20'] = df['volume'].rolling(window=20).mean()
                
                curr_close = df['close'].iloc[-1]
                curr_vol = df['volume'].iloc[-1]
                ema_120 = df['EMA_120'].iloc[-1]
                vol_avg = df['Vol_Avg_20'].iloc[-1]
                
                if pd.isna(ema_120) or pd.isna(vol_avg): continue

                # --- [Logic Specification 적용] ---
                
                # B. Trend Alignment (상승 추세 확인)
                # 정배열: 현재가가 이평선 위에 있어야 함
                if curr_close <= ema_120:
                    # logger.debug(f"Drop {symbol}: 역배열 (Price {curr_close} < EMA {ema_120:.2f})")
                    continue
                    
                # C. Volume Spike (거래량 급증)
                # 현재 거래량이 평균 거래량의 2배 이상 (또는 최근 3캔들 내 급증)
                # 엄격함을 위해 현재 캔들 기준으로 체크
                if curr_vol < (vol_avg * 2.0):
                    # logger.debug(f"Drop {symbol}: 거래량 부족")
                    continue
                    
                # D. Gap Distance (이격도 - The Zone)
                # 주가가 이평선에서 너무 멀지 않아야 함 (눌림목)
                # 괴리율 = |(가격 - EMA) / EMA| * 100
                gap_rate = abs((curr_close - ema_120) / ema_120) * 100
                
                if gap_rate > 3.0: # 3% 이상 벌어지면 추격매수로 간주하고 패스
                    # logger.debug(f"Drop {symbol}: 이격도 과열 ({gap_rate:.2f}%)")
                    continue
                
                # 모든 조건 통과
                logger.info(f"🎯 [Target Found] {symbol} | Price:${curr_close} | Gap:{gap_rate:.2f}% | Vol:Boom!")
                final_targets.append(symbol)
                
                if len(final_targets) >= 3: # 최대 3개까지만 집중 타겟팅
                    break
                    
            except Exception as e:
                logger.error(f"분석 중 에러 ({symbol}): {e}")
                continue
                
        if not final_targets:
            logger.info("💤 조건에 맞는 'The Zone' 진입 종목이 없습니다. 감시 대기.")
            
        return final_targets