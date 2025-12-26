# scanner.py
import time
import pandas as pd
from config import Config
from utils import get_logger

logger = get_logger()

class MarketScanner:
    def __init__(self, api):
        self.api = api
        self.min_price = 1.0
        self.max_price = 30.0   # 예산에 맞춰 조정 ($30)
        self.min_volume = 1000000 
        self.min_change = 5.0   # 5% 이상 상승
        self.max_gap_pct = 5.0  # 이격도 5% 이내 (조금 완화)

        # [비상용] API 랭킹 조회 실패 시 사용할 "기본 유니버스 (Top Volatility Stocks)"
        self.fallback_symbols = [
            'TSLA', 'NVDA', 'AMD', 'PLTR', 'SOXL', 'TQQQ', 'SQQQ', 'MARA', 'COIN', 'RIVN',
            'LCID', 'IONQ', 'JOBY', 'AI', 'MSTR', 'GME', 'AMC', 'UPST', 'AFRM', 'DKNG'
        ]

    def scan_and_filter(self):
        logger.info("📡 [Scanner] 종목 스캐닝 시작...")
        
        candidates = []
        raw_list = self.api.get_ranking(sort_type="vol")
        
        # [Safety 1] API 랭킹 조회가 실패하면? -> 비상용 리스트 사용
        if not raw_list:
            logger.warning(f"⚠️ 랭킹 API 실패. [비상용 리스트 {len(self.fallback_symbols)}개]를 대신 스캔합니다.")
            # 비상용 리스트를 API 형식처럼 가짜로 만들어서 candidates에 넣음
            for sym in self.fallback_symbols:
                candidates.append(sym)
        else:
            # API가 성공했으면 필터링 진행
            for item in raw_list[:30]:
                try:
                    price = float(item['last'])
                    if not (self.min_price <= price <= self.max_price): continue
                    candidates.append(item['symb'])
                except:
                    continue
        
        # [Safety 2] 후보가 없으면 종료
        if not candidates:
            logger.warning("스캔 후보가 없습니다.")
            return []

        logger.info(f"🔍 2차 정밀 분석 대상: {len(candidates)}개")
        
        final_targets = []
        
        # 2차 필터링 (차트 분석)
        for symbol in candidates:
            time.sleep(0.2) # API 보호
            
            # 캔들 조회
            df_5m = self.api.get_candles(Config.EXCHANGE_CD, symbol, "5M")
            if df_5m.empty or len(df_5m) < 50:
                continue
                
            # 지표 계산
            df_5m['EMA_200'] = df_5m['close'].ewm(span=200, adjust=False).mean()
            current_price = df_5m['close'].iloc[-1]
            ema_200 = df_5m['EMA_200'].iloc[-1]
            
            if pd.isna(ema_200): continue
            
            # 필터 로직
            # 1. 정배열 (Price > 200 EMA)
            if current_price <= ema_200: continue
            
            # 2. 이격도 (Gap)
            gap_pct = ((current_price - ema_200) / ema_200) * 100
            if gap_pct > self.max_gap_pct: continue
            
            logger.info(f"🎯 [Target Found] {symbol} | Gap: {gap_pct:.2f}% | Price: ${current_price}")
            final_targets.append(symbol)
            
            if len(final_targets) >= 3: break
                
        return final_targets