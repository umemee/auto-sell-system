from infra.utils import get_logger
from config import Config  # [수정] Config 임포트

class MarketListener:
    def __init__(self, kis_api):
        self.kis = kis_api
        self.logger = get_logger("Scanner")
        # [NEW] 더 이상 고정 리스트를 쓰지 않습니다.
        self.backup_symbols = ['TSLA', 'NVDA', 'AMD', 'TQQQ', 'SOXL']
        
    def scan_markets(self):
        """
        [업그레이드] 실시간 급등주 랭킹 검색
        기준 1: Config.MIN_CHANGE_PCT (기본 42%) 이상 급등
        기준 2: 가격 $0.5 ~ $50.0
        기준 3: 거래대금 $1M 이상
        """
        detected_stocks = []
        THRESHOLD = Config.MIN_CHANGE_PCT 
        
        # [추가] 필터 기준값 로드
        MIN_P = getattr(Config, 'FILTER_MIN_PRICE', 0.5)
        MAX_P = getattr(Config, 'FILTER_MAX_PRICE', 50.0)
        MIN_VAL = getattr(Config, 'FILTER_MIN_TX_VALUE', 1000000)

        try:
            # 1. API를 통해 실시간 등락률 상위 종목 조회
            rank_data = self.kis.get_ranking()
            
            if rank_data:
                for item in rank_data:
                    sym = item.get('symb')
                    
                    # 데이터 파싱 (안전하게 처리)
                    try:
                        rate = float(item.get('rate', 0)) # 등락률
                        # [추가] 현재가 및 거래량 파싱
                        # KIS API 응답 키 확인 필요 ('last' 또는 'price' 등, 보통 rank 데이터엔 'price'나 'last'가 옴)
                        # 여기서는 안전하게 가져오도록 처리
                        price = float(item.get('last') or item.get('price') or 0) 
                        vol = float(item.get('vol') or item.get('volume') or 0)
                    except:
                        continue # 데이터 깨지면 스킵
                    
                    # ==========================================
                    # 🛑 1. 악성 종목 필터링 (SPAC Unit, Warrant, Rights 등)
                    # ==========================================
                    name = item.get('name', '').upper()

                    # 티커 접미사(Suffix) 체크
                    if len(sym) >= 5:
                        last_char = sym[-1]
                        if last_char in ['U', 'W', 'R', 'Q', 'P']:
                            continue

                    # 회사 이름(Name) 키워드 체크
                    exclude_keywords = [
                        'WARRANT', '워런트', 'UNIT', '유닛', 
                        'ACQUISITION', 'SPAC', 'RIGHTS', 
                        'FUND', 'NOTE', 'DEBENTURE'
                    ]
                    if any(keyword in name for keyword in exclude_keywords):
                        continue

                    # ==========================================
                    # 🛑 2. [NEW] 개잡주 필터링 (가격 & 유동성)
                    # ==========================================
                    
                    # 2-1. 가격 필터 ($0.5 ~ $50.0)
                    if not (MIN_P <= price <= MAX_P):
                        # self.logger.info(f"🚫 가격 부적합: {sym} (${price})") 
                        continue

                    # 2-2. 거래대금 필터 (최소 $1M)
                    # 거래대금 = 현재가 * 거래량 (근사치)
                    trade_value = price * vol
                    if trade_value < MIN_VAL:
                        # self.logger.info(f"🚫 유동성 부족: {sym} (${trade_value:,.0f})")
                        continue

                    # ==========================================
                    # ✅ 3. 급등주 최종 확인
                    # ==========================================
                    if rate >= THRESHOLD:
                        self.logger.info(f"🚨 [급등 포착] {sym} (+{rate}%) | ${price} | Vol: {vol:,.0f}")
                        detected_stocks.append(sym)

            else:
                pass

        except Exception as e:
            self.logger.error(f"Scanner Error: {e}")

        # 중복 제거 후 반환
        return list(set(detected_stocks))