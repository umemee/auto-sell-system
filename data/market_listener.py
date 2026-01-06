import time
from infra.utils import get_logger

class MarketListener:
    def __init__(self, kis_api):
        self.kis = kis_api
        self.logger = get_logger("Scanner")
        # [NEW] 더 이상 고정 리스트를 쓰지 않습니다.
        # 하지만 API 랭킹 조회가 실패할 경우를 대비해 주요 종목은 남겨둘 수 있습니다.
        self.backup_symbols = ['TSLA', 'NVDA', 'AMD', 'TQQQ', 'SOXL']
        
    def scan_markets(self):
        """
        [업그레이드] 실시간 급등주 랭킹 검색
        기준: 등락률 40% 이상인 종목 자동 포착
        """
        detected_stocks = []
        THRESHOLD = 40.0 
        
        try:
            # 1. API를 통해 실시간 등락률 상위 종목 조회
            rank_data = self.kis.get_ranking()
            
            if rank_data:
                for item in rank_data:
                    sym = item.get('symb')
                    try:
                        rate = float(item.get('rate', 0)) # 등락률
                    except:
                        rate = 0.0
                    
                    # 2. 40% 이상 급등주 필터링
                    if rate >= THRESHOLD:
                        # self.logger.info(f"🚨 [급등 포착] {sym} (+{rate}%)")
                        detected_stocks.append(sym)
            else:
                # 랭킹 조회 실패 시 백업 로직 (기존 방식)
                # self.logger.warning("랭킹 조회 실패. 백업 리스트 사용.")
                pass

        except Exception as e:
            self.logger.error(f"Scanner Error: {e}")

        # 중복 제거 후 반환
        return list(set(detected_stocks))