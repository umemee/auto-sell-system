# smart_order_monitor.py - v3.0 기획서 적용 (DailyTradeCounter 진입/청산 구분)
# Specification v1.1 Compliant
# [v2.7 수정] Premarket 모드에서도 매도 모니터링 확실하게 실행되도록 루프 구조 개선
# [v2.8 수정] API 필수 파라미터(SORT_SQN 등) 추가 및 날짜 포맷 오타 수정
# [v2.9 수정] 스마트 모니터 루프 종료(break) 제거 -> 대기(continue) 로직으로 변경 (자동 재시작 문제 해결)
# [v3.0 수정] 주말(금요일 장마감 후 ~ 일요일) 슬립 모드 진입 시 시스템 완전 종료 (sys.exit)
# [v3.0.1 수정] DailyTradeCounter 진입/청산 구분 카운트 추가 (완전 자동매매 지원)

import requests
import json
import logging
import time
import threading
import os
import fcntl
import sys
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
# 🔴 [v3.0.1 수정] DailyTradeCounter - 진입/청산 구분 카운트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class DailyTradeCounter:
    """
    일일 매매 횟수 카운터 (v3.0 완전 자동매매 지원)
    
    v3.0 변경사항:
    - 진입/청산 구분 카운트 (각각 6회 제한)
    - 기존 통합 카운트 유지 (v1.x/v2.0 호환성)
    - threading.Lock()으로 Race Condition 방지
    """
    def __init__(self, telegram_bot=None):
        self.telegram_bot = telegram_bot
        self.date = None
        
        # 🔴 [v3.0.1] 진입/청산 구분 카운트
        self.entry_count = 0      # 진입 횟수 (매수)
        self.exit_count = 0       # 청산 횟수 (손절/익절)
        self.MAX_ENTRIES = 6      # 하루 최대 진입 6회
        self.MAX_EXITS = 6        # 하루 최대 청산 6회
        
        # 기존 통합 카운트 (v1.x/v2.0 호환성 유지)
        self.count = 0
        self.MAX_TRADES = 8
        
        # 🆕 [v2.0] 소스별 카운트 (유지)
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
            
            # 🔴 [v3.0.1] 진입/청산 카운트 리셋
            self.entry_count = 0
            self.exit_count = 0
            
            # 기존 통합 카운트 리셋 (호환성)
            self.count = 0
            self.auto_count = 0
            self.telegram_count = 0
            
            logger.info(
                f"📅 새로운 날: {today} (ET). "
                f"매매 카운터 리셋 (진입 {self.MAX_ENTRIES}회, 청산 {self.MAX_EXITS}회)."
            )
            
            # ⚡ [수정] 텔레그램 슬립 모드 플래그 리셋
            if self.telegram_bot and hasattr(self.telegram_bot, 'reset_sleep_mode_flag'):
                try:
                    self.telegram_bot.reset_sleep_mode_flag()
                except Exception as e:
                    logger.error(f"슬립 모드 플래그 리셋 실패: {e}")

    def can_trade(self):
        """매매 가능 여부 확인 (v1.x/v2.0 호환성 유지)"""
        with self.lock:
            self._reset_if_new_day()
            
            if self.count >= self.MAX_TRADES:
                logger.warning(f"⚠️ 오늘 매매 {self.count}/{self.MAX_TRADES}회 도달. 더 이상 매매하지 않습니다.")
                return False
            return True

    def increment(self, source='auto'):
        """
        매매 횟수 증가 (v1.x/v2.0 호환성 유지)
        
        Args:
            source (str): 'auto' 또는 'telegram'
        """
        with self.lock:
            self._reset_if_new_day() # 날짜 변경 보장
            
            # 🆕 [v2.0] 한도 도달 시 방어적 코드
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

    # 🔴 [v3.0.1 신규] 진입 가능 여부 체크
    def can_enter(self):
        """진입 가능 여부 확인 (v3.0 완전 자동매매용)"""
        with self.lock:
            self._reset_if_new_day()
            
            if self.entry_count >= self.MAX_ENTRIES:
                logger.warning(
                    f"⚠️ 오늘 진입 {self.entry_count}/{self.MAX_ENTRIES}회 도달. "
                    f"더 이상 진입하지 않습니다."
                )
                return False
            return True

    # 🔴 [v3.0.1 신규] 청산 가능 여부 체크
    def can_exit(self):
        """청산 가능 여부 확인 (v3.0 완전 자동매매용)"""
        with self.lock:
            self._reset_if_new_day()
            
            if self.exit_count >= self.MAX_EXITS:
                logger.warning(
                    f"⚠️ 오늘 청산 {self.exit_count}/{self.MAX_EXITS}회 도달. "
                    f"더 이상 청산하지 않습니다."
                )
                return False
            return True

    # 🔴 [v3.0.1 신규] 진입 카운트 증가
    def increment_entry(self, source='auto'):
        """
        진입 횟수 증가 (v3.0 완전 자동매매용)
        
        Args:
            source (str): 'auto', 'telegram', 또는 'v3_auto'
        """
        with self.lock:
            self._reset_if_new_day()
            
            if self.entry_count >= self.MAX_ENTRIES:
                logger.warning(
                    f"⚠️ 이미 진입 한도({self.MAX_ENTRIES}회) 도달. "
                    f"카운트: {self.entry_count}"
                )
                return False
            
            self.entry_count += 1
            
            # 기존 통합 카운트도 증가 (호환성)
            self.count += 1
            if source == 'auto' or source == 'v3_auto':
                self.auto_count += 1
            elif source == 'telegram':
                self.telegram_count += 1
            
            logger.info(
                f"✅ [{source.upper()}] 진입 완료: {self.entry_count}/{self.MAX_ENTRIES}회 "
                f"(총 매매: {self.count}/{self.MAX_TRADES}회)"
            )
            
            # 진입 한도 도달 알림
            if self.entry_count >= self.MAX_ENTRIES:
                if self.telegram_bot and hasattr(self.telegram_bot, 'send_message'):
                    try:
                        self.telegram_bot.send_message(
                            f"🚫 오늘 진입 한도 도달 ({self.MAX_ENTRIES}회)\n\n"
                            f"• 진입: {self.entry_count}회\n"
                            f"• 청산: {self.exit_count}회\n\n"
                            f"더 이상 진입하지 않습니다. (청산은 계속됩니다)"
                        )
                    except Exception as e:
                        logger.error(f"진입 한도 알림 실패: {e}")
            
            return True

    # 🔴 [v3.0.1 신규] 청산 카운트 증가
    def increment_exit(self, source='auto'):
        """
        청산 횟수 증가 (v3.0 완전 자동매매용)
        
        Args:
            source (str): 'auto', 'telegram', 또는 'v3_auto'
        """
        with self.lock:
            self._reset_if_new_day()
            
            if self.exit_count >= self.MAX_EXITS:
                logger.warning(
                    f"⚠️ 이미 청산 한도({self.MAX_EXITS}회) 도달. "
                    f"카운트: {self.exit_count}"
                )
                return False
            
            self.exit_count += 1
            
            # 기존 통합 카운트도 증가 (호환성)
            self.count += 1
            if source == 'auto' or source == 'v3_auto':
                self.auto_count += 1
            elif source == 'telegram':
                self.telegram_count += 1
            
            logger.info(
                f"✅ [{source.upper()}] 청산 완료: {self.exit_count}/{self.MAX_EXITS}회 "
                f"(총 매매: {self.count}/{self.MAX_TRADES}회)"
            )
            
            # 청산 한도 도달 알림
            if self.exit_count >= self.MAX_EXITS:
                if self.telegram_bot and hasattr(self.telegram_bot, 'send_message'):
                    try:
                        self.telegram_bot.send_message(
                            f"🚫 오늘 청산 한도 도달 ({self.MAX_EXITS}회)\n\n"
                            f"• 진입: {self.entry_count}회\n"
                            f"• 청산: {self.exit_count}회\n\n"
                            f"더 이상 청산하지 않습니다. (진입은 계속됩니다)"
                        )
                    except Exception as e:
                        logger.error(f"청산 한도 알림 실패: {e}")
            
            return True

    # 🔴 [v3.0.1 수정] 통계 메서드 업그레이드
    def get_stats(self):
        """소스별 통계 반환 (v3.0 진입/청산 포함)"""
        with self.lock:
            self._reset_if_new_day()
            
            return {
                # 기존 통합 통계 (v1.x/v2.0 호환성)
                'total': self.count,
                'auto': self.auto_count,
                'telegram': self.telegram_count,
                'remaining': self.MAX_TRADES - self.count,
                'max_trades': self.MAX_TRADES,
                
                # 🔴 [v3.0.1 신규] 진입/청산 통계
                'entry': self.entry_count,
                'exit': self.exit_count,
                'max_entries': self.MAX_ENTRIES,
                'max_exits': self.MAX_EXITS,
                'remaining_entries': self.MAX_ENTRIES - self.entry_count,
                'remaining_exits': self.MAX_EXITS - self.exit_count
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
    [v3.0.1 수정] DailyTradeCounter v3.0 버전 사용 (진입/청산 구분)
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
        
        # 🔴 [v3.0.1 수정] 일일 매매 카운터 초기화 (진입/청산 구분 지원)
        self.trade_counter = DailyTradeCounter(self.telegram_bot)
        
        # 🔴 [v2.0 수정] 매도 체결 감시자 초기화 (공유 카운터 전달)
        self.sell_monitor = SellOrderMonitor(
            config=self.config,
            token_manager=self.token_manager,
            telegram_bot=self.telegram_bot,
            trade_counter=self.trade_counter # 🆕 공유 인스턴스 전달
        )
        logger.info("✅ SellOrderMonitor 통합 완료 (v3.0.1)")
        
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
        with self.lock:
            try:
                if os.path.exists(self.state_file):
                    with open(self.state_file, 'r', encoding='utf-8') as f:
                        state = json.load(f)
                    
                    # Only restore orders from last 1 hour
                    cutoff_time = datetime.now() - timedelta(hours=1)
                    
                    monitoring_orders = state.get('monitoring_orders', {})
                    for order_no, order_info in monitoring_orders.items():
                        try:
                            created_at_str = order_info.get('created_at')
                            if created_at_str:
                                created_at = datetime.fromisoformat(created_at_str)
                                if created_at > cutoff_time:
                                    self.monitoring_orders[order_no] = order_info
                                    logger.info(f"📋 Restored order from state: {order_no}")
                        except Exception as e:
                            logger.warning(f"⚠️ Failed to restore order {order_no}: {e}")
                    
                    self.processed_orders = set(state.get('processed_orders', []))
                    logger.info(f"📋 Loaded {len(self.processed_orders)} processed orders")
            
            except FileNotFoundError:
                logger.info("📋 No state file found, starting fresh")
            except json.JSONDecodeError as e:
                logger.error(f"❌ State file corrupted: {e}")
            except Exception as e:
                logger.error(f"❌ Failed to load state: {e}")

    def save_state(self):
        """Spec 7.1: Save current state to file"""
        with self.lock:
            try:
                state = {
                    'monitoring_orders': {},
                    'processed_orders': list(self.processed_orders)
                }
                
                for order_no, order_info in self.monitoring_orders.items():
                    serializable_info = order_info.copy()
                    if isinstance(serializable_info.get('created_at'), datetime):
                        serializable_info['created_at'] = serializable_info['created_at'].isoformat()
                    if 'last_checked' in serializable_info and isinstance(serializable_info['last_checked'], datetime):
                        serializable_info['last_checked'] = serializable_info['last_checked'].isoformat()
                    state['monitoring_orders'][order_no] = serializable_info
                
                temp_file = f"{self.state_file}.tmp"
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(state, f, indent=2, ensure_ascii=False)
                
                os.replace(temp_file, self.state_file)
            
            except Exception as e:
                logger.error(f"❌ Failed to save state: {e}")

    def cleanup_expired_orders(self):
        """Remove orders older than 24 hours"""
        with self.lock:
            cutoff_time = datetime.now() - timedelta(hours=24)
            expired_orders = []
            
            for order_no, order_info in self.monitoring_orders.items():
                created_at_str = order_info.get('created_at')
                if created_at_str:
                    try:
                        if isinstance(created_at_str, str):
                            created_at = datetime.fromisoformat(created_at_str)
                        else:
                            created_at = created_at_str
                        
                        if created_at < cutoff_time:
                            expired_orders.append(order_no)
                    except Exception as e:
                        logger.error(f"Failed to parse created_at for {order_no}: {e}")
            
            for order_no in expired_orders:
                self.monitoring_orders.pop(order_no, None)
                logger.info(f"🗑️ Removed expired order: {order_no}")
            
            if expired_orders:
                self.save_state()

    def get_current_trading_mode(self):
        """
        기획서 2.2: 현재 거래 모드 판별
        
        Returns:
            str: 'premarket', 'regular', 'closed'
        """
        try:
            trading_tz = self.config.get('order_settings', {}).get('timezone', 'US/Eastern')
            tz = timezone(trading_tz)
            now = datetime.now(tz)
            current_time = now.time()
            
            # [v2.4 수정] 기획서와 일치 (실제 04:00 ET 시작)
            premarket_start = dtime(4, 0)
            regular_start = dtime(9, 30)
            system_end = dtime(12, 0)
            
            if premarket_start <= current_time < regular_start:
                return 'premarket'
            elif regular_start <= current_time < system_end:
                return 'regular'
            else:
                return 'closed'
        
        except Exception as e:
            logger.error(f"Failed to determine trading mode: {e}")
            return 'closed'

    def scan_for_new_buy_orders(self):
        """
        Spec 3.2: Scan for new buy orders using CCNL API
        """
        if not self.can_make_request():
            return
        
        # ✅ [수정] 토큰 확인 (Race Condition 방지)
        try:
            access_token = self.token_manager.get_access_token()
            if not access_token:
                logger.debug("⚠️ Access Token 없음, 매수 주문 스캔 건너뜀")
                return
        except Exception as e:
            logger.debug(f"⚠️ 토큰 확인 실패: {e}, 매수 주문 스캔 건너뜀")
            return
        
        try:
            from order import inquire_ccnl
            from datetime import datetime
            
            today = datetime.now().strftime("%Y%m%d")
            
            df = inquire_ccnl(
                config=self.config,
                token_manager=self.token_manager,
                pdno="",
                ord_strt_dt=today,
                ord_end_dt=today,
                sll_buy_dvsn="02",
                ccld_nccs_dvsn="01",
                ovrs_excg_cd="%",
                sort_sqn="DS",
                ord_dt="",
                ord_gno_brno="",
                odno=""
            )
            
            if df is None or df.empty:
                return
            
            for _, row in df.iterrows():
                order_no = row.get('odno', '')
                
                if not order_no or order_no in self.processed_orders or order_no in self.monitoring_orders:
                    continue
                
                ticker = row.get('pdno', '').strip()
                filled_qty_str = row.get('ft_ccld_qty', '0')
                filled_price_str = row.get('ft_ccld_unpr3', '0')
                
                try:
                    filled_qty = int(filled_qty_str) if filled_qty_str else 0
                    filled_price = float(filled_price_str) if filled_price_str else 0.0
                except (ValueError, TypeError):
                    continue
                
                if ticker and filled_qty > 0 and filled_price > 0:
                    logger.info(f"🆕 New buy order detected: {order_no} ({ticker} {filled_qty}주 @ ${filled_price})")
                    
                    order_info = {
                        'ticker': ticker,
                        'quantity': filled_qty,
                        'buy_price': filled_price,
                        'created_at': datetime.now(),
                        'last_checked': None,
                        'check_count': 0,
                        'status': 'monitoring',
                        'source': 'auto'
                    }
                    
                    with self.lock:
                        self.monitoring_orders[order_no] = order_info
                    
                    self.save_state()
        
        except Exception as e:
            logger.error(f"❌ Failed to scan for new buy orders: {e}")

    def enter_sleep_mode(self):
        """
        기획서 2.2: 슬립 모드 진입 (ET 12:00)
        
        [v3.0 수정] 주말(금요일 장마감 후 ~ 일요일)은 시스템 완전 종료
        """
        # --- 주말 체크 (토/일) ---
        try:
            from pytz import timezone as pytz_tz
            tz = pytz_tz('US/Eastern')
            now_et = datetime.now(tz)
            weekday = now_et.weekday()  # 0=월, 4=금, 5=토, 6=일

            if weekday in [5, 6]:  # 토요일, 일요일
                weekday_names = ['월', '화', '수', '목', '금', '토', '일']
                logger.info(f"🌴 주말({weekday_names[weekday]}요일) 슬립 모드 진입 → 시스템 완전 종료")

                # 1. 텔레그램 알림
                if self.telegram_bot:
                    self.telegram_bot.send_message(
                        "🌴 <b>주말 슬립 모드</b>\n\n"
                        f"오늘은 {weekday_names[weekday]}요일입니다.\n"
                        "시스템을 완전히 종료합니다.\n\n"
                        "월요일 ET 05:00에 다시 시작됩니다.\n"
                        "좋은 주말 보내세요! 😊",
                        parse_mode='HTML',
                        force=True
                    )

                # 2. 상태 저장
                self.save_state()

                # 3. 당일 매매 내역 전송
                self.send_daily_trades_csv()

                # 4. 시스템 완전 종료
                logger.info("🛑 주말 슬립 모드: 시스템 완전 종료 (sys.exit(0))")
                sys.exit(0)  # ✅ 완전 종료

        except SystemExit:
            raise # sys.exit()은 다시 던져야 함
        except Exception as e:
            logger.error(f"주말 체크 로직 오류: {e}")
            # 오류 발생 시 평일 로직으로 진행

        # --- 평일(월~목) 슬립 모드 로직 (기존 유지) ---
        logger.info("😴 슬립 모드 진입 (평일 대기 - 내일 다시 가동)")

        # 1. 텔레그램 주문 취소
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
        
        # 2. 평일 슬립 알림
        if self.telegram_bot:
            self.telegram_bot.send_sleep_mode_notification(reason="normal")
        
        # 3. 당일 매매 내역 전송
        self.send_daily_trades_csv()

    def send_daily_trades_csv(self):
        """
        당일 매매 내역을 CSV 형식으로 텔레그램 전송
        """
        # ✅ [수정] 토큰 확인 (Race Condition 방지)
        try:
            access_token = self.token_manager.get_access_token()
            if not access_token:
                logger.warning("⚠️ Access Token 없음, 당일 내역 조회 건너뜀")
                return
        except Exception as e:
            logger.warning(f"⚠️ 토큰 확인 실패: {e}, 당일 내역 조회 건너뜀")
            return
        
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

            if isinstance(success, dict):
                sell_order_no = success.get('order_no')
                
                if sell_order_no:
                    self.sell_monitor.add_order(sell_order_no, {
                        'ticker': order_info['ticker'],
                        'quantity': order_info['quantity'],
                        'buy_price': filled_price,
                        'sell_price': sell_price,
                        'source': order_info.get('source', 'auto'),
                        'created_at': datetime.now()
                    })
                    
                    logger.info(f"✅ 매도 주문 접수 완료: {sell_order_no}, SellOrderMonitor 등록됨")
                
                with self._counter_lock:
                    self.stats['successful_detections'] += 1
                
                if order_no:
                    self.processed_orders.add(order_no)
                
                return True
            elif success is True:
                with self._counter_lock:
                    self.stats['successful_detections'] += 1
                
                if order_no:
                    self.processed_orders.add(order_no)
                
                return True
            else:
                if order_no:
                    self.failed_orders[order_no] = (datetime.now(), "Sell order failed")
                return False
        
        except Exception as e:
            logger.error(f"❌ Auto-sell execution error: {e}")
            if order_no:
                self.failed_orders[order_no] = (datetime.now(), str(e))
            return False

    def smart_monitor_loop(self):
        logger.info("🚀 Smart Order Monitor main loop started")
        
        while self.is_running:
            try:
                self.current_mode = self.get_current_trading_mode()
                
                if self.current_mode == 'closed':
                    logger.info("⏸️ 슬립 모드 (ET 12:00-04:00)")
                    self.enter_sleep_mode()
                    time.sleep(3600)
                    continue
                
                # ▼ Pre-market / Regular REST polling logic (정상 운영 로직) ▼
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