# data/market_listener.py - v3.1 Hybrid (Smart Filter Applied)
import logging
from typing import List, Dict
from infra.kis_api import KisApi 

class MarketListener:
    def __init__(self, kis_api: KisApi):
        self.kis = kis_api
        self.logger = logging.getLogger("MarketListener")
        self.target_symbols = [] 

        # 스캐닝 조건
        self.min_price = 0.5        
        self.max_price = 50.0       
        self.min_change = 5.0       
        self.min_volume = 10000     

        # [V2 Feature] ETF/ETN 및 레버리지 상품 필터링 키워드
        self.etf_keywords = ['ETF', 'ETN', 'BULL', 'BEAR', '2X', '3X', 'ULTRA', 'PROSHARES']

    def _is_garbage(self, name: str) -> bool:
        """[V2 Feature] 불필요한 종목(ETF, 스팩 등) 필터링"""
        name_upper = name.upper()
        for kw in self.etf_keywords:
            if kw in name_upper:
                return True
        return False

    def scan_for_candidates(self) -> List[str]:
        """
        [Discovery] 시장 급등주 탐색 + V2 스마트 필터링
        """
        try:
            # 1. 등락률 순위 가져오기
            raw_list = self.kis.get_ranking(sort_type="fluct") 
            
            if not raw_list:
                return []

            candidates = []
            for item in raw_list:
                symb = item.get("symb")
                name = item.get("name", "") # 종목명 확인
                
                # 데이터 정제
                try:
                    price = float(item.get("last", 0))
                    rate = float(item.get("rate", 0))
                    vol = int(item.get("vol", 0))
                except:
                    continue

                # 2. 기본 수치 필터링
                if not (self.min_price <= price <= self.max_price): continue
                if vol < self.min_volume: continue
                if rate < self.min_change: continue
                
                # 3. [V2 Feature] ETF/ETN 필터링 적용
                if self._is_garbage(name):
                    # self.logger.debug(f"🧹 Filtered: {symb} ({name})")
                    continue

                candidates.append(symb)

            # 상위 10개만 집중 감시
            final_targets = candidates[:10]
            
            if final_targets:
                self.logger.info(f"📡 New Candidates Found (Filtered): {final_targets}")
                self.set_targets(final_targets) 
                
            return final_targets

        except Exception as e:
            self.logger.error(f"Scan Error: {e}")
            return []

    def set_targets(self, symbols: List[str]):
        self.target_symbols = symbols

    def get_market_data(self) -> Dict[str, dict]:
        """현재 타겟 종목들의 상세 정보 조회"""
        market_data = {}
        for symbol in self.target_symbols:
            try:
                price_info = self.kis.get_current_price(symbol)
                if price_info:
                    market_data[symbol] = {
                        'price': float(price_info.get('last', 0)),
                        'open': float(price_info.get('open', 0)),
                        'vol': int(price_info.get('volume', 0))
                    }
            except Exception as e:
                pass 
        return market_data