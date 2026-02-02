# data/market_listener.py
from infra.utils import get_logger
from config import Config

class MarketListener:
    def __init__(self, kis_api):
        self.kis = kis_api
        self.logger = get_logger("Scanner")
        # current_watchlist는 이제 main.py의 active_candidates와 연동되므로
        # 내부 상태보다는 로깅 제어용으로 사용됩니다.
        
    def scan_markets(self, ban_list=None, active_candidates=None):
        """
        [실시간 급등주 검색 v5.4]
        - ban_list 연동: 매매 금지 종목 즉시 스킵 (좀비 방지)
        - active_candidates 연동: 이미 포착된 종목 로그 중복 방지
        - Config 기반 강력한 SPAC 필터링 적용
        """
        # 안전장치: None으로 들어오면 빈 세트로 처리
        if ban_list is None: ban_list = set()
        if active_candidates is None: active_candidates = set()

        detected_stocks = []
        
        # 1. Config 로드
        THRESHOLD = getattr(Config, 'MIN_CHANGE_PCT', 42.0)
        MAX_THRESHOLD = getattr(Config, 'MAX_CHANGE_PCT', 300.0) # [추가] 상한선 로드
        MIN_P = getattr(Config, 'FILTER_MIN_PRICE', 0.5)
        MAX_P = getattr(Config, 'FILTER_MAX_PRICE', 50.0)
        MIN_VAL = getattr(Config, 'FILTER_MIN_TX_VALUE', 50000)
        
        # [v5.4] 블랙리스트 키워드 로드
        BLACKLIST = getattr(Config, 'BLACKLIST_KEYWORDS', [])

        try:
            # API 호출
            rank_data = self.kis.get_ranking()
            if not rank_data: return []

            for item in rank_data:
                sym = item.get('symb')
                
                # ---------------------------------------------------------
                # 🚫 [Zero-Step Filter] 좀비 및 밴 종목 즉시 차단
                # ---------------------------------------------------------
                # 이미 매매하고 끝난 종목(ban_list)은 데이터 파싱조차 하지 않고 버립니다.
                if sym in ban_list:
                    continue

                name = item.get('name', '').upper()
                
                try:
                    # 데이터 파싱
                    rate = float(item.get('rate', 0))
                    price = float(item.get('last') or item.get('price') or item.get('stck_prpr') or 0)
                    vol = float(item.get('tvol') or item.get('volume') or item.get('avol') or item.get('acml_vol') or 0)
                except (ValueError, TypeError):
                    continue 

                # =========================================================
                # 🛡️ [Security Filter] SPAC 및 악성 종목 차단
                # =========================================================
                # 1. 티커 접미사 필터 (5글자 이상이고 끝이 특수문자인 경우)
                if len(sym) >= 5 and sym[-1] in ['U', 'W', 'R', 'Q', 'P']: 
                    continue
                
                # 2. 정밀 키워드 필터 (ASPC 등 방어)
                # Config에 정의된 키워드가 이름에 포함되면 즉시 제외
                if any(k in name for k in BLACKLIST): 
                    continue

                # =========================================================
                # 🛡️ [Strategic Filter]
                # =========================================================
                # [추가] 너무 많이 오른 종목(300% 이상)은 제외
                if rate > MAX_THRESHOLD:
                    continue

                # "출신 성분 검증" (전일 종가 역산)
                if rate > -99.0:
                    prev_close = price / (1 + (rate / 100.0))
                else:
                    prev_close = 0.0

                # 가격 및 거래대금 필터
                if not (MIN_P <= price <= MAX_P): continue
                if prev_close < MIN_P: continue 
                
                trade_value = price * vol
                if trade_value < MIN_VAL: continue

                # =========================================================
                # ✅ 최종 선정 및 로깅 제어
                # =========================================================
                if rate >= THRESHOLD:
                    # [핵심] 이미 감시 중인 종목(active_candidates)이라면 로그를 찍지 않음
                    # 즉, "신규 발견"일 때만 로그를 남김
                    if sym not in active_candidates:
                        self.logger.info(
                            f"🚨 [급등 포착] {sym} ({name}) (+{rate}%) "  # <--- ({name}) 추가!
                            f"| Price ${price} (Prev ${prev_close:.2f}) "
                            f"| Val ${trade_value/1000:,.0f}k"
                        )
                    
                    detected_stocks.append(sym)

        except Exception as e:
            self.logger.debug(f"Scanner Loop Warning: {e}")

        return list(set(detected_stocks))