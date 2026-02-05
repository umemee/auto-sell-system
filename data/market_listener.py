# data/market_listener.py
import logging
import os
from infra.utils import get_logger
from config import Config

class MarketListener:
    def __init__(self, kis_api):
        self.kis = kis_api
        self.logger = get_logger("Scanner")
        
        # [DEBUG] 디버깅용 로거 별도 생성 (파일 분리)
        self.debug_logger = logging.getLogger("ScannerDebug")
        self.debug_logger.setLevel(logging.DEBUG)
        
        # logs 폴더 확인 및 생성
        log_dir = os.path.join(os.getcwd(), "logs")
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
            
        file_handler = logging.FileHandler(os.path.join(log_dir, "debug_scanner.log"), encoding='utf-8')
        file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
        
        # 기존 핸들러 제거 후 추가 (중복 방지)
        if self.debug_logger.hasHandlers():
            self.debug_logger.handlers.clear()
        self.debug_logger.addHandler(file_handler)

    def scan_markets(self, ban_list=None, active_candidates=None):
        """
        [실시간 급등주 검색 v5.5 - Debug Edition]
        - 탈락 사유(Filter Reject)를 별도 로그파일에 기록
        - 급등률(Threshold)을 만족했으나 필터에 걸린 '아까운 종목'만 기록
        """
        if ban_list is None: ban_list = set()
        if active_candidates is None: active_candidates = set()

        detected_stocks = []
        
        # 1. Config 로드
        THRESHOLD = getattr(Config, 'MIN_CHANGE_PCT', 42.0)
        MAX_THRESHOLD = getattr(Config, 'MAX_CHANGE_PCT', 300.0)
        MIN_P = getattr(Config, 'FILTER_MIN_PRICE', 0.5)
        MAX_P = getattr(Config, 'FILTER_MAX_PRICE', 50.0)
        MIN_VAL = getattr(Config, 'FILTER_MIN_TX_VALUE', 50000)
        BLACKLIST = getattr(Config, 'BLACKLIST_KEYWORDS', [])

        try:
            rank_data = self.kis.get_ranking()
            if not rank_data: return []

            for item in rank_data:
                sym = item.get('symb')
                if sym in ban_list: continue # 밴 종목은 조용히 스킵

                name = item.get('name', '').upper()
                
                try:
                    rate = float(item.get('rate', 0))
                    price = float(item.get('last') or item.get('price') or item.get('stck_prpr') or 0)
                    vol = float(item.get('tvol') or item.get('volume') or item.get('avol') or item.get('acml_vol') or 0)
                except (ValueError, TypeError):
                    continue 

                # =========================================================
                # 🔍 [Smart Logging] 잠재적 후보군 집중 감시
                # =========================================================
                # 급등률 조건은 만족했으나, 다른 필터에서 떨어질 놈들을 추적
                is_potential_candidate = (rate >= THRESHOLD)

                # 1. SPAC/접미사 필터
                if len(sym) >= 5 and sym[-1] in ['U', 'W', 'R', 'Q', 'P']:
                    if is_potential_candidate:
                        self.debug_logger.debug(f"🚫 [FILTER:Suffix] {sym} (+{rate}%) - SPAC/Warrant 제외")
                    continue
                
                # 2. 키워드 필터
                if any(k in name for k in BLACKLIST):
                    if is_potential_candidate:
                        self.debug_logger.debug(f"🚫 [FILTER:Keyword] {sym} ({name}) - 금지어 포함")
                    continue

                # 3. 과열(Max Threshold) 필터
                if rate > MAX_THRESHOLD:
                    if is_potential_candidate:
                        self.debug_logger.debug(f"🚫 [FILTER:Overheat] {sym} (+{rate}%) - 과열(>{MAX_THRESHOLD}%) 제외")
                    continue

                # 4. 가격(Price) 필터
                if not (MIN_P <= price <= MAX_P):
                    if is_potential_candidate:
                        self.debug_logger.debug(f"🚫 [FILTER:Price] {sym} (${price}) - 가격 범위({MIN_P}~{MAX_P}) 이탈")
                    continue
                
                # 전일 종가 계산 (출신 성분)
                prev_close = price / (1 + (rate / 100.0)) if rate > -99.0 else 0.0
                if prev_close < MIN_P:
                    if is_potential_candidate:
                         self.debug_logger.debug(f"🚫 [FILTER:Penny] {sym} (Prev ${prev_close:.2f}) - 동전주 출신 제외")
                    continue 
                
                # 5. 거래대금(Value) 필터
                trade_value = price * vol
                if trade_value < MIN_VAL:
                    if is_potential_candidate:
                         self.debug_logger.debug(f"🚫 [FILTER:Value] {sym} (${trade_value:,.0f}) - 거래대금 부족(<{MIN_VAL})")
                    continue

                # =========================================================
                # ✅ 최종 선정 (All Pass)
                # =========================================================
                if rate >= THRESHOLD:
                    if sym not in active_candidates:
                        self.logger.info(
                            f"🚨 [급등 포착] {sym} ({name}) (+{rate}%) "
                            f"| Price ${price} "
                            f"| Val ${trade_value/1000:,.0f}k"
                        )
                    detected_stocks.append(sym)

        except Exception as e:
            self.logger.debug(f"Scanner Loop Warning: {e}")

        return list(set(detected_stocks))