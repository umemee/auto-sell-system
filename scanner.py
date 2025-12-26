# scanner.py
import time
import pandas as pd
from config import Config
from utils import get_logger

logger = get_logger()

class MarketScanner:
    def __init__(self, api):
        self.api = api
        # 스캔 설정 (Specification 반영)
        self.min_price = 1.0     # $1
        self.max_price = 15.0    # $15 (예산 $30 고려)
        self.min_volume = 1000000 # 100만 주
        self.min_change = 10.0   # 10% 이상 상승
        self.max_gap_pct = 3.0   # 이격도 3% 이내

    def scan_and_filter(self):
        """
        [Smart Filter Logic]
        1. Ranking API로 후보군 20개 추출
        2. Universe Filter (가격, 거래량)
        3. Setup Filter (변동성, 추세, 이격도)
        """
        logger.info("📡 [Scanner] 종목 스캐닝 시작...")
        
        # 1. Universe Filter (Ranking API)
        # 상승률 상위(change) or 거래량 상위(vol) 선택 가능. 여기선 거래량 기반으로 조회
        raw_list = self.api.get_ranking(sort_type="vol") 
        if not raw_list:
            logger.warning("스캔 결과가 없습니다.")
            return []

        candidates = []
        
        # 2. 1차 필터링 (기본 제원)
        for item in raw_list[:30]: # 상위 30개만 검사
            try:
                symbol = item['symb']
                price = float(item['last']) # 현재가
                vol = int(item['tvol'])     # 거래량
                rate = float(item['rate'])  # 등락률
                
                # (1) 가격 필터 ($1 ~ $15)
                if not (self.min_price <= price <= self.max_price):
                    continue
                    
                # (2) 거래량 필터 (>100만)
                if vol < self.min_volume:
                    continue

                # (3) 변동성 필터 (>10% 상승) - Specification A.
                if rate < self.min_change:
                    continue
                
                candidates.append(symbol)
                
            except Exception:
                continue
        
        logger.info(f"🔍 1차 필터 통과: {len(candidates)}개 -> {candidates}")
        
        # 3. 2차 필터링 (정밀 차트 분석)
        final_targets = []
        
        for symbol in candidates:
            # API 호출 제한 준수 (너무 빠르면 차단됨)
            time.sleep(0.5)
            
            # 차트 데이터 수신 (5분봉 기준 추세 확인이 유리)
            df_5m = self.api.get_candles(Config.EXCHANGE_CD, symbol, "5M")
            if df_5m.empty or len(df_5m) < 100:
                continue
                
            # 지표 계산
            df_5m['EMA_200'] = df_5m['close'].ewm(span=200, adjust=False).mean()
            
            current_price = df_5m['close'].iloc[-1]
            ema_200 = df_5m['EMA_200'].iloc[-1]
            
            # 지표 미생성 시 스킵
            if pd.isna(ema_200):
                continue
            
            # Specification B. Trend Alignment (Price > 200 EMA)
            if current_price <= ema_200:
                # logger.debug(f"Drop {symbol}: 역배열 (Price < EMA200)")
                continue
                
            # Specification D. Gap Distance (이격도 < 3.0%)
            # 눌림목이 와야 하므로, 이평선과 너무 멀면(급등 상태면) 제외
            gap_pct = ((current_price - ema_200) / ema_200) * 100
            
            if gap_pct > self.max_gap_pct:
                # logger.debug(f"Drop {symbol}: 이격도 과다 (+{gap_pct:.2f}%)")
                continue
            
            # Specification C. Volume Spike (간이 계산)
            # 최근 거래량이 평균보다 터졌는지 확인 (여긴 1분봉이 더 정확하나 5분봉으로 대체)
            avg_vol = df_5m['volume'].tail(20).mean()
            last_vol = df_5m['volume'].iloc[-1]
            
            if last_vol < avg_vol * 1.5: # 기준을 약간 완화 (1.5배)
                continue
                
            logger.info(f"🎯 [Target Found] {symbol} | Gap: {gap_pct:.2f}% | Price: ${current_price}")
            final_targets.append(symbol)
            
            # 최대 3개까지만 찾으면 종료 (집중 투자)
            if len(final_targets) >= 3:
                break
                
        return final_targets