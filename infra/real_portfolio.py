import logging
from config import Config

class RealPortfolio:
    """
    [RealPortfolio V1.0 - The Bridge Between Logic & Reality]

    역할:
      1. 실전 계좌 상태(현금, 보유종목)를 KIS API와 동기화 (Sync)
      2. 백테스팅에서 검증된 '자금 관리(Money Management)' 로직 적용
      3. '유령 포지션(Phantom Position)' 방지 및 데이터 무결성 보장

    참고 매뉴얼:
      - Scenario 1.3: 주문가능금액 조회 시 TTTS3007R 사용 (단순 잔고 X)
      - Scenario 1.4: 해외주식 잔고 조회 시 FK200 키 처리 (KisApi 위임)
    """

    def __init__(self, kis_api):
        self.logger = logging.getLogger("RealPortfolio")
        self.kis = kis_api

        # ----------------------------------------------------
        # 📊 Dynamic State (변동 데이터)
        # ----------------------------------------------------
        self.balance = 0.0          # 실제 주문 가능 금액 (Buying Power)
        self.total_equity = 0.0     # 총 자산 (현금 + 주식 평가액)
        
        # Positions Dictionary
        # 구조: { 'TICKER': { 'qty': 10, 'entry_price': 150.0, 'current_price': 155.0, ... } }
        self.positions = {} 

        # ----------------------------------------------------
        # ⚙️ Static Rules (불변 규칙)
        # ----------------------------------------------------
        self.MAX_SLOTS = 2          # [Double Engine] 최대 2종목
        self.SLOT_RATIO = 0.5       # 슬롯당 비중 50%
        self.MIN_ORDER_AMT = 50.0   # 최소 주문 금액 ($50 미만 주문 금지)

    def sync_with_kis(self):
        """
        [Critical] 증권사 서버와 내 내부 장부를 동기화
        - 주기적으로 호출되어야 함 (매 루프 시작 시)
        """
        try:
            # 1. 주문 가능 금액 조회 (Scenario 1.3 방지: TTTS3007R 사용)
            # 단순 잔고(GetBalance)가 아니라 '매수 가능 금액'을 가져와야 함
            buying_power = self.kis.get_buyable_cash()
            self.balance = float(buying_power)

            # 2. 보유 종목(잔고) 조회 (Scenario 1.4 방지: FK200 처리된 API 호출)
            holdings = self.kis.get_balance() # List[Dict] 형태 반환
            
            # 3. 내부 딕셔너리(self.positions) 초기화 및 재구축
            self.positions.clear()
            current_stock_value = 0.0

            if holdings:
                for item in holdings:
                    ticker = item['symbol']
                    qty = float(item['qty'])
                    
                    if qty <= 0: continue # 수량 0인 찌꺼기 데이터 필터링

                    # API에서 주는 평가 금액 (수량 * 현재가)
                    eval_amt = float(item.get('price', 0.0)) 
                    # API에서 주는 수익률
                    pnl_pct = float(item.get('pnl_pct', 0.0))
                    
                    # 평균 단가 역산 (평가금액 / 수량) - API가 평단가를 안 주거나 부정확할 때 대비
                    # 혹은 item.get('pchs_avg_pric') 사용 가능하면 사용
                    # 여기서는 보수적으로 평가액 기반 계산
                    current_price = eval_amt / qty if qty > 0 else 0.0
                    
                    # 진입가 역산 (현재가 / (1 + 수익률/100))
                    entry_price = current_price / (1 + pnl_pct/100.0) if (1 + pnl_pct/100.0) != 0 else current_price

                    self.positions[ticker] = {
                        'ticker': ticker,
                        'qty': int(qty),
                        'entry_price': entry_price,
                        'current_price': current_price,
                        'eval_value': eval_amt,
                        'pnl_pct': pnl_pct
                    }
                    current_stock_value += eval_amt

            # 4. 총 자산 가치 업데이트 (현금 + 주식)
            self.total_equity = self.balance + current_stock_value

            # 로그 출력 (디버깅용)
            self._log_status()

        except Exception as e:
            self.logger.error(f"❌ [Sync Fail] Portfolio Sync Failed: {e}")
            # 동기화 실패 시, 기존 데이터를 유지할지 클리어할지 결정해야 함.
            # 안전을 위해 여기서 멈추지 않고, 이전 상태를 유지하되 경고 로그를 남김.

    def has_open_slot(self):
        """빈 슬롯 확인 (Double Engine)"""
        return len(self.positions) < self.MAX_SLOTS

    def is_holding(self, ticker):
        """특정 종목 보유 여부"""
        return ticker in self.positions

    def get_position(self, ticker):
        """특정 종목 포지션 정보 반환"""
        return self.positions.get(ticker)

    def get_max_order_amount(self):
        """
        [자금 관리 코어]
        백테스팅 로직: 전체 자산(Equity)의 50%를 목표로 함.
        실전 로직: 
          Target = Total Equity * 0.5
          Available = Buying Power * 0.98 (미수/수수료 버퍼)
          Order Amount = Min(Target, Available)
        """
        # 1. 목표 금액 산정 (전체 자산의 50%)
        target_amount = self.total_equity * self.SLOT_RATIO

        # 2. 실제 가용 현금 (Scenario 1.1: 98% 안전 버퍼)
        usable_cash = self.balance * Config.ALL_IN_RATIO 

        # 3. 최종 주문 금액 결정
        # 돈이 있어도 목표 비중 이상은 안 사고,
        # 목표 비중이 높아도 돈이 없으면 못 산다.
        final_amount = min(target_amount, usable_cash)

        # 최소 주문 금액 체크 ($50 미만이면 0 처리 -> 주문 거부)
        if final_amount < self.MIN_ORDER_AMT:
            return 0.0

        return final_amount

    def update_local_after_order(self, fill):
        """
        [Optimistic Update]
        주문을 넣은 직후, 다음 API Sync가 돌기 전까지 
        '가상의 포지션'을 로컬에 잡아두어 중복 매수를 방지함.
        """
        ticker = fill['ticker']
        qty = fill['qty']
        price = fill['price']
        
        if fill['type'] == 'BUY':
            # 매수 즉시 잔고 차감 (가상)
            cost = qty * price
            self.balance -= cost
            
            self.positions[ticker] = {
                'ticker': ticker,
                'qty': qty,
                'entry_price': price,
                'current_price': price,
                'eval_value': cost,
                'pnl_pct': 0.0 # 진입 직후 수익률 0
            }
            self.logger.info(f"✅ [Local Update] Added {ticker} ({qty} @ {price})")
            
        elif fill['type'] == 'SELL':
            # 매도 즉시 잔고 증가 (가상)
            revenue = qty * price
            self.balance += revenue
            
            if ticker in self.positions:
                del self.positions[ticker]
                self.logger.info(f"👋 [Local Update] Removed {ticker}")

    def _log_status(self):
        """현재 상태 로그 출력"""
        pos_str = ", ".join([f"{k}({v['pnl_pct']:.1f}%)" for k, v in self.positions.items()])
        if not pos_str: pos_str = "None"
        
        self.logger.info(
            f"💰 [Portfolio] Equity: ${self.total_equity:,.0f} | "
            f"Cash: ${self.balance:,.0f} | "
            f"Slots: {len(self.positions)}/{self.MAX_SLOTS} | "
            f"Holding: [{pos_str}]"
        )