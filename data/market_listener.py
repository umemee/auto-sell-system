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
                    
                    # ==========================================
                    # 🛑 악성 종목 필터링 (SPAC Unit, Warrant, Rights 등)
                    # ==========================================
                    name = item.get('name', '').upper()

                    # 1. 티커 접미사(Suffix) 체크
                    # 미국 주식(NASDAQ 등)은 5글자일 때 마지막 글자가 특수 유형을 의미함
                    if len(sym) >= 5:
                        last_char = sym[-1]
                        # U: Unit(스팩유닛), W: Warrant(워런트), R: Rights(신주인수권), Q: Bankruptcy(파산), P: Preferred(우선주)
                        if last_char in ['U', 'W', 'R', 'Q', 'P']:
                            # self.logger.info(f"🚫 필터링됨(유형): {sym} (사유: {last_char} type)")
                            continue

                    # 2. 회사 이름(Name) 키워드 체크
                    # SPAC(기업인수목적회사), 인수권, 펀드 등 제외
                    exclude_keywords = [
                        'WARRANT', '워런트',   # 워런트
                        'UNIT', '유닛',        # 유닛 (스팩 묶음)
                        'ACQUISITION',         # 스팩(SPAC) 이름에 주로 들어감
                        'SPAC',                # 스팩 명시
                        'RIGHTS',              # 신주인수권
                        'FUND',                # 펀드/ETF (개별 급등주 원할 경우 제외 고려)
                        'NOTE', 'DEBENTURE'    # 채권형 상품
                    ]

                    # 이름에 금지 키워드가 하나라도 포함되면 제외
                    if any(keyword in name for keyword in exclude_keywords):
                        # self.logger.info(f"🚫 필터링됨(이름): {sym} - {name}")
                        continue

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