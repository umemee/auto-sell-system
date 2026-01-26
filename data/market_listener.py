# data/market_listener.py
from infra.utils import get_logger
from config import Config

class MarketListener:
    def __init__(self, kis_api):
        self.kis = kis_api
        self.logger = get_logger("Scanner")
        # [상태 표시용] 현재 감시 중인 종목 리스트 (중복 로그 방지용)
        self.current_watchlist = [] 
        
    def scan_markets(self):
        """
        [실시간 급등주 검색 v5.3]
        - KIS API 호환성 강화 (거래량 키값 문제 해결)
        - '동전주 세탁' 방지 로직 추가 (전일 종가 기준 필터링)
        """
        detected_stocks = []
        
        # ---------------------------------------------------------
        # 1. Config 로드
        # ---------------------------------------------------------
        THRESHOLD = getattr(Config, 'MIN_CHANGE_PCT', 42.0)
        
        # [중요] 실전 필터링 기준
        MIN_P = getattr(Config, 'FILTER_MIN_PRICE', 0.5)
        MAX_P = getattr(Config, 'FILTER_MAX_PRICE', 50.0)
        
        # [Config 수정 필요] 프리마켓에서는 100만이 너무 큽니다. 
        # config.py에서 FILTER_MIN_TX_VALUE를 50000~100000 수준으로 낮추는 것을 권장합니다.
        MIN_VAL = getattr(Config, 'FILTER_MIN_TX_VALUE', 1000000)

        try:
            # API 호출 (랭킹 데이터) - kis_api의 스마트 재시도 로직이 보호해줌
            rank_data = self.kis.get_ranking()
            
            if not rank_data:
                return []

            for item in rank_data:
                sym = item.get('symb')
                name = item.get('name', '').upper()
                
                try:
                    # -----------------------------------------------------
                    # 2. 데이터 파싱 (안전장치 강화)
                    # -----------------------------------------------------
                    rate = float(item.get('rate', 0)) # 등락률
                    
                    # [FIX 1] Price Key: last, price, stck_prpr 등 다양한 키 대응
                    price = float(item.get('last') or item.get('price') or item.get('stck_prpr') or 0)
                    
                    # [FIX 2] Volume Key: vol, volume 외에 'avol', 'acml_vol' (누적거래량) 필수 체크
                    # 이 부분이 없어서 기존 코드에서 거래량이 0으로 잡혔습니다.
                    vol = float(item.get('tvol') or item.get('volume') or item.get('avol') or item.get('acml_vol') or 0)
                    
                except (ValueError, TypeError):
                    continue 

                # =========================================================
                # 🛡️ 3. 보안 필터 (Security Filter)
                # =========================================================
                
                # 3-1. 악성 종목(Ticker) 필터
                if len(sym) >= 5 and sym[-1] in ['U', 'W', 'R', 'Q', 'P']: continue
                
                # 3-2. 이름(Name) 필터
                exclude_keywords = ['WARRANT', '워런트', 'UNIT', '유닛', 'SPAC', 'RIGHTS', 'FUND', 'NOTE', 'DEBENTURE']
                if any(k in name for k in exclude_keywords): continue

                # =========================================================
                # 🛡️ 4. 로직 필터 (Strategic Filter) - 핵심 수정 사항
                # =========================================================

                # [FIX 3] "출신 성분 검증" (전일 종가 역산)
                # 현재가가 0.6불이라도, 어제 0.4불이었다면 '개잡주'로 판단하여 제외합니다.
                # 공식: 현재가 / (1 + 등락률/100)
                if rate > -99.0: # 0으로 나누기 방지
                    prev_close = price / (1 + (rate / 100.0))
                else:
                    prev_close = 0.0

                # 4-1. 가격 필터 (현재가 AND 전일종가 모두 만족해야 함)
                if not (MIN_P <= price <= MAX_P): continue
                if prev_close < MIN_P: continue  # 여기가 바로 '함정 방어' 구간입니다.

                # 4-2. 거래대금 필터
                trade_value = price * vol
                if trade_value < MIN_VAL: continue

                # =========================================================
                # ✅ 5. 최종 선정
                # =========================================================
                if rate >= THRESHOLD:
                    # [로그 최적화] 이미 보고 있던 종목이면 로그 생략
                    if sym not in self.current_watchlist:
                        self.logger.info(
                            f"🚨 [급등 포착] {sym} (+{rate}%) "
                            f"| Price ${price} (Prev ${prev_close:.2f}) "
                            f"| Val ${trade_value/1000:,.0f}k"
                        )
                    detected_stocks.append(sym)

        except Exception as e:
            # 치명적이지 않은 에러는 디버그 로그로만 남김
            self.logger.debug(f"Scanner Loop Warning: {e}")

        # 메인 루프에서 비교할 수 있도록 리스트 반환

        return list(set(detected_stocks))
