import time
from infra.utils import get_logger

class MarketListener:
    def __init__(self, kis_api):
        self.kis = kis_api
        self.logger = get_logger("Scanner")
        # [중요] 감시할 종목 리스트 (여기에 VSME, CYCN 등 급등 후보를 넣어야 봅니다!)
        self.target_symbols = [
            'VSME', 'CYCN', 'TSLA', 'NVDA', 'AAPL', 'PLTR', 'SOXL', 
            'TQQQ', 'SQQQ', 'AMD', 'MSFT', 'AMZN', 'GOOGL', 'META'
        ]
        
    def scan_markets(self):
        """
        [최종 수정] 
        기준: Gap(%) = (현재가 - 시가) / 시가
        이유: KIS API가 일부 종목의 전일종가(Base)를 0으로 주기 때문에,
             확실하게 데이터가 있는 '시가(Open)'를 기준으로 40% 급등을 잡습니다.
        """
        detected_stocks = []
        
        for sym in self.target_symbols:
            try:
                # 현재가 조회
                price_info = self.kis.get_current_price("NASD", sym)
                if not price_info: continue

                curr_price = float(price_info.get('last', 0))
                open_price = float(price_info.get('open', 0)) # 당일 시가
                
                # 데이터 유효성 체크 (시가가 0이면 계산 불가)
                if curr_price <= 0 or open_price <= 0:
                    continue

                # [핵심] 시가(Open) 기준 변동률 계산 (check_scanner의 Gap%와 동일)
                change_rate = (curr_price - open_price) / open_price
                change_pct = change_rate * 100

                # 40% 이상 급등주 포착
                THRESHOLD = 40.0 

                # 로그에 감지된 수치 출력 (디버깅용)
                if change_pct > 10.0: # 10%만 넘어도 로그에는 찍어봄
                    self.logger.info(f"🔎 {sym}: ${curr_price} (Gap: {change_pct:.2f}%)")

                if change_pct >= THRESHOLD:
                    self.logger.info(f"🚨 [포착] {sym} 급등! (+{change_pct:.2f}%)")
                    detected_stocks.append(sym)
                
                time.sleep(0.1) # API 부하 조절

            except Exception as e:
                self.logger.error(f"Scan Error ({sym}): {e}")
                continue

        return detected_stocks