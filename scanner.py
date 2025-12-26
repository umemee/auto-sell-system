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
        # [Universe Filter: 최소한의 자격 요건]
        self.min_price = 0.2        # 동전주 포함 (사용자 설정)
        self.max_price = 30.0       # 자본금 고려 ($30 이하)
        self.min_volume = 100000    # 거래량 기준 대폭 완화 (10만 주 이상이면 감시)
        self.min_change = 40.0       # 40% 이상만 오르면 일단 감시
        
        self.etf_keywords = ['ETF', 'ETN', 'BULL', 'BEAR', '2X', '3X', 'ULTRA', 'PROSHARES']

    def is_etf(self, name):
        name_upper = name.upper()
        for kw in self.etf_keywords:
            if kw in name_upper:
                return True
        return False

    def scan_and_filter(self):
        logger.info("📡 [Scanner] 광역 그물망 가동 (Wide-Net Scanning)...")
        
        # 1. 상승률 순위 가져오기
        raw_list = self.api.get_ranking(sort_type="fluct")
        
        if not raw_list:
            logger.warning("⚠️ 랭킹 데이터를 받아오지 못했습니다.")
            return []

        candidates = []
        
        # 2. [1차 필터] 기본 자격 요건 (Universe)
        for item in raw_list:
            try:
                symb = item.get('symb')
                name = item.get('name', '')
                price = float(item.get('last', 0))
                rate = float(item.get('rate', 0))
                vol = int(item.get('tvol', 0))
                
                # ETF 제외
                if self.is_etf(name): continue
                
                # 가격 조건 ($0.2 ~ $30)
                if not (self.min_price <= price <= self.max_price): continue
                    
                # 거래량 조건 (최소한의 유동성)
                if vol < self.min_volume: continue
                    
                # 상승률 조건 (최소 5% 이상)
                if rate < self.min_change: continue
                
                candidates.append({
                    'symbol': symb,
                    'price': price,
                    'rate': rate,
                    'vol': vol
                })
                
            except Exception:
                continue
        
        # 상위 20개 선정 (거래량 많은 순으로 정렬 후 자름)
        # 급등주 중에서도 그나마 거래가 활발한 놈을 우선순위로 둠
        candidates.sort(key=lambda x: x['vol'], reverse=True)
        top_candidates = candidates[:20]
        
        logger.info(f"🔍 1차 필터 통과: {len(top_candidates)}개 종목 (정밀 검사 생략, 즉시 등록)")
        
        final_targets = []
        
        # 3. [2차 필터] 기술적 지표 필터링 "제거" -> 무조건 담기
        # 스캐너는 감시만 하고, 매수 여부는 Strategy가 결정함
        for item in top_candidates:
            symbol = item['symbol']
            price = item['price']
            rate = item['rate']
            
            # [Relaxed Logic]
            # 이격도(Gap), 정배열(Trend) 체크 삭제.
            # 일단 감시 리스트에 넣어야 "폭등하는 놈"을 놓치지 않음.
            
            logger.info(f"🎯 [Target Found] {symbol} | Price:${price} | Rate:+{rate}%")
            final_targets.append(symbol)
            
            # 최대 10개까지만 감시 (시스템 부하 고려)
            if len(final_targets) >= 10:
                break
                
        if not final_targets:
            logger.info("💤 조건에 맞는 종목이 없습니다. 기준을 더 낮추시겠습니까?")
            
        return final_targets