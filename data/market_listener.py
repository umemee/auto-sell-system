# data/market_listener.py
import logging
from typing import List, Dict
from infra.kis_api import KisApi 

class MarketListener:
    def __init__(self, kis_api: KisApi):
        self.kis = kis_api
        self.logger = logging.getLogger("MarketListener")
        self.target_symbols = [] 

        # 스캐닝 조건 (Scanner.py에서 가져옴)
        self.min_price = 0.5        # 최소 주가
        self.max_price = 50.0       # 최대 주가
        self.min_change = 5.0       # 최소 등락률 (5% 이상)
        self.min_volume = 10000     # 최소 거래량

    def scan_for_candidates(self) -> List[str]:
        """
        [Discovery] 시장 급등주 탐색 (Ranking 조회)
        KIS API의 등락률 순위 정보를 가져와서 1차 필터링 수행
        """
        try:
            # 1. 등락률 순위 가져오기 (기존 scanner.py 로직 계승)
            # infra/kis_api.py의 get_ranking 함수 활용
            raw_list = self.kis.get_ranking(sort_type="fluct") 
            
            if not raw_list:
                return []

            candidates = []
            for item in raw_list:
                symb = item.get("symb")
                
                # 데이터 정제
                try:
                    price = float(item.get("last", 0))
                    rate = float(item.get("rate", 0))
                    vol = int(item.get("vol", 0))
                except:
                    continue

                # 2. 기본 필터링 (동전주 제외, 거래량 부족 제외)
                if not (self.min_price <= price <= self.max_price): continue
                if vol < self.min_volume: continue
                if rate < self.min_change: continue
                
                # ETF 제외 (옵션)
                # if "ETF" in item.get("name", "").upper(): continue

                candidates.append(symb)

            # 상위 10개만 집중 감시
            final_targets = candidates[:10]
            
            if final_targets:
                self.logger.info(f"📡 New Candidates Found: {final_targets}")
                self.set_targets(final_targets) # 감시 대상 업데이트
                
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
                pass # 조회 실패는 로그 생략 (너무 많음)
        return market_data