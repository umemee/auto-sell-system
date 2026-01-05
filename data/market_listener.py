import logging
from typing import List
from infra.kis_api import KisApi 

class MarketListener:
    def __init__(self, kis_api: KisApi):
        self.kis = kis_api
        self.logger = logging.getLogger("MarketListener")
        self.target_symbols = [] 
        # [NEW] 외부 조회용 최신 타겟 리스트 저장소
        self.current_targets = []
        
        self.etf_keywords = ['ETF', 'ETN', 'BULL', 'BEAR', '2X', '3X', 'ULTRA', 'PROSHARES']

    def _is_garbage(self, name: str) -> bool:
        name_upper = name.upper()
        for kw in self.etf_keywords:
            if kw in name_upper: return True
        return False
        
    # [NEW] 현재 감시 중인 종목 리스트 반환
    def get_current_targets(self):
        return self.current_targets

    def scan_markets(self, min_change=40.0) -> List[str]:
        """
        급등주 스캔 (메서드명: scan_markets)
        """
        try:
            raw_list = self.kis.get_ranking(sort_type="fluct") 
            if not raw_list: 
                self.current_targets = []
                return []

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

                # 1. 가격 필터
                if not (0.5 <= price <= 200.0): continue
                # 2. 거래량 필터
                if vol < 1000: continue
                # 3. 급등 필터
                if rate < min_change: continue
                
                # 4. ETF 필터
                if self._is_garbage(name): continue

                candidates.append(symb)

            final_targets = candidates[:10]
            
            # [NEW] 최신 타겟 업데이트 (외부 조회용)
            self.current_targets = final_targets
            
            if final_targets:
                self.logger.info(f"📡 Found Targets (>= {min_change}%): {final_targets}")
                
            return final_targets

        except Exception as e:
            self.logger.error(f"Scan Error: {e}")
            self.current_targets = [] # 에러 시 빈 리스트
            return []