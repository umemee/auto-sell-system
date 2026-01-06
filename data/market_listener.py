import logging
from typing import List
from infra.kis_api import KisApi 

class MarketListener:
    def __init__(self, kis_api: KisApi):
        self.kis = kis_api
        self.logger = logging.getLogger("MarketListener")
        self.target_symbols = [] 
        self.current_targets = []
        
        # ETF 등 잡주 필터는 유지 (이건 필수)
        self.etf_keywords = ['ETF', 'ETN', 'BULL', 'BEAR', '2X', '3X', 'ULTRA', 'PROSHARES']

    def _is_garbage(self, name: str) -> bool:
        name_upper = name.upper()
        for kw in self.etf_keywords:
            if kw in name_upper: return True
        return False
        
    def get_current_targets(self):
        return self.current_targets

    def scan_markets(self, min_change=40.0) -> List[str]: # 기본값 40
        """
        급등주 스캔 (넓은 뜰채 전략)
        """
        try:
            # 1. 랭킹 데이터 가져오기
            raw_list = self.kis.get_ranking(sort_type="fluct") 
            
            # [디버그] API가 실제로 몇 개를 줬는지 확인
            if not raw_list:
                self.logger.info("💨 스캔 결과: API가 빈 리스트를 반환했습니다.")
                self.current_targets = []
                return []
            
            # self.logger.info(f"🔍 API Raw Data Count: {len(raw_list)}") # 너무 시끄러우면 주석

            candidates = []
            for item in raw_list:
                symb = item.get("symb")
                name = item.get("name", "")
                
                try:
                    price = float(item.get("last", 0))
                    rate = float(item.get("rate", 0))
                    vol = int(item.get("vol", 0))
                except:
                    continue

                # [필터 완화]
                # 1. 가격: 최소한의 상장 요건 ($0.1) 이상이면 통과
                if price < 0.1: continue
                
                # 2. 거래량: 아예 5만 아니면 통과 (초기 급등 포착)
                if vol <= 5: continue
                
                # 3. 급등: min_change(40%) 이상이면 통과
                if rate < min_change: continue
                
                # 4. ETF 필터 (이건 유지)
                if self._is_garbage(name): continue

                candidates.append(symb)

            # 상위 10개 후보 선정
            final_targets = candidates[:10]
            self.current_targets = final_targets
            
            if final_targets:
                self.logger.info(f"📡 뜰채 포착 (>{min_change}%): {final_targets}")
            else:
                # 조건에 맞는게 하나도 없으면 로그 남김
                self.logger.info(f"💨 뜰채 빈손 (API 수신 {len(raw_list)}개 중 조건 만족 0개)")
                
            return final_targets

        except Exception as e:
            self.logger.error(f"Scan Error: {e}")
            self.current_targets = []
            return []