# smart_order_monitor.py - Korea Investment Securities Smart Order Monitor
# Specification v1.0 Compliant
# Pre-market REST Polling + Regular Hours WebSocket Monitoring System

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


class SmartOrderMonitor:
    """
    Smart Order Monitor System (Specification v1.0)
    
    Operating Hours (ET):
    - Pre-market: 04:00-09:30 (REST Polling with Smart Intervals)
    - Regular Hours: 09:30-12:00 (WebSocket Real-time)
    - Sleep Mode: 12:00-04:00 (System Off)
    
    Key Features:
    - Spec 3.1: Smart polling (3s/10s intervals)
    - Spec 5.1: Rate limit protection (15 req/sec)
    - Spec 2.3: WebSocket failure → System stop
    - Spec 4.4: Sell failure → Immediate abandon
    """

    def __init__(self, config, token_manager, telegram_bot=None):
        self.config = config
        self.token_manager = token_manager
        self.telegram_bot = telegram_bot
        
        # Monitoring state
        self.monitoring_orders = {}  # {order_no: order_info}
        self.is_running = False
        self.monitor_thread = None
        
        # Mode tracking
        self.current_mode = None
        self.last_mode_change = datetime.now()
        
        # Spec 3.1: Polling configurations
        self.premarket_config = config['polling'].get('premarket', {})
        self.ws_config = config['polling'].get('regular', {})
        
        # Spec 5.1: Rate limit protection
        self.rate_config = config['rate_limit']
        self.daily_api_count = 0
        self.last_reset_date = datetime.now().date()
        self.hourly_api_count = 0
        self.last_hour_reset = datetime.now().hour
        self.last_request_time = 0
        self.consecutive_requests = 0
        
        # State persistence (Spec 7.1)
        self.state_file = config['system'].get('state_file', '/tmp/auto-sell-order-state.json')
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
        
        # Spec 4.4: Order tracking (prevent duplicates)
        self.processed_orders = set()      # Successfully sold
        self.failed_orders = {}           # Failed orders: {order_no: (timestamp, reason)}
        self.processed_ws_orders = set()  # Processed via WebSocket
        
        # WebSocket client initialization
        self.ws_client = None
        self.ws_failure_count = 0
        self.ws_max_failures = 3  # Spec 2.3: 3 attempts before system stop
        
        # Last buy order scan time
        self.last_buy_scan = 0

        # ✅ 스레드 안전을 위한 Lock 추가
        self._counter_lock = threading.Lock()

    def load_persisted_state(self):
        """Spec 7.1: Load persisted state from file"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                
                # Only restore orders from last 1 hour
                cutoff_time = datetime.now() - timedelta(hours=1)
                
                for order_no, order_data in state.get('orders', {}).items():
                    created_at = datetime.fromisoformat(order_data['created_at'])
                    if created_at > cutoff_time:
                        order_data['created_at'] = created_at
                        if 'last_checked' in order_data and order_data['last_checked']:
                            order_data['last_checked'] = datetime.fromisoformat(order_data['last_checked'])
                        self.monitoring_orders[order_no] = order_data
                
                logger.info(f"💾 State restored: {len(self.monitoring_orders)} orders")
        except Exception as e:
            logger.warning(f"State restoration failed: {e}")

    def save_state(self):
        """Spec 7.1: Save current state to file"""
        try:
            state = {
                'timestamp': datetime.now().isoformat(),
                'last_check': datetime.now().isoformat(),
                'orders': {}
            }
            
            for order_no, order_data in self.monitoring_orders.items():
                order_copy = order_data.copy()
                order_copy['created_at'] = order_data['created_at'].isoformat()
                if order_data.get('last_checked'):
                    order_copy['last_checked'] = order_data['last_checked'].isoformat()
                state['orders'][order_no] = order_copy
            
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            
            with open(self.state_file, 'w', encoding='utf-8') as f:
                fcntl.flock(f, fcntl.LOCK_EX)  # 배타적 잠금
                json.dump(state, f, ensure_ascii=False, indent=2)
                fcntl.flock(f, fcntl.LOCK_UN)  # 잠금 해제

        except Exception as e:
            logger.warning(f"State save failed: {e}")

    def should_system_run(self):
        """
        Spec 2.2: Check if system should be running
        Operating hours: ET 04:00-12:00
        
        Returns:
            bool: True if within operating hours
        """
        try:
            trading_tz = self.config.get('order_settings', {}).get('timezone', 'US/Eastern')
            tz = timezone(trading_tz)
            now_time = datetime.now(tz).time()
            
            start_time = dtime(4, 0)   # 04:00 ET
            end_time = dtime(12, 0)    # 12:00 ET
            
            return start_time <= now_time < end_time
        except Exception as e:
            logger.error(f"System time check error: {e}")
            return False

    def get_current_trading_mode(self):
        """
        Spec 2.2, 2.3: Determine current trading mode
        
        Returns:
            str: 'premarket', 'regular', 'closed'
        """
        try:
            trading_tz = self.config.get('order_settings', {}).get('timezone', 'US/Eastern')
            tz = timezone(trading_tz)
            now_time = datetime.now(tz).time()
            
            logger.debug(f"🕐 Current time: {now_time.strftime('%H:%M')} ({trading_tz})")
            
            # Spec 2.3: Regular hours (WebSocket mode)
            if 'regular' in self.config['polling']:
                for time_range in self.config['polling']['regular'].get('time_ranges', []):
                    start_time = dtime(*[int(x) for x in time_range['start'].split(':')])
                    end_time = dtime(*[int(x) for x in time_range['end'].split(':')])
                    if start_time <= now_time < end_time:
                        logger.debug(f"✅ Regular hours: {time_range['start']} ~ {time_range['end']}")
                        return 'regular'
            
            # Spec 2.3: Pre-market (REST polling)
            if 'premarket' in self.config['polling']:
                for time_range in self.config['polling']['premarket'].get('time_ranges', []):
                    start_time = dtime(*[int(x) for x in time_range['start'].split(':')])
                    end_time = dtime(*[int(x) for x in time_range['end'].split(':')])
                    if start_time <= now_time < end_time:
                        logger.debug(f"✅ Pre-market: {time_range['start']} ~ {time_range['end']}")
                        return 'premarket'
            
            # Spec 2.2: Sleep mode
            logger.debug(f"⏸️ Sleep mode (closed)")
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
            
            # Spec 6.1: Telegram notification
            if self.telegram_bot:
                mode_names = {
                    'premarket': '🔵 Pre-market (REST Polling)',
                    'regular': '⚡ Regular Hours (WebSocket)',
                    'closed': '😴 Sleep Mode'
                }
                message = (
                    f"🔄 Mode Switch\n"
                    f"{mode_names.get(old_mode, old_mode)} → {mode_names.get(new_mode, new_mode)}"
                )
                self.telegram_bot.send_message(message)
            
            self.save_state()
            
            # Spec 2.3: Handle mode-specific actions
            if new_mode == 'closed':
                self.handle_sleep_mode()
            elif new_mode == 'regular':
                self.start_websocket_mode()
            elif new_mode == 'premarket':
                self.stop_websocket_mode()
            
            return True
        
        return False

    def handle_sleep_mode(self):
        """Spec 2.2: Handle sleep mode (ET 12:00-04:00)"""
        logger.info("😴 Entering sleep mode - System off until 04:00 ET")
        
        # Spec 6.1: Daily statistics telegram notification
        if self.telegram_bot:
            next_start = "04:00 ET (17:00 KST)"
            message = (
                f"😴 Sleep Mode Started\n"
                f"⏰ Next start: {next_start}\n"
                f"📊 Today's Statistics:\n"
                f"- Total requests: {self.stats['total_requests']}\n"
                f"- Successful detections: {self.stats['successful_detections']}\n"
                f"- WebSocket detections: {self.stats['ws_detections']}\n"
                f"- Rate limit hits: {self.stats['rate_limit_violations']}"
            )
            self.telegram_bot.send_message(message)

    def start_websocket_mode(self):
        """
        Spec 2.3: Start WebSocket for regular hours (ET 09:30-12:00)
        Spec 5.2: If WebSocket fails after 3 attempts → System stop
        """
        logger.info("⚡ Starting WebSocket mode...")
        
        try:
            # Initialize WebSocket client if not exists
            if self.ws_client is None:
                self.ws_client = WebSocketClient(
                    self.config,
                    self.token_manager,
                    self.handle_ws_message
                )
            
            # Start WebSocket
            self.ws_client.start()
            self.ws_failure_count = 0  # Reset failure count
            
            logger.info("✅ WebSocket started successfully")
            
        except Exception as e:
            self.ws_failure_count += 1
            logger.error(f"❌ WebSocket start failed (Attempt {self.ws_failure_count}/{self.ws_max_failures}): {e}")
            
            # Spec 2.3, 5.2: After 3 failures → System stop
            if self.ws_failure_count >= self.ws_max_failures:
                logger.critical("🚨 WebSocket failed 3 times → SYSTEM STOP (Spec 2.3)")
                
                if self.telegram_bot:
                    self.telegram_bot.send_error_notification(
                        f"🚨 System Stopped: WebSocket connection failed {self.ws_max_failures} times\n"
                        f"Spec 2.3: WebSocket failure → System stop"
                    )
                
                # STOP THE ENTIRE SYSTEM
                self.stop()
                raise RuntimeError("WebSocket connection failed - System stopped per Spec 2.3")

    def stop_websocket_mode(self):
        """Stop WebSocket when switching to pre-market mode"""
        if self.ws_client:
            logger.info("⏸️ Stopping WebSocket mode...")
            self.ws_client.stop()

    def calculate_polling_interval(self, order_info):
        """
        Spec 3.2: Calculate smart polling interval
        
        Pre-market intervals:
        - High activity (04:00-05:00, 08:00-09:30): 3 seconds
        - Low activity (05:00-08:00): 10 seconds
        """
        if self.current_mode == 'regular':
            return 3600  # WebSocket mode - no polling needed
        
        if self.current_mode == 'closed':
            return 3600  # Sleep mode
        
        if self.current_mode == 'premarket':
            # Spec 3.2: Determine high/low activity period
            try:
                trading_tz = self.config.get('order_settings', {}).get('timezone', 'US/Eastern')
                tz = timezone(trading_tz)
                now_time = datetime.now(tz).time()
                
                # High activity periods (Spec 3.2)
                high_activity_periods = [
                    (dtime(4, 0), dtime(5, 0)),    # 04:00-05:00
                    (dtime(8, 0), dtime(9, 30))    # 08:00-09:30
                ]
                
                for start, end in high_activity_periods:
                    if start <= now_time < end:
                        return self.premarket_config.get('interval_seconds', {}).get('high_activity', 3)
                
                # Low activity period (05:00-08:00)
                return self.premarket_config.get('interval_seconds', {}).get('low_activity', 10)
                
            except Exception as e:
                logger.error(f"Polling interval calculation error: {e}")
                return 5  # Safe default
        
        return 5  # Safe default

    def can_make_request(self):
        """
        Spec 5.1: Check if API request can be made
        
        Rate limits:
        - 15 requests/second (75% of official 20/sec limit)
        - 500 requests/hour
        - 5000 requests/day
        """
        self.reset_counters_if_needed()
        
        # Spec 2.3: No REST API requests in regular or closed mode
        if self.current_mode in ['regular', 'closed']:
            return False
        
        # Spec 5.1: Minimum interval check (0.07 seconds = 1/15)
        now_time = time.time()
        min_interval = self.rate_config.get('min_request_interval', 0.07)
        
        if now_time - self.last_request_time < min_interval:
            return False
        
        # Consecutive request limit
        consecutive_limit = self.rate_config.get('consecutive_limit', 10)
        if self.consecutive_requests >= consecutive_limit:
            logger.warning(f"⚠️ Consecutive limit reached: {self.consecutive_requests}/{consecutive_limit}")
            time.sleep(1)
            self.consecutive_requests = 0
        
        # Spec 5.1: Daily limit
        if self.daily_api_count >= self.rate_config['daily_limit']:
            logger.warning(f"⚠️ Daily API limit reached: {self.daily_api_count}/{self.rate_config['daily_limit']}")
            return False
        
        # Spec 5.1: Hourly limit
        if self.hourly_api_count >= self.rate_config['hourly_limit']:
            logger.warning(f"⚠️ Hourly API limit reached: {self.hourly_api_count}/{self.rate_config['hourly_limit']}")
            return False
        # Rate Limit 90% 도달 시 텔레그램 알림
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
        """Reset API counters daily/hourly"""
        now = datetime.now()
        
        # Daily reset
        if now.date() != self.last_reset_date:
            logger.info(
                f"📊 Daily reset - API: {self.daily_api_count}, "
                f"Success: {self.stats['successful_detections']}, "
                f"WS: {self.stats['ws_detections']}"
            )
            
            self.daily_api_count = 0
            self.last_reset_date = now.date()
            self.stats['successful_detections'] = 0
            self.stats['ws_detections'] = 0
            self.stats['premarket_calls'] = 0
            self.stats['rate_limit_violations'] = 0
            self.stats['api_errors'] = {}
            
            # Spec 4.4: Clear processed orders tracking
            self.processed_orders.clear()
            self.processed_ws_orders.clear()
            self.failed_orders.clear()
            logger.info("🔄 Processed orders list cleared")
        
        # Hourly reset
        if now.hour != self.last_hour_reset:
            logger.debug(f"📊 Hourly reset: {self.hourly_api_count} calls")
            self.hourly_api_count = 0
            self.last_hour_reset = now.hour
            self.consecutive_requests = 0

    def handle_api_error(self, error_code, error_msg):
        """Spec 5.1, 8.1: Handle API errors"""
        self.stats['api_errors'][error_code] = self.stats['api_errors'].get(error_code, 0) + 1
        
        # Rate limit errors
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
        
        # Temporary errors
        elif error_code in ['EGW90001']:
            logger.warning(f"⚠️ Temporary error: {error_code} - {error_msg}")
            time.sleep(5)
            return False
        
        # Other errors
        else:
            logger.error(f"❌ API error: {error_code} - {error_msg}")
        
        return False

    def add_order_to_monitor(self, order_no, ticker, quantity, buy_price, order_time=None):
        """Add order to monitoring list"""
        if not order_time:
            order_time = datetime.now()
        
        order_info = {
            'ticker': ticker,
            'quantity': quantity,
            'buy_price': buy_price,
            'created_at': order_time,
            'last_checked': None,
            'check_count': 0,
            'mode_when_created': self.get_current_trading_mode()
        }
        
        self.monitoring_orders[order_no] = order_info
        current_mode = self.get_current_trading_mode()
        
        logger.info(f"📝 Order registered: {order_no} ({ticker} {quantity} @ ${buy_price}) - Mode: {current_mode}")
        self.save_state()
        
        # Spec 6.1: Telegram notification
        if self.telegram_bot:
            mode_emoji = {'premarket': '🔵', 'regular': '⚡', 'closed': '😴'}
            message = (
                f"{mode_emoji.get(current_mode, '📝')} Order Registered\n"
                f"📄 {order_no}\n"
                f"🏷️ {ticker} {quantity} shares\n"
                f"💰 ${buy_price}"
            )
            self.telegram_bot.send_message(message)

    def check_order_status(self, order_no):
        """
        Spec 3장: Check order status via REST API
        Korea Investment Securities Official API
        """
        if not self.can_make_request():
            return None
        
        try:
            # Official API endpoint
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
            
            today = datetime.now().strftime("%Y%m%d")
            
            # Official API parameters (GitHub verified)
            params = {
                "CANO": self.config['cano'],
                "ACNT_PRDT_CD": self.config['acnt_prdt_cd'],
                "PDNO": "",
                "ORD_STRT_DT": today,
                "ORD_END_DT": today,
                "SLL_BUY_DVSN": "02",          # Buy only
                "CCLD_NCCS_DVSN": "01",        # Filled only
                "OVRS_EXCG_CD": "NASD",
                "SORT_SQN": "DS",
                "ORD_DT": "",
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "CTX_AREA_NK200": "",
                "CTX_AREA_FK200": ""
            }
            
            request_start = time.time()
            response = requests.get(url, headers=headers, params=params, timeout=15)
            
            # Update counters
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
            
            # Check for API errors
            error_code = data.get("msg_cd", "")
            if error_code and error_code != "MCA00000":
                error_msg = data.get('msg1', 'Unknown error')
                if self.handle_api_error(error_code, error_msg):
                    return None
            
            if data.get("rt_cd") != "0":
                logger.error(f"❌ API error: {data.get('msg1', 'Unknown')}")
                return None
            
            # Log slow responses
            response_time = time.time() - request_start
            if response_time > 5:
                logger.warning(f"⏱️ Slow API response: {response_time:.2f}s")
            
            # Find matching order
            for item in data.get("output", []):
                if item.get("odno") == order_no:
                    # Official field names (GitHub verified)
                    ccld_qty = item.get("ft_ccld_qty", "0")
                    ccld_unpr = item.get("ft_ccld_unpr3", "0")
                    
                    return {
                        'status': '02',  # Filled
                        'filled_qty': int(ccld_qty) if ccld_qty else 0,
                        'filled_price': float(ccld_unpr) if ccld_unpr else 0.0,
                        'order_data': item
                    }
            
            return {'status': 'not_found', 'filled_qty': 0, 'filled_price': 0.0}
        
        except requests.exceptions.Timeout:
            logger.warning(f"⏱️ API timeout: {order_no}")
            return None
        except Exception as e:
            logger.error(f"❌ Status check error: {e}")

            # ✅ 연속 오류 카운터 증가
            with self._counter_lock:
                self.stats['consecutive_api_errors'] += 1
    
            # ✅ 10회 도달 시 비상 정지
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
        """
        Spec 4장: Execute automatic sell order
        
        Args:
            order_info: Order information dict
            filled_price: Fill price
            order_no: Order number (for tracking)
        
        Returns:
            bool: Success status
        """
        try:
            from order import place_sell_order
            
            current_mode = self.get_current_trading_mode()
            
            # Spec 4.1: Get profit margin from config
            # order_settings.target_profit_rate (percentage)
            target_profit_rate = self.config.get('order_settings', {}).get('target_profit_rate', 3.0)
            profit_margin = target_profit_rate / 100
            
            sell_price = round(filled_price * (1 + profit_margin), 2)
            
            execution_data = {
                'ticker': order_info['ticker'],
                'quantity': order_info['quantity'],
                'price': filled_price
            }
            
            logger.info(
                f"🎯 Fill detected! {execution_data['ticker']} ${filled_price} "
                f"→ Sell @ ${sell_price} (Mode: {current_mode})"
            )
            
            # Execute sell order
            success = place_sell_order(
                self.config,
                self.token_manager,
                execution_data,
                self.telegram_bot
            )
            
            if success:
                # Update statistics
                if current_mode == 'regular':
                    self.stats['ws_detections'] += 1
                    total_detected = self.stats['ws_detections']
                else:
                    self.stats['successful_detections'] += 1
                    total_detected = self.stats['successful_detections']
                
                # Spec 4.4: Record successful order
                if order_no:
                    self.processed_orders.add(order_no)
                    logger.info(f"✅ Order {order_no} marked as processed")
                
                logger.info(f"✅ Auto-sell success: {execution_data['ticker']} (Total: {total_detected})")
                
                # Spec 6.1: Telegram notification
                if self.telegram_bot:
                    mode_emoji = {'premarket': '🔵', 'regular': '⚡'}
                    message = (
                        f"{mode_emoji.get(current_mode, '🎉')} Sell Success!\n"
                        f"🏷️ {execution_data['ticker']}\n"
                        f"💰 ${filled_price} → ${sell_price}\n"
                        f"📈 +{target_profit_rate}%\n"
                        f"📊 Total {current_mode} detections: {total_detected}"
                    )
                    self.telegram_bot.send_message(message)
            else:
                # Spec 4.4: Record failed order
                if order_no:
                    self.failed_orders[order_no] = (datetime.now(), 'Sell failed')
                    logger.warning(f"⚠️ Order {order_no} marked as failed")
            
            return success
        
        except Exception as e:
            logger.error(f"❌ Auto-sell execution error: {e}")
            return False

    def handle_ws_message(self, message):
        """
        Spec 2.3: Handle WebSocket real-time messages (H0STCNI0)
        
        Args:
            message: WebSocket message (JSON string)
        """
        try:
            data = json.loads(message)
            
            # Real-time fill data (H0STCNI0)
            if data.get('header', {}).get('tr_id') == 'H0STCNI0':
                body = data.get('body', {})
                if not body:
                    return
                
                # Handle multiple fills
                outputs = body.get('output', [])
                if not isinstance(outputs, list):
                    outputs = [outputs]
                
                for item in outputs:
                    # Only process buy orders (02)
                    if item.get('sll_buy_dvsn_cd') != '02':
                        continue
                    
                    order_no = item.get("odno", "")
                    if not order_no:
                        continue
                    
                    # Spec 4.4: Prevent duplicate processing
                    if (order_no in self.processed_ws_orders or 
                        order_no in self.processed_orders):
                        logger.debug(f"Already processed WS fill: {order_no}")
                        continue
                    
                    ticker = item.get("pdno", "")
                    try:
                        ccld_qty = int(item.get("ccld_qty", "0"))
                        ccld_price = float(item.get("ccld_unpr", "0"))
                    except ValueError:
                        logger.warning(f"WS data parsing error: {item}")
                        continue
                    
                    if ccld_qty > 0 and ccld_price > 0:
                        logger.info(f"🎉 [WS] New buy fill! {order_no}: {ticker} {ccld_qty} @ ${ccld_price}")
                        
                        order_info = {
                            'ticker': ticker,
                            'quantity': ccld_qty,
                            'buy_price': ccld_price,
                            'created_at': datetime.now()
                        }
                        
                        # Execute auto-sell with order number
                        success = self.execute_auto_sell(order_info, ccld_price, order_no)
                        
                        if success:
                            logger.info(f"✅ [WS] Auto-sell immediate success: {ticker}")
                            self.processed_ws_orders.add(order_no)
                        else:
                            logger.error(f"❌ [WS] Auto-sell failed: {ticker}. Switching to REST polling.")
                            # On failure, add to REST polling monitoring
                            self.add_order_to_monitor(order_no, ticker, ccld_qty, ccld_price)
        
        except json.JSONDecodeError:
            logger.debug(f"WS message parsing failed (not JSON): {message[:50]}...")
        except Exception as e:
            logger.error(f"❌ WS message handling error: {e} - Message: {message}")

    def scan_for_new_buy_orders(self):
        """
        Spec 3장: Scan for new buy orders (auto-detection)
        Prevents duplicate processing
        """
        try:
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
            
            today = datetime.now().strftime("%Y%m%d")
            
            params = {
                "CANO": self.config['cano'],
                "ACNT_PRDT_CD": self.config['acnt_prdt_cd'],
                "PDNO": "",
                "ORD_STRT_DT": today,
                "ORD_END_DT": today,
                "SLL_BUY_DVSN": "02",
                "CCLD_NCCS_DVSN": "01",
                "OVRS_EXCG_CD": "NASD",
                "SORT_SQN": "DS",
                "ORD_DT": "",
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "CTX_AREA_NK200": "",
                "CTX_AREA_FK200": ""
            }
            
            response = requests.get(url, headers=headers, params=params, timeout=15)
            
            # Update counters
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
                
                # Spec 4.4: Enhanced duplicate prevention
                if order_no in self.monitoring_orders:
                    continue  # Already monitoring
                if order_no in self.processed_ws_orders:
                    continue  # Processed via WebSocket
                if order_no in self.processed_orders:
                    logger.debug(f"⏭️ Already processed order: {order_no}")
                    continue
                if order_no in self.failed_orders:
                    # Don't retry failed orders for 1 hour
                    fail_time, reason = self.failed_orders[order_no]
                    if (datetime.now() - fail_time).total_seconds() < 3600:
                        logger.debug(f"⏭️ Failed order (1hr wait): {order_no}")
                        continue
                    else:
                        # Retry after 1 hour
                        del self.failed_orders[order_no]
                
                ticker = order.get("pdno", "")
                ccld_qty = order.get("ft_ccld_qty", "0")
                ccld_price = order.get("ft_ccld_unpr3", "0")
                
                try:
                    ccld_qty = int(ccld_qty) if ccld_qty else 0
                    ccld_price = float(ccld_price) if ccld_price else 0.0
                except:
                    continue
                
                if ccld_qty > 0 and ccld_price > 0:
                    logger.info(f"🎉 [POLL] New buy fill detected! {order_no}: {ticker} {ccld_qty} @ ${ccld_price}")
                    
                    order_info = {
                        'ticker': ticker,
                        'quantity': ccld_qty,
                        'buy_price': ccld_price,
                        'created_at': datetime.now(),
                        'last_checked': None,
                        'check_count': 0,
                        'mode_when_created': self.get_current_trading_mode()
                    }
                    
                    # Execute auto-sell with order number
                    success = self.execute_auto_sell(order_info, ccld_price, order_no)
                    
                    # ✅ --- 수정 위치 2 ---
                    if success:
                        logger.info(f"✅ [POLL] Auto-sell immediate success: {ticker}")
                        
                        # Spec 6.1: Telegram notification
                        if self.telegram_bot:
                            target_profit_rate = self.config.get('order_settings', {}).get('target_profit_rate', 3.0)
                            message = (
                                f"🎉 [POLL] Auto-detection & Sell Success!\n"
                                f"🏷️ {ticker} {ccld_qty} shares\n"
                                f"💰 Buy: ${ccld_price}\n"
                                f"📈 Target profit: +{target_profit_rate}%"
                            )
                            self.telegram_bot.send_message(message)
                    else:
                        # ✅ "이미 매도됨" 오류는 monitoring에 추가하지 않음
                        logger.error(f"❌ [POLL] Auto-sell failed: {ticker}. Not adding to monitoring (already sold).")
                        
                        # 실패 기록만 (monitoring에 추가하지 않음)
                        self.failed_orders[order_no] = (datetime.now(), 'Already sold')
                        self.processed_orders.add(order_no)  # 재처리 방지
                        
                        # 텔레그램 알림
                        if self.telegram_bot:
                            if hasattr(self.telegram_bot, 'send_info_notification'):
                                self.telegram_bot.send_info_notification(
                                    f"매도 대상 없음: {ticker} (이미 매도됨)"
                                )
                            else:
                                self.telegram_bot.send_message(
                                    f"ℹ️ 시스템 정보\n매도 대상 없음: {ticker} (이미 매도됨)"
                                )
                        
                        logger.info(f"🗑️ Order {order_no} not added to monitoring (already sold)")
                    # ✅ --- 수정 완료 2 ---
        
        except Exception as e:
            logger.error(f"❌ Buy detection scan error: {e}")

    def cleanup_expired_orders(self):
        """Remove expired orders from monitoring"""
        now = datetime.now()
        expired_orders = []
        
        for order_no, order_info in self.monitoring_orders.items():
            age_hours = (now - order_info['created_at']).total_seconds() / 3600
            
            # Expiration time based on mode
            current_mode = self.get_current_trading_mode()
            max_hours = 0.5 if current_mode == 'premarket' else 2
            
            if age_hours > max_hours:
                expired_orders.append(order_no)
        
        for order_no in expired_orders:
            order_info = self.monitoring_orders.pop(order_no, None)
            if order_info:
                age_hours = (now - order_info['created_at']).total_seconds() / 3600
                logger.info(f"⏰ Order expired: {order_no} ({age_hours:.1f} hours)")
        
        if expired_orders:
            self.save_state()

    def smart_monitor_loop(self):
        """
        Spec 3장: Main monitoring loop
        - Pre-market: REST polling with smart intervals
        - Regular hours: WebSocket real-time (no polling)
        - Sleep mode: System off
        """
        logger.info("🚀 Smart Order Monitor started")
        
        while self.is_running:
            try:
                # Spec 2.2: Check if system should be running
                if not self.should_system_run():
                    logger.info("🌙 Outside operating hours (ET 12:00) - Stopping system")
                    self.stop()
                    break
                
                # Spec 2.3: Switch mode if needed
                if self.switch_mode_if_needed():
                    if self.current_mode == 'closed':
                        time.sleep(300)  # 5 min wait in sleep mode
                        continue
                
                # Spec 2.3: In regular hours (WebSocket), skip REST polling
                if self.current_mode == 'regular':
                    logger.debug("⚡ WebSocket mode active... No REST polling")
                    time.sleep(5)  # Prevent CPU spinning
                    continue
                
                # ▼ Pre-market REST polling logic ▼
                
                # Scan for new buy orders every 15 seconds
                current_time = time.time()
                if current_time - self.last_buy_scan > 15:
                    logger.debug("🔍 Scanning for new buy orders...")
                    self.scan_for_new_buy_orders()
                    self.last_buy_scan = current_time
                
                # If no orders to monitor, wait
                if not self.monitoring_orders:
                    time.sleep(30)
                    continue
                
                # Clean up expired orders
                self.cleanup_expired_orders()
                
                # Process each monitoring order
                processed_count = 0
                
                for order_no, order_info in list(self.monitoring_orders.items()):
                    if not self.is_running:
                        break
                    
                    # Calculate polling interval
                    polling_interval = self.calculate_polling_interval(order_info)
                    now = datetime.now()
                    
                    # Skip if checked recently
                    if (order_info['last_checked'] and 
                        (now - order_info['last_checked']).total_seconds() < polling_interval):
                        continue
                    
                    # Check order status
                    status_info = self.check_order_status(order_no)
                    order_info['last_checked'] = now
                    order_info['check_count'] += 1
                    processed_count += 1
                    
                    if status_info is None:
                        continue
                    
                    # Check if filled
                    if (status_info['status'] in ['02', 'Filled', 'Complete'] and 
                        status_info['filled_qty'] > 0):
                        
                        logger.info(
                            f"🎉 [POLL] Fill complete: {order_no} "
                            f"(Mode: {self.current_mode}, Checks: {order_info['check_count']})"
                        )
                        
                        # Execute auto-sell with order number
                        success = self.execute_auto_sell(order_info, status_info['filled_price'], order_no)
                        
                        # ✅ --- 수정 위치 1 ---
                        if success:
                            self.monitoring_orders.pop(order_no, None)
                            self.save_state()
                        else:
                            # ✅ "이미 매도됨" 오류는 재시도하지 않고 제거
                            logger.warning(f"⚠️ [POLL] Fill detected but sell failed: {order_no}. Removing from monitoring.")
                            
                            # monitoring_orders에서 완전히 제거
                            self.monitoring_orders.pop(order_no, None)
                            
                            # 재처리 방지
                            self.processed_orders.add(order_no)
                            
                            # 상태 저장
                            self.save_state()
                            
                            # 텔레그램 알림
                            if self.telegram_bot:
                                if hasattr(self.telegram_bot, 'send_info_notification'):
                                    self.telegram_bot.send_info_notification(
                                        f"매도 대상 없음: {order_info['ticker']} (이미 매도됨)"
                                    )
                                else:
                                    self.telegram_bot.send_message(
                                        f"ℹ️ 시스템 정보\n매도 대상 없음: {order_info['ticker']} (이미 매도됨)"
                                    )
                            
                            logger.info(f"🗑️ Order {order_no} removed from monitoring (already sold)")
                        # ✅ --- 수정 완료 1 ---
                    
                    # Rate limit: Wait between checks
                    time.sleep(max(1, self.rate_config.get('min_request_interval', 0.07)))
                
                # Mode-specific wait times
                if self.current_mode == 'premarket':
                    time.sleep(2)
                else:
                    time.sleep(60)
                
                # Periodic state save
                if processed_count > 0 and processed_count % 20 == 0:
                    self.save_state()
                
                # Periodic statistics log
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

    def start(self):
        """Start monitoring system"""
        if self.is_running:
            logger.warning("⚠️ Monitoring already running")
            return
        
        self.current_mode = self.get_current_trading_mode()
        self.is_running = True
        
        # Start WebSocket if in regular hours
        if self.current_mode == 'regular':
            self.start_websocket_mode()
        
        # Start monitoring thread
        self.monitor_thread = threading.Thread(target=self.smart_monitor_loop, daemon=True)
        self.monitor_thread.start()
        
        logger.info(f"🚀 Smart Order Monitor started - Initial mode: {self.current_mode}")

    def stop(self):
        """Stop monitoring system"""
        if not self.is_running:
            return
        
        logger.info("🛑 Stopping monitor...")
        self.is_running = False
        
        # Stop WebSocket
        self.stop_websocket_mode()
        
        # Save state
        self.save_state()
        
        # Wait for thread
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=10)
        
        logger.info(
            f"🛑 Monitor stopped - Final stats: "
            f"Requests: {self.stats['total_requests']}, "
            f"Success: {self.stats['successful_detections']}, "
            f"WS: {self.stats['ws_detections']}"
        )

    def get_monitoring_count(self):
        """Get current monitoring order count"""
        return len(self.monitoring_orders)

    def get_detailed_stats(self):
        """Get detailed statistics"""
        current_mode = self.get_current_trading_mode()
        
        return {
            'monitoring_count': len(self.monitoring_orders),
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