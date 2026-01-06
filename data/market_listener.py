import time
from infra.utils import get_logger

class MarketListener:
    def __init__(self, kis_api):
        self.kis = kis_api
        self.logger = get_logger("Scanner")
        # 감시할 종목 리스트 (여기에 실제 관심 종목들을 넣어야 합니다)
        # KIS API는 전 종목 스캐닝이 어려우므로, 주요 급등 후보군을 미리 넣어두는 것이 좋습니다.
        # 예시로 기술주/변동성 종목들을 넣어둡니다. 필요시 config에서 불러오도록 수정 가능합니다.
        self.target_symbols = [
            'TSLA', 'NVDA', 'AMD', 'AAPL', 'MSFT', 'AMZN', 'GOOGL', 'META',
            'NFLX', 'COIN', 'MARA', 'PLTR', 'SOXL', 'TQQQ', 'SQQQ'
        ]
        
    def scan_markets(self):
        """
        [수정된 로직]
        1. 기준 변경: 당일 시가(Open) -> 전일 종가(Base) 대비 등락률 확인
        2. 목표: HTS상 수익률이 +20% 이상인 종목을 1차적으로 모두 가져옴 (40%는 너무 빡빡할 수 있음)
        """
        detected_stocks = []
        
        # self.logger.info(f"🔍 스캐닝 시작 ({len(self.target_symbols)}개 종목)...")

        for sym in self.target_symbols:
            try:
                # 현재가 조회 (last:현재가, base:전일종가, open:시가)
                price_info = self.kis.get_current_price("NASD", sym)
                
                if not price_info:
                    continue

                curr_price = price_info.get('last', 0)
                base_price = price_info.get('base', 0) # 전일 종가
                
                # 데이터 유효성 체크
                if curr_price <= 0 or base_price <= 0:
                    continue

                # [핵심 변경] 전일 종가 기준 변동률 계산 (HTS와 동일)
                change_rate = (curr_price - base_price) / base_price
                change_pct = change_rate * 100

                # 40% 이상 급등주 포착 (테스트를 위해 15%로 낮춰서 로그 확인 추천)
                # 실제 운영 시에는 0.40 (40%)로 설정
                THRESHOLD = 20.0 # 일단 20%만 넘어도 포착하도록 완화 (검증용)

                if change_pct >= THRESHOLD:
                    self.logger.info(f"🚨 [포착] {sym}: ${curr_price} (+{change_pct:.2f}%)")
                    detected_stocks.append(sym)
                
                # API 호출 속도 조절 (너무 빠르면 차단됨)
                time.sleep(0.1) 

            except Exception as e:
                self.logger.error(f"Scan Error ({sym}): {e}")
                continue

        if detected_stocks:
            self.logger.info(f"✅ 최종 감시 대상: {detected_stocks}")
        
        return detected_stocks