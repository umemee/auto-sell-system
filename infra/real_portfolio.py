#infra/real_portfolio.py
import logging
from config import Config
import datetime
import pytz # 시간 기록을 위해 필수

class RealPortfolio:
    """
    [RealPortfolio V2.1 - Memory Enhanced & Integrity Protected]

    업그레이드 사항:
      1. State Preservation (기억 보존): 
         - API Sync 시 기존의 'highest_price'(고점) 정보를 덮어쓰지 않고 유지합니다.
         - 트레일링 스탑(Trailing Stop)이 정상 작동하기 위한 필수 조치입니다.
      2. Cool-down (재진입 금지): 
         - 'ban_list'를 도입하여 당일 매도한 종목은 장 마감 전까지 재매수를 차단합니다.
      3. Data Integrity (데이터 무결성):
         - API 잔고와 로컬 상태를 지능적으로 병합(Merge)합니다.
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
        # { 'TICKER': { 'qty': 10, 'entry_price': 100, 'highest_price': 120, ... } }
        self.positions = {} 
        
        # [NEW] 금일 매매 금지(Cool-down) 리스트 (Set 구조)
        self.ban_list = set()

        # ----------------------------------------------------
        # ⚙️ Static Rules (불변 규칙)
        # ----------------------------------------------------
        self.MAX_SLOTS = getattr(Config, 'MAX_SLOTS', 2)
        self.SLOT_RATIO = 0.5       
        self.MIN_ORDER_AMT = 20.0   

    def sync_with_kis(self):
        """
        [Smart Sync Logic] 
        API 잔고를 가져오되, 로컬의 중요 정보(highest_price)는 보존하는 병합 로직
        """
        try:
            # 1. 자산(예수금) 조회
            # TTTS3007R (주문 가능 금액) 사용 -> 미수 발생 방지
            buying_power = self.kis.get_buyable_cash()
            self.balance = float(buying_power)

            # 2. 보유 종목 API 조회
            holdings = self.kis.get_balance() # List[Dict] 반환
            
            # API에서 확인된 종목 코드 집합 (동기화 비교용)
            api_tickers = set()
            current_stock_value = 0.0

            if holdings:
                for item in holdings:
                    ticker = item['symbol']
                    qty = float(item['qty']) # 소수점 수량 대비 float
                    
                    if qty <= 0: continue # 잔여 찌꺼기 데이터 무시
                    
                    api_tickers.add(ticker)

                    # API 데이터 추출
                    eval_amt = float(item.get('price', 0.0))  # 평가 금액
                    pnl_pct = float(item.get('pnl_pct', 0.0)) # 수익률(%)
                    
                    # 수량이 정수가 아니라면 정수 처리 (미국 주식 소수점 가능성 고려 시 float 유지 권장이나 여기선 int)
                    qty = int(qty)

                    # 현재가 및 진입가 역산
                    current_price = eval_amt / qty if qty > 0 else 0.0
                    
                    # API 수익률 기반 진입가 역산 (API 평단가가 부정확할 경우 대비)
                    if (1 + pnl_pct/100.0) != 0:
                        entry_price = current_price / (1 + pnl_pct/100.0)
                    else:
                        entry_price = current_price

                    # [핵심] 기존 정보 병합 (Merge)
                    if ticker in self.positions:
                        # 🕒 [Time Cut] 기존에 기록된 진입 시간 가져오기
                        cached_entry_time = self.positions[ticker].get('entry_time')

                        # 이미 로컬에 있는 종목 -> highest_price 및 entry_time 유지
                        self.positions[ticker].update({
                            'qty': qty,
                            'current_price': current_price,
                            'eval_value': eval_amt,
                            'pnl_pct': pnl_pct,
                            'entry_time': cached_entry_time # ✨ [추가] API 동기화 시 시간 정보 보존
                        })
                        
                        # 고점 갱신 로직 (기존 유지)
                        if current_price > self.positions[ticker].get('highest_price', 0):
                            self.positions[ticker]['highest_price'] = current_price

                    else:
                        # 로컬에 없던 신규 종목 (API에는 있는데 로컬엔 없는 경우)
                        # 이 경우 정확한 매수 시점을 알 수 없으므로, '현재 시간'을 기준으로 잡거나 비워둡니다.
                        # 여기서는 보수적으로 '현재 시간'을 넣어 타임 컷이 바로 발동되지 않게 합니다.
                        now_et = datetime.datetime.now(pytz.timezone('US/Eastern'))
                        
                        self.positions[ticker] = {
                            'ticker': ticker,
                            'qty': qty,
                            'entry_price': entry_price,
                            'current_price': current_price,
                            'eval_value': eval_amt,
                            'pnl_pct': pnl_pct,
                            'highest_price': current_price,
                            'entry_time': now_et # ✨ [추가] 초기화
                        }
                    
                    current_stock_value += eval_amt

            # 3. 사라진 종목 처리 (매도 완료 감지)
            # 로컬에는 있었는데 API 목록(api_tickers)에 없다면 -> 매도된 것임
            local_tickers = list(self.positions.keys())
            for ticker in local_tickers:
                if ticker not in api_tickers:
                    self.logger.info(f"🗑️ [Sync] Position Removed detected: {ticker}")
                    del self.positions[ticker]
                    self.ban_list.add(ticker) # [Cool-down] 금일 재매수 금지 등록

            # 4. 총 자산 가치 업데이트
            self.total_equity = self.balance + current_stock_value

            # 로그 출력 (선택 사항)
            # self._log_status()

        except Exception as e:
            self.logger.error(f"❌ [Sync Fail] Portfolio Sync Failed: {e}")
            # 동기화 실패 시 로컬 상태 유지 (삭제하지 않음)

    def has_open_slot(self):
        """빈 슬롯 확인 (Double Engine)"""
        return len(self.positions) < self.MAX_SLOTS

    def is_holding(self, ticker):
        """특정 종목 보유 여부"""
        return ticker in self.positions

    def is_banned(self, ticker):
        """[NEW] 금일 매매 금지 종목 확인"""
        return ticker in self.ban_list

    def get_position(self, ticker):
        """특정 종목 포지션 정보 반환"""
        return self.positions.get(ticker)

    def get_max_order_amount(self):
        """
        [Double Engine 자금 관리]
        목표: 전체 자산의 50% 베팅 (단, 현금 범위 내에서)
        """
        # 1. 현재 슬롯 확인 (이미 꽉 찼으면 0 반환)
        if len(self.positions) >= self.MAX_SLOTS:
            return 0.0

        # 2. 1슬롯당 목표 금액 계산 (총 자산 / 2)
        target_amount = self.total_equity / self.MAX_SLOTS
        
        # 3. [안전 장치] 주문 가능 현금의 98% (수수료/슬리피지 버퍼)
        # 중요: 목표 금액이 아무리 커도, 내 수중에 있는 현금보다 많이 주문할 순 없음
        safe_cash = self.balance * 0.98 
        
        # 4. 최종 주문 금액 (둘 중 작은 값)
        final_amount = min(target_amount, safe_cash)
        
        # 최소 주문 금액 ($50 미만은 주문 안 함 - 수수료 효율 고려)
        if final_amount < 20:
            return 0.0
            
        return final_amount

    def update_local_after_order(self, fill):
        """
        [Optimistic Update]
        주문 직후 API 반영 전, 로컬 상태를 선제적으로 업데이트하여
        중복 주문 방지 및 반응 속도 향상
        """
        ticker = fill['ticker']
        qty = int(fill['qty'])
        price = float(fill['price'])
        
        if fill['type'] == 'BUY':
            cost = qty * price
            self.balance -= cost
            
            # 🕒 [Time Cut] 현재 미국 시간 기록
            now_et = datetime.datetime.now(pytz.timezone('US/Eastern'))

            # [수정 1] VIVS 사태 방지: 기존 데이터가 있으면 삭제 후 덮어쓰기 (강제 초기화)
            if ticker in self.positions:
                self.logger.warning(f"⚠️ [Data Clean] {ticker} 기존 데이터 삭제 후 재진입")
                del self.positions[ticker]

            # [수정 2] 신규 데이터 생성 (평단가 = 현재 매수가로 고정)
            self.positions[ticker] = {
                'ticker': ticker,
                'qty': qty,
                'entry_price': price,        # 진입가 확실하게 기록
                'current_price': price,
                'eval_value': cost,
                'pnl_pct': 0.0,
                'highest_price': price, 
                'entry_time': now_et         # 진입 시간 기록
            }
            
            self.logger.info(f"✅ [Local Update] BUY {ticker} ({qty}주 @ ${price}) | Balance: ${self.balance:.2f}")
            
        elif fill['type'] == 'SELL':
            # [수정 3] 수수료(0.2% 가정)를 뗀 금액만 예수금에 반영하여 '자금 부족' 방지
            revenue = (qty * price) * 0.998 
            self.balance += revenue
            
            if ticker in self.positions:
                del self.positions[ticker]
                self.ban_list.add(ticker) # 매도 시 즉시 밴 리스트 추가
                
                self.logger.info(f"👋 [Local Update] SELL {ticker} -> Added to Ban List | Balance: ${self.balance:.2f}")
                
                # [필수] 주문 직후 총 자산(Equity) 재계산
                current_val = sum(p['qty'] * p['current_price'] for p in self.positions.values())
                self.total_equity = self.balance + current_val

    def update_highest_price(self, ticker, current_price):
        """
        [Backtest Logic 이식] 트레일링 스탑을 위한 고가 갱신
        """
        if ticker in self.positions:
            # 기존 고가보다 현재가가 높으면 갱신
            if current_price > self.positions[ticker]['highest_price']:
                old_high = self.positions[ticker]['highest_price']
                self.positions[ticker]['highest_price'] = current_price
                # (선택) 로그가 너무 많으면 주석 처리 가능
                # self.logger.info(f"📈 [{ticker}] 고가 갱신: ${old_high} -> ${current_price}")
    
    # [신규 추가] 외부(main.py)에서 호출할 잔고 강제 동기화 함수
    def sync_balance(self):
        """API를 통해 예수금만 강제 동기화 (매도 직후 사용)"""
        try:
            # get_buyable_cash는 kis_api에 구현되어 있어야 함
            cash = self.kis.get_buyable_cash() 
            if cash > 0:
                old_balance = self.balance
                self.balance = float(cash)
                self.logger.info(f"💰 [Sync] 잔고 갱신 완료: ${old_balance:.2f} -> ${self.balance:.2f}")
        except Exception as e:
            self.logger.error(f"❌ 잔고 동기화 실패: {e}")
    
    def _log_status(self):
        """현재 상태 로그 출력 (디버깅용)"""
        pos_str = ", ".join([f"{k}({v.get('pnl_pct',0):.1f}%)" for k, v in self.positions.items()])
        if not pos_str: pos_str = "None"
        
        self.logger.info(
            f"💰 Equity: ${self.total_equity:,.0f} | "
            f"Cash: ${self.balance:,.0f} | "
            f"Slots: {len(self.positions)}/{self.MAX_SLOTS} | "
            f"Holding: [{pos_str}] | "
            f"Ban List: {len(self.ban_list)}"
        )