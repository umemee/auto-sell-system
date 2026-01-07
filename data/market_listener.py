from infra.utils import get_logger
from config import Config  # [수정] Config 임포트 추가

class MarketListener:
    def __init__(self, kis_api):
        self.kis = kis_api
        self.logger = get_logger("Scanner")
        # [NEW] 더 이상 고정 리스트를 쓰지 않습니다.
        self.backup_symbols = ['TSLA', 'NVDA', 'AMD', 'TQQQ', 'SOXL']
        
    def scan_markets(self):
        """
        [업그레이드] 실시간 급등주 랭킹 검색
        기준: Config.MIN_CHANGE_PCT (기본 40%) 이상 급등주 포착
        """
        detected_stocks = []
        # [수정] 하드코딩(40.0) 제거 -> Config 변수 사용 #
        THRESHOLD = Config.MIN_CHANGE_PCT 
        
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
                    
                    # [수정] 워런트(W) 및 파생상품 필터링 (5글자 이상 W로 끝남 or 이름에 워런트)
                    name = item.get('name', '').upper() # 수정
                    if (len(sym) >= 5 and sym.endswith('W')) or 'WARRANT' in name or '워런트' in name: # 수정
                        continue # 수정

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