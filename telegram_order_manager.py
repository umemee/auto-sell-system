# telegram_order_manager.py - 해외주식 자동매도 시스템 v2.0
# 기획서 v2.0 (섹션 5, 8, 10) 기반

import json
import logging
import time
import threading
import os
from datetime import datetime

# --- 의존성 파일 ---
# 🔴 [수정 2, 5] import 경로 수정
# order.py에 필요한 함수들이 모두 있다고 가정하고 import 경로를 통합합니다.
try:
    # KIS API 유틸리티 (실시간 시세, 가용 자금, 주문, 시스템 시간)
    from order import get_current_price, get_available_funds, place_buy_order, should_system_run
except ImportError:
    logging.critical("의존성 오류: order.py에서 주요 함수(get_current_price, get_available_funds, place_buy_order, should_system_run)를 찾을 수 없습니다.")
    # 실제 운영 시 이 함수들은 반드시 구현되어야 합니다.
    def get_current_price(*args, **kwargs): return 0.0
    def get_available_funds(*args, **kwargs): return 10000.0
    def place_buy_order(*args, **kwargs): 
        return {'success': False, 'order_no': None, 'error': 'NotImplemented'}
    def should_system_run(): 
        logging.warning("Fallback should_system_run: True 반환")
        return True
# --- 끝: 의존성 파일 ---


logger = logging.getLogger(__name__)

class TelegramOrderManager:
    """
    텔레그램 주문 시스템 (시스템 B)
    
    기획서 v2.0 (섹션 5)
    - Phase 1: 주문 관리 (CRUD, File I/O)
    - Phase 2: 목표가 감시 및 자동 매수
    """
    
    def __init__(self, config, token_manager, telegram_bot, order_monitor, trade_counter):
        """
        Args:
            config: 설정 딕셔너리
            token_manager: TokenManager 인스턴스
            telegram_bot: TelegramBot 인스턴스 (알림용)
            order_monitor: SmartOrderMonitor 인스턴스 (매수 후 등록용)
            trade_counter: DailyTradeCounter 인스턴스 (제한 확인용)
        """
        self.config = config
        self.token_manager = token_manager
        self.telegram_bot = telegram_bot
        self.order_monitor = order_monitor
        self.trade_counter = trade_counter
        
        # 기획서 8.1: 텔레그램 주문 상태 파일
        # 🔴 [수정 6] config.yaml에 없어도 안전하게 절대 경로를 기본값으로 사용
        self.state_file = config.get('system', {}).get(
            'telegram_state_file', 
            '/home/ec2-user/overseas-stock-auto-sell/telegram_orders.json'
        )
        
        # 기획서 5.3: 최대 주문 수
        self.MAX_PENDING_ORDERS = 6
        
        self.pending_orders = {}    # {order_id: order_data}
        self.executed_orders = []   # [order_data, ...]
        self.lock = threading.Lock() # 상태 변경을 위한 스레드 락
        
        self.is_running = False
        self.monitor_thread = None
        
        self.load_orders()
        logger.info(f"✅ 텔레그램 주문 관리자 초기화. 대기 중인 주문 {len(self.pending_orders)}건 로드.")

    # -------------------------------------------------------------------------
    # Phase 1: 주문 관리 (CRUD, File I/O) - 기획서 10.1
    # -------------------------------------------------------------------------

    def load_orders(self):
        """기획서 8.3: telegram_orders.json에서 주문 상태 로드"""
        with self.lock:
            if not os.path.exists(self.state_file):
                logger.warning(f"{self.state_file}이(가) 없습니다. 새 파일을 생성합니다.")
                self.save_orders_internal() # 빈 파일 생성
                return
            
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                self.pending_orders = data.get('pending_orders', {})
                self.executed_orders = data.get('executed_orders', [])
                
            except json.JSONDecodeError:
                logger.error(f"❌ {self.state_file} 파일이 손상되었습니다. 백업을 확인하세요.")
            except Exception as e:
                logger.error(f"❌ 주문 로드 실패: {e}")

    def save_orders_internal(self):
        """(내부용) 락 없이 상태 파일 저장"""
        try:
            state = {
                'pending_orders': self.pending_orders,
                'executed_orders': self.executed_orders
            }
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
                
        except Exception as e:
            logger.error(f"❌ 주문 저장 실패: {e}")
            if self.telegram_bot:
                self.telegram_bot.send_error_notification(f"주문 저장 실패: {e}")

    def _generate_order_id(self):
        """텔레그램 주문 ID 생성 (예: TG_20251113103015)"""
        return f"TG_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    def add_pending_order(self, ticker, target_price, ratio):
        """
        텔레그램봇(/buy)의 요청을 받아 새 주문을 추가 (기획서 5.2)
        
        Returns:
            dict: {'success': True, 'order_data': ...}
                  또는 {'error': '에러코드', 'message': '...'}
        """
        with self.lock:
            # 기획서 5.3: 최대 주문 수 (6개)
            if len(self.pending_orders) >= self.MAX_PENDING_ORDERS:
                logger.warning(f"텔레그램 주문 실패: 최대 주문 수({self.MAX_PENDING_ORDERS}개) 초과")
                return {'error': 'TG_MAX_ORDERS', 'message': f"이미 대기 주문이 {self.MAX_PENDING_ORDERS}개입니다."}

            try:
                # 가용 자금 확인 및 수량 계산 (기획서 5.2)
                available_funds = get_available_funds(self.config, self.token_manager)
                investment_amount = available_funds * (ratio / 100)
                quantity = int(investment_amount / target_price)
                estimated_cost = quantity * target_price

                if quantity == 0:
                    logger.warning(f"자금 부족: ${investment_amount:.2f} (1주 @ ${target_price} 구매 불가)")
                    return {'error': 'TG_INSUFFICIENT_FUNDS', 'message': f"자금이 부족하여 1주({target_price})도 매수할 수 없습니다."}

                order_id = self._generate_order_id()
                order_data = {
                    'order_id': order_id,
                    'ticker': ticker,
                    'target_price': target_price,
                    'ratio': ratio,
                    'quantity': quantity,
                    'estimated_cost': estimated_cost,
                    'status': 'pending',
                    'created_at': datetime.now().isoformat(),
                    'attempts': 0
                }
                
                self.pending_orders[order_id] = order_data
                self.save_orders_internal()
                
                logger.info(f"✅ [TG] 신규 주문 추가: {order_id} ({ticker} @ ${target_price})")
                
                return {'success': True, 'order_data': order_data}

            except Exception as e:
                logger.error(f"❌ 주문 추가 중 오류: {e}")
                return {'error': 'TG_UNKNOWN_ERROR', 'message': str(e)}

    def get_pending_orders(self):
        """대기 중인 모든 주문 목록 반환 (기획서 5.2 /orders)"""
        with self.lock:
            # 동시 수정을 방지하기 위해 값 목록의 복사본을 반환
            return list(self.pending_orders.values())

    def cancel_order(self, order_id):
        """특정 주문 취소 (기획서 5.2 /cancel)"""
        with self.lock:
            if order_id in self.pending_orders:
                order = self.pending_orders.pop(order_id)
                self.save_orders_internal()
                logger.info(f"🗑️ [TG] 주문 취소: {order_id} ({order['ticker']})")
                return order
            
            return None # 취소할 주문 없음

    def cancel_all_pending_orders(self):
        """슬립 모드 진입 시 모든 대기 주문 취소 (기획서 7.4)"""
        with self.lock:
            if not self.pending_orders:
                return []
                
            cancelled_orders = list(self.pending_orders.values())
            self.pending_orders.clear()
            self.save_orders_internal()
            
            logger.info(f"🌙 [TG] 슬립 모드: 대기 중인 주문 {len(cancelled_orders)}건 전체 취소")
            return cancelled_orders

    # -------------------------------------------------------------------------
    # Phase 2: 목표가 감시 및 자동 매수 - 기획서 10.2
    # -------------------------------------------------------------------------

    def start(self):
        """목표가 감시 스레드 시작"""
        if self.is_running:
            logger.warning("⚠️ 텔레그램 감시 스레드가 이미 실행 중입니다.")
            return
        
        self.is_running = True
        self.monitor_thread = threading.Thread(target=self.monitor_target_prices, daemon=True)
        self.monitor_thread.start()
        logger.info("🚀 [TG] 목표가 감시 스레드 시작")

    def stop(self):
        """목표가 감시 스레드 종료"""
        if not self.is_running:
            return
            
        self.is_running = False
        if self.monitor_thread and self.monitor_thread.is_alive():
            try:
                self.monitor_thread.join(timeout=5)
            except Exception as e:
                logger.error(f"텔레그램 감시 스레드 종료 중 오류: {e}")
                
        logger.info("🛑 [TG] 목표가 감시 스레드 종료")

    def monitor_target_prices(self):
        """
        (스레드) 10초마다 대기 중인 주문의 목표가 감시 (기획서 5.4)
        """
        while self.is_running:
            start_time = time.time()
            try:
                # 🔴 [수정 3] reset_if_new_day()는 반환값 없음 (호출만)
                self.trade_counter.reset_if_new_day()
                
                # 🔴 [수정 2] order.py의 should_system_run() 직접 호출
                # 주말/운영시간 외에는 감시 중지 (기획서 2.3)
                if not should_system_run():
                    time.sleep(60) # 1분 대기
                    continue
                
                # 스레드 안전하게 주문 목록 복사본 가져오기
                orders_to_check = self.get_pending_orders()
                
                if not orders_to_check:
                    time.sleep(10) # 감시할 주문 없음
                    continue
                
                for order in orders_to_check:
                    if not self.is_running: break # 스레드 종료 신호
                    
                    # 1. 실시간 시세 조회
                    current_price = get_current_price(self.config, self.token_manager, order['ticker'])
                    
                    if current_price is None or current_price == 0.0:
                        logger.debug(f"[TG] {order['ticker']} 시세 조회 실패, 스킵")
                        continue
                    
                    # 2. 목표가 도달 확인 (기획서 5.4)
                    if current_price <= order['target_price']:
                        logger.info(f"🎯 [TG] 목표가 도달: {order['ticker']} (현재가: ${current_price} <= 목표가: ${order['target_price']})")
                        
                        # 3. 일일 제한 확인 (기획서 7.3)
                        if not self.trade_counter.can_trade():
                            # 🔴 [수정 1] Syntax Error 수정
                            logger.warning(
                                f"⚠️ [TG] {order['ticker']} 목표가 도달, "
                                f"그러나 일일 매매 한도({self.trade_counter.MAX_TRADES}회) 도달로 매수 보류."
                            )
                            
                            # 기획서 7.3: 사용자에게 알림 (주문은 유지)
                            self.telegram_bot.send_message(
                                f"⚠️ 매매 한도 도달\n\n"
                                f"{order['ticker']} 목표가(${order['target_price']})에 도달했지만,\n"
                                f"일일 매매 한도({self.trade_counter.MAX_TRADES}회)에 도달하여 매수하지 않습니다.\n\n"
                                f"주문은 내일(ET 기준)까지 유지됩니다."
                            )
                            # 이 주문은 오늘 더 이상 확인하지 않음 (다음 루프에서 계속 확인됨)
                            continue
                        
                        # 4. 자동 매수 실행
                        # execute_buy_order가 성공하면(True) 내부에서 주문을 삭제하므로
                        # 루프가 깨지지 않음
                        self.execute_buy_order(order['order_id'])
            
            except Exception as e:
                logger.error(f"❌ [TG] 목표가 감시 루프 오류: {e}")
            
            finally:
                # 기획서 5.4: 10초마다 체크
                elapsed = time.time() - start_time
                wait_time = max(0, 10 - elapsed)
                time.sleep(wait_time)


    def execute_buy_order(self, order_id):
        """
        (내부용) 목표가 도달 시 자동 매수 실행 (기획서 5.5)
        - 2회 재시도 (5초 간격)
        """
        # 락을 획득하여 주문을 '선점'
        with self.lock:
            order = self.pending_orders.get(order_id)
            
            # 락을 기다리는 동안 다른 스레드가 처리했거나 취소된 경우
            if not order:
                logger.warning(f"[TG] {order_id} 매수 시도: 이미 처리되었거나 취소됨.")
                return False
            
            logger.info(f"🚀 [TG] {order['ticker']} @ ${order['target_price']} 매수 실행 시도...")

            # (중요) 락 내부에서 일일 한도 재확인 (Race Condition 방지)
            if not self.trade_counter.can_trade():
                logger.warning(f"⚠️ [TG] {order['ticker']} 매수 실패: 일일 한도 도달 (Race Condition 방지)")
                # 알림은 monitor_target_prices에서 이미 보냈으므로 생략
                return False

            # 기획서 5.5: 2회 재시도
            for attempt in range(2):
                try:
                    # 1. 가용 자금 재확인 (기획서 5.5)
                    available_funds = get_available_funds(self.config, self.token_manager)
                    required_funds = order['target_price'] * order['quantity']
                    
                    if available_funds < required_funds:
                        logger.error(f"❌ [TG] 자금 부족: {order['ticker']} (가용: ${available_funds} < 필요: ${required_funds})")
                        self.telegram_bot.send_message(
                            f"⚠️ 매수 실패 (자금 부족)\n\n"
                            f"종목: {order['ticker']}\n"
                            f"주문은 유지되며, 자금 확보 후 다음 기회에 재시도됩니다."
                        )
                        return False # 2회 재시도 없이 즉시 실패 (주문은 유지)

                    # 2. 매수 주문 실행 (order.py)
                    result = place_buy_order(
                        config=self.config,
                        token_manager=self.token_manager,
                        ticker=order['ticker'],
                        quantity=order['quantity'],
                        price=order['target_price'] # 지정가 매수
                    )
                    
                    # 3. 주문 성공
                    if result and result.get('success'):
                        kis_order_no = result['order_no']
                        
                        logger.info(f"✅ [TG] 매수 성공: {order['ticker']} (KIS 주문번호: {kis_order_no})")
                        
                        # 4. 텔레그램 알림 (기획서 5.5)
                        target_sell_price = order['target_price'] * (1 + self.config.get('order_settings', {}).get('target_profit_rate', 6.0) / 100)
                        self.telegram_bot.send_message(
                            f"✅ [TG] 매수 체결\n\n"
                            f"종목: {order['ticker']}\n"
                            f"가격: ${order['target_price']:.2f}\n"
                            f"수량: {order['quantity']}주\n"
                            f"주문ID: {kis_order_no}\n\n"
                            f"🎯 6% 자동매도 감시를 시작합니다.\n"
                            f"(목표 매도가: ${target_sell_price:.2f})"
                        )
                        
                        # 5. SmartOrderMonitor에 등록 (자동 매도 대기) (기획서 5.5)
                        # 🔴 [수정 4] v2.0의 신규 메서드 호출
                        self.order_monitor.add_order_to_monitor(
                            order_no=kis_order_no,
                            ticker=order['ticker'],
                            quantity=order['quantity'],
                            buy_price=order['target_price'],
                            source='telegram' # 🆕 v2.0
                        )
                        
                        # 6. 대기 목록 -> 실행 목록으로 이동 (기획서 8.3)
                        executed_order = self.pending_orders.pop(order_id)
                        executed_order['status'] = 'filled'
                        executed_order['kis_order_no'] = kis_order_no
                        executed_order['executed_at'] = datetime.now().isoformat()
                        self.executed_orders.append(executed_order)
                        
                        self.save_orders_internal()
                        
                        return True # 매수 성공, 루프 종료

                except Exception as e:
                    logger.error(f"❌ [TG] 매수 실행 오류 (시도 {attempt+1}/2): {e}")
                    
                    if attempt < 1: # 1회 더 시도 (총 2회)
                        time.sleep(5) # 기획서 5.3: 5초 간격
                        continue
            
            # 2회 모두 실패
            logger.warning(f"⚠️ [TG] 매수 실패 (2회 시도): {order['ticker']}, 주문 유지 (기획서 5.5)")
            
            order['attempts'] += 1 # 재시도 횟수 기록 (선택적)
            self.save_orders_internal()
            
            # 텔레그램 알림 (기획서 5.5)
            self.telegram_bot.send_message(
                f"⚠️ [TG] 매수 실패\n\n"
                f"종목: {order['ticker']}\n"
                f"목표가: ${order['target_price']}\n\n"
                f"네트워크/API 오류로 매수에 실패했습니다 (2회 시도).\n"
                f"주문은 유지되며, 10초 후 다음 기회에 재시도됩니다."
            )
            
            return False # 매수 실패 (주문은 유지됨)