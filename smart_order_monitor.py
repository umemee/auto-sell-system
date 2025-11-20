# smart_order_monitor.py - v2.0 기획서 Phase 4 (DailyTradeCounter) 적용
# Specification v1.1 Compliant
# [v2.7 수정] Premarket 모드에서도 매도 모니터링 확실하게 실행되도록 루프 구조 개선
# [v2.8 수정] API 필수 파라미터(SORT_SQN 등) 추가 및 날짜 포맷 오타 수정

import requests
import json
import logging
import time
import threading
import os
import fcntl
from datetime import datetime, timedelta, time as dtime
from pytz import timezone

logger = logging.getLogger(__name__)

# Try to import WebSocket client
try:
    from websocket_client import WebSocketClient
except ImportError:
    logger.warning("WebSocketClient not found. WebSocket mode will be disabled.")
    class WebSocketClient:
        """Fallback WebSocket client if import fails"""
        def __init__(self, *args, **kwargs):
            pass
        def start(self):
            pass
        def stop(self):
            pass

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DailyTradeCounter
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class DailyTradeCounter:
    """
    일일 매매 횟수 카운터 (v2.0 통합 제어 시스템)
    - source ('auto', 'telegram') 별 카운트
    - threading.Lock()으로 Race Condition 방지 (기획서 12.2)
    """
    def __init__(self, telegram_bot=None):
        self.telegram_bot = telegram_bot
        self.date = None
        self.count = 0
        self.MAX_TRADES = 8  # 하루 최대 매매 횟수
        
        # 🆕 [v2.0] 소스별 카운트
        self.auto_count = 0
        self.telegram_count = 0
        
        # 🆕 [v2.0] Race Condition 방지용 락
        self.lock = threading.Lock()
        
        try:
            # 타임존 설정 (기본: US/Eastern)
            self.tz = timezone('US/Eastern')
        except Exception:
            logger.error("pytz 라이브러리가 필요합니다. 'pip install pytz'")
            self.tz = None
        
        self._reset_if_new_day() # 락 없이 내부 호출

    def _reset_if_new_day(self):
        """(내부용) 날짜가 바뀌면 카운터 리셋 (락 없이 호출)"""
        if not self.tz:
            today = datetime.now().date() # Fallback
        else:
            today = datetime.now(self.tz).date()
        
        if self.date != today:
            self.date = today
            self.count = 0
            # 🆕 [v2.0] 소스별 카운트 리셋
            self.auto_count = 0
            self.telegram_count = 0
            logger.info(f"📅 새로운 날: {today} (ET). 매매 카운터 리셋 (최대 {self.MAX_TRADES}회).")

    def can_trade(self):
        """매매 가능 여부 확인 (스레드 안전)"""
        with self.lock:
            self._reset_if_new_day()
            
            if self.count >= self.MAX_TRADES:
                logger.warning(f"⚠️ 오늘 매매 {self.count}/{self.MAX_TRADES}회 도달. 더 이상 매매하지 않습니다.")
                return False
            return True

    def increment(self, source='auto'):
        """
        매매 횟수 증가 (스레드 안전)
        
        Args:
            source (str): 'auto' 또는 'telegram'
        """
        with self.lock:
            self._reset_if_new_day() # 날짜 변경 보장
            
            # 🆕 [v2.0] 한도 도달 시 방어적 코드 (기획서 7.2 Race Condition)
            if self.count >= self.MAX_TRADES:
                logger.warning(f"이미 매매 한도({self.MAX_TRADES}회) 도달. 카운트: {self.count}")
                return

            self.count += 1
            
            # 🆕 [v2.0] 소스별 카운트
            if source == 'auto':
                self.auto_count += 1
            elif source == 'telegram':
                self.telegram_count += 1
                
            logger.info(
                f"✅ [{source.upper()}] 매매 완료: {self.count}/{self.MAX_TRADES}회 "
                f"(Auto: {self.auto_count}, TG: {self.telegram_count})"
            )
            
            # 텔레그램 알림 (한도 도달 시)
            if self.count >= self.MAX_TRADES:
                if self.telegram_bot and hasattr(self.telegram_bot, 'send_message'):
                    try:
                        self.telegram_bot.send_message(
                            f"🚫 오늘 매매 한도 도달 ({self.MAX_TRADES}회)\n\n"
                            f"• 자동 감지: {self.auto_count}회\n"
                            f"• 텔레그램: {self.telegram_count}회\n\n"
                            f"내일 (ET 기준)까지 매매를 중단합니다."
                        )
                    except Exception as e:
                        logger.error(f"매매 한도 도달 알림 실패: {e}")

    # 🆕 [v2.0] main.py에서 요청한 통계 메서드
    def get_stats(self):
        """소스별 통계 반환 (스레드 안전)"""
        with self.lock:
            self._reset_if_new_day()
            
            return {
                'total': self.count,
                'auto': self.auto_count,
                'telegram': self.telegram_count,
                'remaining': self.MAX_TRADES - self.count,
                'max_trades': self.MAX_TRADES
            }
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔴 [v2.0 수정] SellOrderMonitor (v2.0 연동)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class SellOrderMonitor:
    """
    매도 주문 체결 감시자
    
    역할:
    1. 매도 주문이 실제로 체결되었는지 10초마다 확인
    2. 체결 확인 후에만 DailyTradeCounter 증가 (v2.0 - source 전달)
    3. 30분 이상 미체결 시 경고
    """
    
    def __init__(self, config, token_manager, telegram_bot, trade_counter):
        self.config = config
        self.token_manager = token_manager
        self.telegram_bot = telegram_bot
        self.trade_counter = trade_counter
        
        # 체결 대기 중인 매도 주문들
        self.pending_sells = {}  # {order_no: order_info}
        
        self.is_running = False
        self.monitor_thread = None
        
        logger.info("✅ SellOrderMonitor 초기화 완료 (v2.0)")
    
    def add_order(self, order_no, order_info):
        """
        매도 주문을 감시 목록에 추가
        """
        self.pending_sells[order_no] = order_info
        logger.info(f"📝 [{order_info.get('source', 'auto').upper()}] 매도 주문 감시 등록: {order_no} ({order_info['ticker']})")
    
    def check_order_filled(self, order_no):
        """
        특정 주문이 체결되었는지 확인
        """
        try:
            from order import inquire_ccnl
            from datetime import datetime
            
            # [수정] 날짜 포맷 오타 수정 (%Ym%d -> %Y%m%d)
            today = datetime.now().strftime("%Y%m%d")
            
            # 체결 내역 조회
            df = inquire_ccnl(
                config=self.config,
                token_manager=self.token_manager,
                ord_strt_dt=today,
                ord_end_dt=today,
                sll_buy_dvsn="01",  # 매도만
                ccld_nccs_dvsn="01",  # 체결만
                odno=order_no
            )
            
            # DataFrame이 비어있지 않으면 체결됨
            if df is not None and not df.empty:
                logger.info(f"✅ 체결 확인: {order_no}")
                return True
            
            return False
        
        except Exception as e:
            logger.error(f"❌ 체결 확인 오류 ({order_no}): {e}")
            return False
    
    def monitor_loop(self):
        """
        메인 감시 루프 - 10초마다 실행
        """
        logger.info("🔍 매도 체결 감시 시작 (v2.0)")
        
        while self.is_running:
            try:
                # 락 없이 안전하게 순회하기 위해 list()로 복사
                for order_no, info in list(self.pending_sells.items()):
                    
                    # 30분 이상 미체결이면 타임아웃
                    age_seconds = (datetime.now() - info['created_at']).total_seconds()
                    if age_seconds > 1800:  # 30분
                        logger.warning(f"⚠️ 매도 미체결 타임아웃: {order_no} ({info['ticker']})")
                        
                        # 목록에서 제거
                        if order_no in self.pending_sells:
                            del self.pending_sells[order_no]
                        
                        # 텔레그램 알림
                        if self.telegram_bot:
                            self.telegram_bot.send_message(
                                f"⚠️ 매도 미체결 경고\n\n"
                                f"종목: {info['ticker']}\n"
                                f"주문번호: {order_no}\n"
                                f"경과 시간: 30분\n\n"
                                f"KIS 앱에서 수동 확인 필요"
                            )
                        
                        continue
                    
                    # 체결 확인
                    if self.check_order_filled(order_no):
                        
                        # 🆕 [v2.0] DailyTradeCounter에 source 전달
                        source = info.get('source', 'auto')
                        logger.info(f"✅ [{source.upper()}] 매도 체결 완료: {order_no} ({info['ticker']})")
                        
                        # ✅ 핵심: 체결 확인 후에만 카운터 증가!
                        self.trade_counter.increment(source=source)
                        
                        # 텔레그램 알림
                        if self.telegram_bot:
                            # 수익 계산
                            buy_price = info.get('buy_price', 0)
                            profit_amount = 0
                            if buy_price > 0:
                                profit_amount = (info['sell_price'] - buy_price) * info['quantity']
                            
                            # 🆕 [v2.0] 알림에 소스 표시
                            source_text = " (TG)" if source == 'telegram' else " (Auto)"
                            
                            self.telegram_bot.send_message(
                                f"✅ 매도 체결 완료{source_text} ({self.trade_counter.get_stats()['total']}/{self.trade_counter.MAX_TRADES})\n\n"
                                f"종목: {info['ticker']}\n"
                                f"수량: {info['quantity']}주\n"
                                f"체결가: ${info['sell_price']:.2f}\n"
                                f"수익: ${profit_amount:.2f}\n"
                                f"주문번호: {order_no}"
                            )
                        
                        # 목록에서 제거
                        if order_no in self.pending_sells:
                            del self.pending_sells[order_no]
                
                # 10초 대기
                time.sleep(10)
            
            except Exception as e:
                logger.error(f"❌ 매도 감시 루프 오류: {e}")
                time.sleep(10)
        
        logger.info("🛑 매도 체결 감시 종료")
    
    def start(self):
        """감시 스레드 시작"""
        if self.is_running:
            logger.warning("⚠️ 이미 실행 중")
            return
        
        self.is_running = True
        self.monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.monitor_thread.start()
        logger.info("🚀 매도 체결 감시 스레드 시작")
    
    def stop(self):
        """감시 스레드 종료"""
        if not self.is_running:
            return
        
        self.is_running = False
        
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
        
        logger.info("🛑 매도 체결 감시 스레드 종료")
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class SmartOrderMonitor:
    """
    Smart Order Monitor System (기획서 v1.1 완전 준수)
    
    [v2.4 수정] 운영 시간 04:00 ET (한국 18:00) 시작으로 강제 지정
    """

    def __init__(self, config, token_manager, telegram_bot=None):
        self.config = config
        self.token_manager = token_manager
        self.telegram_bot = telegram_bot
        
        self.telegram_order_manager = None # main.py에서 주입 예정
        
        # Monitoring state
        self.monitoring_orders = {}  # {order_no: order_info}
        self.is_running = False
        self.monitor_thread = None
        
        # Spec 3.1: Polling configurations
        self.premarket_config = config['polling'].get('premarket', {})
        self.ws_config = config['polling'].get('regular', {})
        
        # Spec 5.1: Rate limit protection
        self.rate_config = config['rate_limit']
        self.daily_api_count = 0
        self.last_reset_date = None 
        self.hourly_api_count = 0
        self.last_hour_reset = datetime.now().hour
        self.last_request_time = 0
        self.consecutive_requests = 0
        
        # State persistence (Spec 7.1)
        self.state_file = config['system'].get('state_file', '/tmp/auto-sell-order-state.json')

        # Spec 4.4: Order tracking (prevent duplicates)
        self.processed_orders = set()      # Successfully sold (✅ 1순위)
        self.failed_orders = {}           # Failed orders: {order_no: (timestamp, reason)}
        self.processed_ws_orders = set()  # Processed via WebSocket
        
        # ✅ 스레드 안전을 위한 Lock 추가
        self._counter_lock = threading.Lock()
        self.lock = threading.Lock()
        
        self.load_persisted_state()
        
        # Statistics
        self.stats = {
            'total_requests': 0,
            'successful_detections': 0,
            'ws_detections': 0,
            'mode_switches': 0,
            'rate_limit_violations': 0,
            'api_errors': {},
            'premarket_calls': 0,
            'consecutive_api_errors': 0
        }
        
        # WebSocket client initialization
        self.ws_client = None
        self.ws_failure_count = 0
        self.ws_max_failures = 3  # Spec 2.3: 3 attempts before system stop
        
        # Last buy order scan time
        self.last_buy_scan = 0
        
        # 🔴 [v2.0 수정] 일일 매매 카운터 초기화 (통합 제어)
        self.trade_counter = DailyTradeCounter(self.telegram_bot)
        
        # 🔴 [v2.0 수정] 매도 체결 감시자 초기화 (공유 카운터 전달)
        self.sell_monitor = SellOrderMonitor(
            config=self.config,
            token_manager=self.token_manager,
            telegram_bot=self.telegram_bot,
            trade_counter=self.trade_counter # 🆕 공유 인스턴스 전달
        )
        logger.info("✅ SellOrderMonitor 통합 완료 (v2.0)")
        
        self.reset_counters_if_needed()
        
        # [v2.4 수정] 초기 모드 설정 (자체 로직 사용)
        self.current_mode = self.get_current_trading_mode()
        self.last_mode_change = datetime.now()

    def set_telegram_order_manager(self, manager):
        """main.py에서 TelegramOrderManager를 주입받기 위한 세터(setter)"""
        self.telegram_order_manager = manager
        logger.info("✅ TelegramOrderManager(B)가 SmartOrderMonitor(A)에 연결되었습니다.")

    def load_persisted_state(self):
        """Spec 7.1: Load persisted state from file"""
        with self.lock: # 🔴 [추가] 락 적용
            try:
                if os.path.exists(self.state_file):
                    with open(self.state_file, 'r', encoding='utf-8') as f:
                        state = json.load(f)
                    
                    # Only restore orders from last 1 hour
                    cutoff_time = datetime.now() - timedelta(hours=1)
                    
                    for order_no, order_data in state.get('orders', {}).items():
                        created_at_str = order_data.get('created_at', '')
                        if not created_at_str:
                            continue
                        
                        created_at = datetime.fromisoformat(created_at_str)
                        
                        if created_at > cutoff_time:
                            order_data['created_at'] = created_at
                            if 'last_checked' in order_data and order_data['last_checked']:
                                order_data['last_checked'] = datetime.fromisoformat(order_data['last_checked'])
                            self.monitoring_orders[order_no] = order_data
                    
                    self.processed_orders = set(state.get('processed_orders', []))
                    
                    logger.info(f"💾 State restored: {len(self.monitoring_orders)} orders, {len(self.processed_orders)} processed orders")
            except Exception as e:
                logger.warning(f"State restoration failed: {e}")

    def save_state(self):
        """Spec 7.1: Save current state to file"""
        with self.lock: # 🔴 [추가] 락 적용
            try:
                state = {
                    'timestamp': datetime.now().isoformat(),
                    'last_check': datetime.now().isoformat(),
                    'orders': {},
                    'processed_orders': list(self.processed_orders)
                }
                
                for order_no, order_data in self.monitoring_orders.items():
                    order_copy = order_data.copy()
                    if isinstance(order_data.get('created_at'), datetime):
                        order_copy['created_at'] = order_data['created_at'].isoformat()
                    if order_data.get('last_checked') and isinstance(order_data.get('last_checked'), datetime):
                        order_copy['last_checked'] = order_data['last_checked'].isoformat()
                    state['orders'][order_no] = order_copy
                
                os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
                
                with open(self.state_file, 'w', encoding='utf-8') as f:
                    fcntl.flock(f, fcntl.LOCK_EX)  # 배타적 잠금
                    json.dump(state, f, ensure_ascii=False, indent=2)
                    fcntl.flock(f, fcntl.LOCK_UN)  # 잠금 해제

            except Exception as e:
                logger.warning(f"State save failed: {e}")

    # 🔴 [v2.4 수정] 자체 운영 시간 판단 로직 (04:00 ET 시작)
    def should_system_run(self):
        """
        시스템 운영 시간 확인 (04:00 ET ~ 12:00 ET)
        """
        try:
            trading_tz = self.config.get('order_settings', {}).get('timezone', 'US/Eastern')
            tz = timezone(trading_tz)
            now = datetime.now(tz)
            now_time = now.time()
            
            # 주말 체크 (토=5, 일=6)
            if now.weekday() >= 5:
                # logger.debug("주말 - 운영 안 함")
                return False
                
            # 05:00 ET ~ 12:00 ET
            start_time = dtime(5, 0)
            end_time = dtime(12, 0)
            
            is_running = start_time <= now_time < end_time
            return is_running

        except Exception as e:
            logger.error(f"System time check error: {e}")
            return False

    # 🔴 [v2.4 수정] 자체 모드 판단 로직 (04:00 ET 시작)
    def get_current_trading_mode(self):
        """
        현재 거래 모드 확인 (04:00 ET 기준)
        """
        try:
            trading_tz = self.config.get('order_settings', {}).get('timezone', 'US/Eastern')
            tz = timezone(trading_tz)
            now_time = datetime.now(tz).time()
            
            # 05:00 ~ 09:30 : Premarket
            if dtime(5, 0) <= now_time < dtime(9, 30):
                return 'premarket'
            # 09:30 ~ 12:00 : Regular
            elif dtime(9, 30) <= now_time < dtime(12, 0):
                return 'regular'
            else:
                return 'closed'
        except Exception as e:
            logger.error(f"Mode detection error: {e}")
            return 'premarket'  # Safe default

    def switch_mode_if_needed(self):
        """Spec 2.3: Switch mode if needed and notify"""
        new_mode = self.get_current_trading_mode()
        
        if new_mode != self.current_mode:
            old_mode = self.current_mode
            self.current_mode = new_mode
            self.last_mode_change = datetime.now()
            self.stats['mode_switches'] += 1
            
            logger.info(f"🔄 Mode switch: {old_mode} → {new_mode}")
            
            if self.telegram_bot:
                mode_names = {
                    'premarket': '🔵 Pre-market (REST Polling)',
                    'regular': '⚡ Regular Hours (REST Polling)',
                    'closed': '😴 Sleep Mode'
                }
                message = (
                    f"🔄 Mode Switch\n"
                    f"{mode_names.get(old_mode, str(old_mode))} → {mode_names.get(new_mode, str(new_mode))}"
                )
                self.telegram_bot.send_message(message)
            
            self.save_state()
            
            if new_mode == 'closed':
                self.handle_sleep_mode()
            elif new_mode == 'regular':
                self.stop_websocket_mode()
            elif new_mode == 'premarket':
                self.stop_websocket_mode()
            
            return True
        
        return False

    def handle_sleep_mode(self):
        """Spec 2.2: Handle sleep mode (한국시간 01:00)"""
        logger.info("😴 슬립 모드 진입 - 한국시간 오전 01시")

        if hasattr(self, 'telegram_order_manager') and self.telegram_order_manager:
            try:
                cancelled_orders = self.telegram_order_manager.cancel_all_pending_orders()
                if cancelled_orders:
                    logger.info(f"🌙 [TG] 슬립 모드: {len(cancelled_orders)}개 대기 주문 취소됨.")
                    
                    message = f"😴 <b>슬립 모드 진입: {len(cancelled_orders)}개 주문 취소</b>\n\n"
                    message += "당일 대기 중이던 다음 텔레그램 주문이 자동 취소되었습니다:\n\n"
                    for order in cancelled_orders[:10]:
                        message += f"• {order['ticker']} @ ${order['target_price']:.2f} ({order['quantity']}주)\n"
                    if len(cancelled_orders) > 10:
                        message += f"...외 {len(cancelled_orders) - 10}건"
                    
                    if self.telegram_bot:
                        self.telegram_bot.send_message(message, force=True)
                
            except Exception as e:
                logger.error(f"❌ 슬립 모드 중 텔레그램 주문 취소 실패: {e}")
        else:
            logger.warning("⚠️ 텔레그램 주문 관리자(telegram_order_manager)가 SmartOrderMonitor에 연결되지 않았습니다.")
        
        if self.telegram_bot:
            self.telegram_bot.send_sleep_mode_notification(reason="normal")
        
        self.send_daily_trades_csv()

    def send_daily_trades_csv(self):
        """
        당일 매매 내역을 CSV 형식으로 텔레그램 전송
        """
        try:
            from datetime import datetime
            
            # 오늘 날짜 (한국시간 기준)
            from pytz import timezone
            kst = timezone('Asia/Seoul')
            today = datetime.now(kst).strftime("%Y%m%d")
            
            logger.info(f"📋 당일 매매 내역 조회 중: {today}")
            
            from order import inquire_ccnl
            
            df = inquire_ccnl(
                config=self.config,
                token_manager=self.token_manager,
                pdno="",
                ord_strt_dt=today,
                ord_end_dt=today,
                sll_buy_dvsn="01",  # 매도만
                ccld_nccs_dvsn="01",  # 체결만
                ovrs_excg_cd="%",
                sort_sqn="DS",
                ord_dt="",
                ord_gno_brno="",
                odno=""
            )
            
            if df is None or df.empty:
                logger.info("📋 당일 매매 내역 없음")
                return
            
            csv_lines = ["날짜,순서,장,종목,진입시각,수량,진입가,목표가,손절가,청산가,손익,감정,원칙준수,메모"]
            
            for i, row in df.iterrows():
                ticker = row.get('pdno', '')
                qty = row.get('ft_ccld_qty', '0')
                
                target_profit_rate = self.config.get('order_settings', {}).get('target_profit_rate', 6.0)
                profit_margin = target_profit_rate / 100
                
                sell_price = float(row.get('ft_ccld_unpr3', '0'))
                buy_price = sell_price / (1 + profit_margin)
                
                order_time = row.get('ord_tmd', '')
                if len(order_time) == 6:
                    time_formatted = f"{order_time[0:2]}:{order_time[2:4]}:{order_time[4:6]}"
                else:
                    time_formatted = order_time
                
                hour = int(order_time[0:2]) if len(order_time) >= 2 else 0
                minute = int(order_time[2:4]) if len(order_time) >= 4 else 0
                market = "프리" if (hour < 22) or (hour == 22 and minute < 30) else "정규"
                
                if buy_price > 0:
                    profit_pct = ((sell_price - buy_price) / buy_price) * 100
                    profit_str = f"{profit_pct:.2f}%"
                else:
                    profit_str = "0.00%"
                
                csv_line = f"{today[:4]}-{today[4:6]}-{today[6:]},{i+1},{market},{ticker},{time_formatted},{qty},{buy_price:.4f},{sell_price:.4f},,{sell_price:.4f},{profit_str},,,"
                csv_lines.append(csv_line)
            
            csv_text = "\n".join(csv_lines)
            
            if self.telegram_bot:
                message = f"""
📊 <b>당일 매매 내역 (엑셀 복사용)</b>

<pre>{csv_text}</pre>

✅ 위 내용을 복사해서 엑셀에 붙여넣으세요!
"""
                self.telegram_bot.send_message(message, parse_mode='HTML')
                logger.info(f"✅ 당일 매매 내역 전송 완료: {len(df)}건")
        
        except Exception as e:
            logger.error(f"❌ 당일 매매 내역 전송 오류: {e}")

    def start_websocket_mode(self):
        # 🔴 [v2.0] WebSocket 미사용
        logger.info("❌ WebSocket은 v2.0에서 사용되지 않습니다.")
        return

    def stop_websocket_mode(self):
        if self.ws_client:
            logger.info("⏸️ Stopping WebSocket mode...")
            self.ws_client.stop()
        self.ws_client = None

    def calculate_polling_interval(self, order_info):
        if self.current_mode == 'closed':
            return 3600  # Sleep mode
        
        if self.current_mode == 'regular':
            try:
                trading_tz = self.config.get('order_settings', {}).get('timezone', 'US/Eastern')
                tz = timezone(trading_tz)
                now_time = datetime.now(tz).time()
                
                high_start = dtime(9, 30)
                high_end = dtime(9, 40)
                
                if high_start <= now_time < high_end:
                    interval = self.config['polling']['regular']['interval_seconds'].get('high_activity', 3)
                    logger.debug(f"정규장 고활성 구간: {interval}초")
                    return interval
                
                interval = self.config['polling']['regular']['interval_seconds'].get('low_activity', 10)
                logger.debug(f"정규장 저활성 구간: {interval}초")
                return interval
            except Exception as e:
                logger.error(f"정규장 폴링 주기 계산 오류: {e}")
                return 5
        
        if self.current_mode == 'premarket':
            try:
                interval = self.premarket_config.get('interval_seconds', {}).get('uniform', 4)
                logger.debug(f"프리마켓: {interval}초")
                return interval
            except Exception as e:
                logger.error(f"프리마켓 폴링 주기 계산 오류: {e}")
                return 4
        
        return 5

    def can_make_request(self):
        self.reset_counters_if_needed()
        if self.current_mode == 'closed':
            return False
        
        now_time = time.time()
        min_interval = self.rate_config.get('min_request_interval', 0.07)
        
        if now_time - self.last_request_time < min_interval:
            return False
        
        # [수정] 연속 요청 제한 완화 (10 -> 20)
        consecutive_limit = self.rate_config.get('consecutive_limit', 20)
        if self.consecutive_requests >= consecutive_limit:
            logger.warning(f"⚠️ Consecutive limit reached: {self.consecutive_requests}/{consecutive_limit}")
            time.sleep(1)
            self.consecutive_requests = 0
        
        if self.daily_api_count >= self.rate_config['daily_limit']:
            logger.warning(f"⚠️ Daily API limit reached: {self.daily_api_count}/{self.rate_config['daily_limit']}")
            return False
        
        if self.hourly_api_count >= self.rate_config['hourly_limit']:
            logger.warning(f"⚠️ Hourly API limit reached: {self.hourly_api_count}/{self.rate_config['hourly_limit']}")
            return False
            
        utilization_pct = (self.daily_api_count / self.rate_config['daily_limit']) * 100
        if utilization_pct >= 90 and self.telegram_bot:
            if hasattr(self.telegram_bot, 'send_rate_limit_warning'):
                self.telegram_bot.send_rate_limit_warning(
                    self.daily_api_count,
                    self.rate_config['daily_limit'],
                    utilization_pct
                )
        return True

    def reset_counters_if_needed(self):
        try:
            trading_tz = self.config.get('order_settings', {}).get('timezone', 'US/Eastern')
            tz = timezone(trading_tz)
            now = datetime.now(tz)
        except Exception:
            logger.warning("pytz.timezone('US/Eastern') 로드 실패. 로컬 시간대로 Fallback.")
            now = datetime.now()
        
        if self.last_reset_date is None or now.date() != self.last_reset_date:
            logger.info(f"📊 Daily reset (ET 기준: {now.date()})")
            self.daily_api_count = 0
            self.last_reset_date = now.date()
            self.stats['successful_detections'] = 0
            self.stats['ws_detections'] = 0
            self.stats['premarket_calls'] = 0
            self.stats['rate_limit_violations'] = 0
            self.stats['api_errors'] = {}
            
            self.processed_orders.clear()
            self.processed_ws_orders.clear()
            self.failed_orders.clear()
            logger.info("🔄 Processed orders list cleared (ET 기준)")
        
        if now.hour != self.last_hour_reset:
            logger.debug(f"📊 Hourly reset: {self.hourly_api_count} calls")
            self.hourly_api_count = 0
            self.last_hour_reset = now.hour
            self.consecutive_requests = 0

    def handle_api_error(self, error_code, error_msg):
        self.stats['api_errors'][error_code] = self.stats['api_errors'].get(error_code, 0) + 1
        
        if error_code in ['EGW00101', 'EGW00102']:
            self.stats['rate_limit_violations'] += 1
            wait_time = self.rate_config.get('cooldown_seconds', 60)
            logger.error(f"🚨 Rate Limit detected! Waiting {wait_time}s (Error: {error_code})")
            if self.telegram_bot:
                message = (
                    f"⚠️ Rate Limit Detected\n"
                    f"📛 Error: {error_code}\n"
                    f"⏰ Waiting: {wait_time}s\n"
                    f"📊 Daily calls: {self.daily_api_count}"
                )
                self.telegram_bot.send_message(message)
            time.sleep(wait_time)
            return True
        elif error_code in ['EGW90001']:
            logger.warning(f"⚠️ Temporary error: {error_code} - {error_msg}")
            time.sleep(5)
            return False
        else:
            logger.error(f"❌ API error: {error_code} - {error_msg}")
        return False

    def check_order_status(self, order_no):
        if not self.can_make_request():
            return None
        
        try:
            url = f"{self.config['api']['base_url']}/uapi/overseas-stock/v1/trading/inquire-ccnl"
            token = self.token_manager.get_access_token()
            if not token:
                logger.error("❌ No access token available")
                return None
            
            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": self.config['api_key'],
                "appsecret": self.config['api_secret'],
                "tr_id": "TTTS3035R",
                "custtype": "P"
            }
            
            # [수정] 날짜 포맷 오타 수정 (%Ym%d -> %Y%m%d)
            today = datetime.now().strftime("%Y%m%d")
            
            # ✅ [수정] 필수 파라미터 추가 (API 오류 방지)
            params = {
                "CANO": self.config['cano'],
                "ACNT_PRDT_CD": self.config['acnt_prdt_cd'],
                "PDNO": "",
                "ORD_STRT_DT": today,
                "ORD_END_DT": today,
                "SLL_BUY_DVSN": "02",
                "CCLD_NCCS_DVSN": "01",
                "OVRS_EXCG_CD": "NASD",
                "SORT_SQN": "DS",        # 필수 추가
                "ORD_DT": "",            # 필수 추가
                "ORD_GNO_BRNO": "",      # 필수 추가
                "ODNO": "",              # 필수 추가
                "CTX_AREA_NK200": "",
                "CTX_AREA_FK200": ""
            }
            
            request_start = time.time()
            response = requests.get(url, headers=headers, params=params, timeout=15)
            
            self.last_request_time = time.time()
            with self._counter_lock:
                self.consecutive_requests += 1
                self.daily_api_count += 1
                self.hourly_api_count += 1
                self.stats['total_requests'] += 1
                self.stats['premarket_calls'] += 1
            
            if response.status_code != 200:
                logger.error(f"❌ HTTP error: {response.status_code}")
                return None
            
            data = response.json()
            error_code = data.get("msg_cd", "")
            if error_code and error_code != "MCA00000":
                error_msg = data.get('msg1', 'Unknown error')
                if self.handle_api_error(error_code, error_msg):
                    return None
            
            if data.get("rt_cd") != "0":
                logger.error(f"❌ API error: {data.get('msg1', 'Unknown')}")
                return None
            
            for item in data.get("output", []):
                if item.get("odno") == order_no:
                    ccld_qty = item.get("ft_ccld_qty", "0")
                    ccld_unpr = item.get("ft_ccld_unpr3", "0")
                    return {
                        'status': '02',
                        'filled_qty': int(ccld_qty) if ccld_qty else 0,
                        'filled_price': float(ccld_unpr) if ccld_unpr else 0.0,
                        'order_data': item
                    }
            return {'status': 'not_found', 'filled_qty': 0, 'filled_price': 0.0}
        
        except Exception as e:
            logger.error(f"❌ Status check error: {e}")
            with self._counter_lock:
                self.stats['consecutive_api_errors'] += 1
            if self.stats['consecutive_api_errors'] >= 10:
                logger.critical(f"🚨 연속 {self.stats['consecutive_api_errors']}회 API 오류 - 시스템 종지")
                if self.telegram_bot:
                    self.telegram_bot.send_error_notification(
                        f"연속 API 오류 {self.stats['consecutive_api_errors']}회\n시스템을 안전하게 종료합니다.",
                        level="critical"
                    )
                self.stop()
                import sys
                sys.exit(1)
            return None

    def execute_auto_sell(self, order_info, filled_price, order_no=None):
        try:
            if not self.trade_counter.can_trade():
                logger.warning(f"⚠️ [SELL] 매매 한도 도달 (방어적 체크). {order_info['ticker']} ({order_no}) 매도 주문을 실행하지 않습니다.")
                if order_no:
                    self.processed_orders.add(order_no)
                return False

            from order import place_sell_order
            current_mode = self.get_current_trading_mode()
            target_profit_rate = self.config.get('order_settings', {}).get('target_profit_rate', 6.0)
            profit_margin = target_profit_rate / 100
            
            # [수정] $1 기준 조건부 반올림 로직 적용
            raw_sell_price = filled_price * (1 + profit_margin)
            if raw_sell_price >= 1.0:
                sell_price = round(raw_sell_price, 2)
            else:
                sell_price = round(raw_sell_price, 4)
            
            execution_data = {
                'ticker': order_info['ticker'],
                'quantity': order_info['quantity'],
                'price': filled_price
            }
            
            logger.info(f"🎯 Fill detected! {execution_data['ticker']} ${filled_price} → Sell @ ${sell_price} (Mode: {current_mode})")
            
            success = place_sell_order(
                self.config,
                self.token_manager,
                execution_data,
                self.telegram_bot
            )

            if success:
                sell_order_no = success.get('order_no') if isinstance(success, dict) else None
                if sell_order_no:
                    self.sell_monitor.add_order(sell_order_no, {
                        'ticker': order_info['ticker'],
                        'quantity': order_info['quantity'],
                        'buy_price': filled_price,
                        'sell_price': sell_price,
                        'source': order_info.get('source', 'auto'),
                        'created_at': datetime.now()
                    })
                    if current_mode == 'regular':
                        self.stats['ws_detections'] += 1
                    else:
                        self.stats['successful_detections'] += 1
                    if order_no:
                        self.processed_orders.add(order_no)
                    logger.info(f"📝 매도 주문 접수: {execution_data['ticker']} (체결 감시 중)")
                else:
                    logger.error("❌ 주문번호 없음 - 체결 감시 불가")
                    if order_no:
                        self.failed_orders[order_no] = (datetime.now(), 'Sell order_no missing')
                    return False
            else:
                if order_no:
                    self.failed_orders[order_no] = (datetime.now(), 'Sell failed')
                    logger.warning(f"⚠️ Order {order_no} marked as failed")
                return False
            
            return True
        
        except Exception as e:
            logger.error(f"❌ Auto-sell execution error: {e}")
            return False

    def handle_ws_message(self, message):
        if self.current_mode != 'closed':
            logger.debug(f"WS message ignored (v2.0): {message[:50]}...")
        return

    def scan_for_new_buy_orders(self):
        try:
            if not self.trade_counter.can_trade():
                return

            if not self.can_make_request():
                return
            
            url = f"{self.config['api']['base_url']}/uapi/overseas-stock/v1/trading/inquire-ccnl"
            token = self.token_manager.get_access_token()
            if not token:
                logger.error("❌ No access token available")
                return
            
            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": self.config['api_key'],
                "appsecret": self.config['api_secret'],
                "tr_id": "TTTS3035R",
                "custtype": "P"
            }
            
            # [수정] 날짜 포맷 오타 수정 (%Ym%d -> %Y%m%d)
            today = datetime.now().strftime("%Y%m%d")
            
            # ✅ [수정] 필수 파라미터 추가 (API 오류 방지)
            params = {
                "CANO": self.config['cano'],
                "ACNT_PRDT_CD": self.config['acnt_prdt_cd'],
                "PDNO": "",
                "ORD_STRT_DT": today,
                "ORD_END_DT": today,
                "SLL_BUY_DVSN": "02",
                "CCLD_NCCS_DVSN": "01",
                "OVRS_EXCG_CD": "NASD",
                "SORT_SQN": "DS",        # 필수 추가
                "ORD_DT": "",            # 필수 추가
                "ORD_GNO_BRNO": "",      # 필수 추가
                "ODNO": "",              # 필수 추가
                "CTX_AREA_NK200": "",
                "CTX_AREA_FK200": ""
            }
            
            response = requests.get(url, headers=headers, params=params, timeout=15)
            self.last_request_time = time.time()
            with self._counter_lock:
                self.consecutive_requests += 1
                self.daily_api_count += 1
                self.hourly_api_count += 1
                self.stats['total_requests'] += 1
            
            if response.status_code != 200:
                logger.error(f"❌ Buy detection HTTP error: {response.status_code}")
                return
            
            data = response.json()
            if data.get("rt_cd") != "0":
                return
            
            for order in data.get("output", []):
                order_no = order.get("odno", "")
                if order_no in self.monitoring_orders: continue
                if order_no in self.processed_ws_orders: continue
                if order_no in self.processed_orders: continue
                if order_no in self.failed_orders:
                    fail_time, reason = self.failed_orders[order_no]
                    if (datetime.now() - fail_time).total_seconds() < 3600:
                        continue
                    else:
                        del self.failed_orders[order_no]
                
                ticker = order.get("pdno", "")
                ccld_qty = int(order.get("ft_ccld_qty", "0"))
                ccld_price = float(order.get("ft_ccld_unpr3", "0"))
                
                if ccld_qty > 0 and ccld_price > 0:
                    if not self.trade_counter.can_trade():
                        logger.warning(f"⚠️ [POLL] 매매 한도 도달. {ticker} ({order_no}) 매도 주문을 실행하지 않습니다.")
                        if self.telegram_bot:
                            self.telegram_bot.send_sleep_mode_notification(
                                reason="trade_limit",
                                trade_stats=self.trade_counter.get_stats()
                            )
                        self.processed_orders.add(order_no)
                        break
                        
                    logger.info(f"🎉 [POLL] New buy fill detected! {order_no}: {ticker} {ccld_qty} @ ${ccld_price}")
                    order_info = {
                        'ticker': ticker,
                        'quantity': ccld_qty,
                        'buy_price': ccld_price,
                        'created_at': datetime.now(),
                        'source': 'auto'
                    }
                    
                    success = self.execute_auto_sell(order_info, ccld_price, order_no)
                    if success:
                        logger.info(f"✅ [POLL] Auto-sell order placed: {ticker}")
                    else:
                        logger.error(f"❌ [POLL] Auto-sell failed: {ticker}. Not adding to monitoring (already sold).")
                        self.failed_orders[order_no] = (datetime.now(), 'Already sold')
                        self.processed_orders.add(order_no)
                        if self.telegram_bot:
                            if hasattr(self.telegram_bot, 'send_info_notification'):
                                self.telegram_bot.send_info_notification(f"매도 대상 없음: {ticker} (이미 매도됨)")
                            else:
                                self.telegram_bot.send_message(f"ℹ️ 시스템 정보\n매도 대상 없음: {ticker} (이미 매도됨)")
                        logger.info(f"🗑️ Order {order_no} not added to monitoring (already sold)")
        
        except Exception as e:
            logger.error(f"❌ Buy detection scan error: {e}")

    def cleanup_expired_orders(self):
        now = datetime.now()
        expired_orders = []
        with self.lock:
            for order_no, info in self.monitoring_orders.items():
                created_at = info['created_at']
                if isinstance(created_at, str):
                    created_at = datetime.fromisoformat(created_at)
                age_hours = (now - created_at).total_seconds() / 3600
                current_mode = self.get_current_trading_mode()
                max_hours = 0.5 if current_mode == 'premarket' else 2
                if age_hours > max_hours:
                    expired_orders.append(order_no)
            
            for order_no in expired_orders:
                order_info = self.monitoring_orders.pop(order_no, None)
                if order_info:
                    created_at = order_info['created_at']
                    if isinstance(created_at, str):
                        created_at = datetime.fromisoformat(created_at)
                    age_hours = (now - created_at).total_seconds() / 3600
                    logger.info(f"⏰ Order expired: {order_no} ({age_hours:.1f} hours)")
            
            if expired_orders:
                self.save_state()

    def smart_monitor_loop(self):
        """
        Spec 3장: Main monitoring loop
        """
        logger.info("🚀 Smart Order Monitor started (04:00 ET Start)")
        
        while self.is_running:
            try:
                # 1단계: 모드 전환 체크
                if self.switch_mode_if_needed():
                    if self.current_mode == 'closed':
                        logger.info("😴 슬립 모드 진입 확인")
                        time.sleep(300)
                        logger.info("🛑 시스템 종료 시작")
                        logger.info("smart_monitor_loop: 루프를 종료합니다 (closed)")
                        break
                
                # 2단계: 백업 체크 (운영 시간 확인)
                if not self.should_system_run():
                    try:
                        trading_tz = self.config.get('order_settings', {}).get('timezone', 'US/Eastern')
                        tz = timezone(trading_tz)
                        now_time = datetime.now(tz).time()
                        start_time = dtime(5, 0)  # 05:00 ET
                        
                        # 04:30 ~ 05:00 사이라면 종료하지 않고 대기 (30초)
                        if dtime(4, 30) <= now_time < start_time:
                            logger.info(f"⏳ 운영 시작 대기 중... (현재: {now_time.strftime('%H:%M')} ET)")
                            time.sleep(30)
                            continue
                    except Exception:
                        pass
                    
                    # 그 외의 시간은 진짜 종료
                    logger.warning("⚠️ 운영 시간 외 감지 (백업 체크)")
                    if self.current_mode != 'closed':
                        self.current_mode = 'closed'
                        self.handle_sleep_mode()
                    logger.info("smart_monitor_loop: 루프를 종료합니다 (should_system_run)")
                    break
                
                # ▼ Pre-market / Regular REST polling logic ▼
                current_time = time.time()
                if current_time - self.last_buy_scan > 10:
                    if self.trade_counter.can_trade():
                        logger.debug("🔍 Scanning for new buy orders...")
                        self.scan_for_new_buy_orders()
                    else:
                        logger.debug("⏸️ 매매 한도 도달. 신규 매수 탐색 스킵.")
                    self.last_buy_scan = current_time
                
                if not self.monitoring_orders:
                    sleep_time = 30 if self.current_mode == 'premarket' else 10
                    time.sleep(sleep_time)
                    continue
                
                self.cleanup_expired_orders()
                processed_count = 0
                
                with self.lock:
                    orders_to_check = list(self.monitoring_orders.items())
                
                for order_no, order_info in orders_to_check:
                    if not self.is_running:
                        break
                    
                    polling_interval = self.calculate_polling_interval(order_info)
                    now = datetime.now()
                    
                    last_checked_str = order_info.get('last_checked')
                    if last_checked_str:
                        last_checked_dt = datetime.fromisoformat(last_checked_str)
                        if (now - last_checked_dt).total_seconds() < polling_interval:
                            continue
                    
                    status_info = self.check_order_status(order_no)
                    
                    with self.lock:
                        if order_no in self.monitoring_orders:
                            self.monitoring_orders[order_no]['last_checked'] = now.isoformat()
                            self.monitoring_orders[order_no]['check_count'] += 1
                    
                    processed_count += 1
                    
                    if status_info is None:
                        continue
                    
                    if (status_info['status'] in ['02', 'Filled', 'Complete'] and 
                        status_info['filled_qty'] > 0):
                        
                        if not self.trade_counter.can_trade():
                            logger.warning(f"⚠️ [POLL] 매매 한도 도달. {order_info['ticker']} ({order_no}) 매도 주문을 실행하지 않습니다.")
                            if self.telegram_bot:
                                self.telegram_bot.send_sleep_mode_notification(
                                    reason="trade_limit",
                                    trade_stats=self.trade_counter.get_stats()
                                )
                            with self.lock:
                                self.monitoring_orders.pop(order_no, None)
                            self.processed_orders.add(order_no)
                            self.save_state()
                            continue
                        
                        logger.info(
                            f"🎉 [POLL] Fill complete: {order_no} "
                            f"(Mode: {self.current_mode}, Checks: {order_info['check_count']})"
                        )
                        
                        success = self.execute_auto_sell(order_info, status_info['filled_price'], order_no)
                        
                        with self.lock:
                            if success:
                                self.monitoring_orders.pop(order_no, None)
                            else:
                                logger.warning(f"⚠️ [POLL] Fill detected but sell failed: {order_no}. Removing from monitoring.")
                                self.monitoring_orders.pop(order_no, None)
                                self.processed_orders.add(order_no)
                                if self.telegram_bot:
                                    if hasattr(self.telegram_bot, 'send_info_notification'):
                                        self.telegram_bot.send_info_notification(
                                            f"매도 대상 없음: {order_info['ticker']} (이미 매도됨)"
                                        )
                                    else:
                                        self.telegram_bot.send_message(
                                            f"ℹ️ 시스템 정보\n매도 대상 없음: {order_info['ticker']} (이미 매도됨)"
                                        )
                                logger.info(f"🗑️ Order {order_no} not added to monitoring (already sold)")
                            
                            self.save_state()
                    
                    time.sleep(max(1, self.rate_config.get('min_request_interval', 0.07)))
                
                sleep_interval = 2 if self.current_mode == 'premarket' else 1
                time.sleep(sleep_interval)
                
                if processed_count > 0 and processed_count % 20 == 0:
                    self.save_state()
                
                if self.stats['total_requests'] > 0 and self.stats['total_requests'] % 100 == 0:
                    rate_limit_rate = (self.stats['rate_limit_violations'] / self.stats['total_requests']) * 100
                    logger.info(
                        f"📊 Stats - Requests: {self.stats['total_requests']}, "
                        f"Success: {self.stats['successful_detections']}, "
                        f"WS: {self.stats['ws_detections']}, "
                        f"Rate Limit: {rate_limit_rate:.1f}%"
                    )
            
            except Exception as e:
                logger.error(f"❌ Main loop error: {e}")
                time.sleep(30)
        
        logger.info("🛑 Smart Order Monitor stopped")
        self.is_running = False

    def start(self):
        """Start monitoring system"""
        if self.is_running:
            logger.warning("⚠️ Monitoring already running")
            return
        
        # [수정] 시작 모드 강제 업데이트
        self.current_mode = self.get_current_trading_mode()
        self.is_running = True
        
        self.monitor_thread = threading.Thread(target=self.smart_monitor_loop, daemon=True)
        self.monitor_thread.start()
        
        self.sell_monitor.start()
        
        logger.info(f"🚀 Smart Order Monitor started - Initial mode: {self.current_mode}")

    def stop(self):
        """Stop monitoring system"""
        if not self.is_running:
            return
        
        logger.info("🛑 Stopping monitor...")
        self.is_running = False
        
        self.stop_websocket_mode()
        
        if hasattr(self, 'sell_monitor'):
            self.sell_monitor.stop()
        
        self.save_state()
        
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=10)
        
        logger.info(
            f"🛑 Monitor stopped - Final stats: "
            f"Requests: {self.stats['total_requests']}, "
            f"Success: {self.stats['successful_detections']}, "
            f"WS: {self.stats['ws_detections']}"
        )

    def add_order_to_monitor(self, order_no, ticker, quantity, buy_price, source='auto'):
        """
        [v2.0 신규] 외부에서 주문을 모니터링 목록에 추가
        """
        with self.lock:
            if order_no in self.monitoring_orders:
                logger.warning(f"⚠️ 이미 모니터링 중인 주문: {order_no}")
                return False
            
            self.monitoring_orders[order_no] = {
                'ticker': ticker,
                'quantity': quantity,
                'buy_price': buy_price,
                'source': source,
                'created_at': datetime.now(),
                'last_checked': None,
                'check_count': 0,
                'status': 'monitoring'
            }
            
            self.save_state()
            
            logger.info(
                f"📝 [{source.upper()}] 모니터링 등록: "
                f"{order_no} ({ticker} {quantity}주 @ ${buy_price:.2f})"
            )
            return True

    def get_monitoring_count(self):
        """Get current monitoring order count"""
        with self.lock:
            return len(self.monitoring_orders)

    def get_detailed_stats(self):
        """Get detailed statistics"""
        current_mode = self.get_current_trading_mode()
        
        return {
            'monitoring_count': self.get_monitoring_count(),
            'current_mode': current_mode,
            'daily_api_calls': self.daily_api_count,
            'hourly_api_calls': self.hourly_api_count,
            'total_requests': self.stats['total_requests'],
            'successful_detections': self.stats['successful_detections'],
            'ws_detections': self.stats.get('ws_detections', 0),
            'mode_switches': self.stats['mode_switches'],
            'premarket_calls': self.stats.get('premarket_calls', 0),
            'rate_limit_violations': self.stats['rate_limit_violations'],
            'api_errors': self.stats['api_errors'],
            'consecutive_requests': self.consecutive_requests
        }
