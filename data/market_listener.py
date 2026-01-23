from infra.utils import get_logger
from config import Config

class MarketListener:
    def __init__(self, kis_api):
        self.kis = kis_api
        self.logger = get_logger("Scanner")
        self.current_watchlist = [] # [상태 표시용] 현재 감시 중인 종목 리스트
        
    def scan_markets(self):
        """
        [실시간 급등주 검색]
        Config의 필터 설정을 엄격히 따릅니다.
        """
        detected_stocks = []
        
        # Config 로드 (값이 없으면 기본값 사용)
        THRESHOLD = getattr(Config, 'MIN_CHANGE_PCT', 42.0)
        MIN_P = getattr(Config, 'FILTER_MIN_PRICE', 0.5)
        MAX_P = getattr(Config, 'FILTER_MAX_PRICE', 50.0)
        MIN_VAL = getattr(Config, 'FILTER_MIN_TX_VALUE', 1000000)

        try:
            # API 호출 (랭킹 데이터)
            rank_data = self.kis.get_ranking()
            
            if not rank_data:
                # 데이터가 없으면 조용히 리턴 (로그 과다 방지)
                return []

            for item in rank_data:
                sym = item.get('symb')
                name = item.get('name', '').upper()
                
                try:
                    # 데이터 파싱 및 안전한 형변환
                    rate = float(item.get('rate', 0)) # 등락률
                    
                    # API 응답 필드명이 상황따라 다를 수 있어 유연하게 대처
                    price_raw = item.get('last') or item.get('price') or 0
                    price = float(price_raw)
                    
                    vol_raw = item.get('vol') or item.get('volume') or 0
                    vol = float(vol_raw)
                    
                except (ValueError, TypeError):
                    continue # 숫자가 아니면 스킵

                # ==========================================
                # 🛑 1. 필터링 (쓰레기 종목 제외)
                # ==========================================
                
                # 티커 필터 (워런트, 유닛 등)
                if len(sym) >= 5 and sym[-1] in ['U', 'W', 'R', 'Q', 'P']:
                    continue
                    
                # 이름 필터
                exclude_keywords = ['WARRANT', 'UNIT', 'SPAC', 'RIGHTS', 'NOTE', 'DEBENTURE']
                if any(k in name for k in exclude_keywords):
                    continue

                # ==========================================
                # 🛑 2. 조건 필터 (가격 & 유동성)
                # ==========================================
                
                # 가격 ($0.5 ~ $50)
                if not (MIN_P <= price <= MAX_P):
                    continue

                # 거래대금 (새벽엔 이게 제일 큰 장벽입니다)
                trade_value = price * vol
                if trade_value < MIN_VAL:
                    continue

                # ==========================================
                # ✅ 3. 선정
                # ==========================================
                if rate >= THRESHOLD:
                    # 중복 로그 방지: 이미 감시 리스트에 없던 것만 로그 출력
                    if sym not in self.current_watchlist:
                        self.logger.info(f"🚨 [급등 포착] {sym} (+{rate}%) | ${price} | 거래액 ${trade_value:,.0f}")
                    detected_stocks.append(sym)

        except Exception as e:
            # 치명적이지 않은 에러는 경고만 하고 넘어감
            self.logger.debug(f"Scanner Loop Warning: {e}")

        return list(set(detected_stocks))
