# smart_order_monitor.py - 한국투자증권 실전 자동 감시 시스템 최신 공식 표준 완전 반영
# (WebSocket 실시간 감시 모드 통합 버전)

import requests
import json
import logging
import time
import threading
import os
from datetime import datetime, timedelta, time as dtime
from pytz import timezone

# ✅ [추가] WebSocket 클라이언트 import
# (프로젝트 내에 websocket_client.py 파일이 있다고 가정)
try:
    from websocket_client import WebSocketClient
except ImportError:
    logger.error("websocket_client.py를 찾을 수 없습니다. WebSocket 모드가 작동하지 않습니다.")
    # WebSocketClient가 없어도 시스템이 중단되지 않도록 임시 클래스 정의
    class WebSocketClient:
        def __init__(self, *args, **kwargs):
            logger.error("WebSocketClient가 import되지 않아 비활성화 상태로 실행됩니다.")
        def start(self): pass
        def stop(self): pass

logger = logging.getLogger(__name__)

class SmartOrderMonitor:
    """KIS API 실전 환경 최적화 집중/스마트 폴링 및 WebSocket 실시간 감시 시스템"""

    def __init__(self, config, token_manager, telegram_bot=None):
        self.config = config
        self.token_manager = token_manager
        self.telegram_bot = telegram_bot
        self.monitoring_orders = {}
        self.is_running = False
        self.monitor_thread = None

        # 폴링 모드별 설정
        self.current_mode = None
        self.last_mode_change = datetime.now()

        # 집중 폴링 설정
        self.aggressive_config = config['polling']['aggressive']
        self.aggressive_interval = self.aggressive_config['interval']

        # 스마트 폴링 설정
        self.smart_config = config['polling']['smart']
        self.smart_initial_interval = self.smart_config['initial_interval']
        self.smart_max_interval = self.smart_config['max_interval']
        self.backoff_multiplier = self.smart_config['backoff_multiplier']

        # Rate Limit 보호 강화
        self.rate_config = config['rate_limit']
        self.daily_api_count = 0
        self.last_reset_date = datetime.now().date()
        self.hourly_api_count = 0
        self.last_hour_reset = datetime.now().hour
        self.last_request_time = 0
        self.consecutive_requests = 0

        # 상태 영속화 파일
        self.state_file = config['system'].get('state_file', '/tmp/auto-sell-order-state.json')
        self.load_persisted_state()

        # 통계
        self.stats = {
            'total_requests': 0,
            'successful_detections': 0,
            'aggressive_mode_calls': 0,
            'smart_mode_calls': 0,
            'ws_detections': 0, # WS 감지 통계
            'mode_switches': 0,
            'rate_limit_violations': 0,
            'api_errors': {}
        }
        
        # ✅ [추가] WebSocket 클라이언트 초기화
        self.ws_client = WebSocketClient(config, token_manager, self.handle_ws_message)
        # ✅ [추가] WebSocket 중복 체결 방지용
        self.processed_ws_orders = set()


    def load_persisted_state(self):
        """저장된 상태 복원"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                cutoff_time = datetime.now() - timedelta(hours=1)
                for order_no, order_data in state.get('orders', {}).items():
                    created_at = datetime.fromisoformat(order_data['created_at'])
                    if created_at > cutoff_time:
                        order_data['created_at'] = created_at
                        self.monitoring_orders[order_no] = order_data
                logger.info(f"💾 상태 복원: {len(self.monitoring_orders)}개 주문")
        except Exception as e:
            logger.warning(f"상태 복원 실패: {e}")

    def save_state(self):
        """현재 상태 저장"""
        try:
            state = {'timestamp': datetime.now().isoformat(),'last_check': datetime.now().isoformat(), 'orders': {}}
            for order_no, order_data in self.monitoring_orders.items():
                order_copy = order_data.copy()
                order_copy['created_at'] = order_data['created_at'].isoformat()
                if order_data.get('last_checked'):
                    order_copy['last_checked'] = order_data['last_checked'].isoformat()
                state['orders'][order_no] = order_copy
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"상태 저장 실패: {e}")

    # smart_order_monitor.py - get_current_trading_mode() 함수 수정
# (전체 파일에서 이 함수만 교체하세요)

    def get_current_trading_mode(self):
        """
        현재 시간에 따른 매매 모드 판별 (config의 timezone 사용)
    
        Returns:
            str: 'aggressive', 'smart', 'ws_mode', 'off'
        """
        try:
            # ✅ config에서 timezone 가져오기 (더 이상 KST 하드코딩 안 함!)
            trading_tz = self.config['trading'].get('timezone', 'US/Eastern')
            tz = timezone(trading_tz)
            now_time = datetime.now(tz).time()
        
            logger.debug(f"🕐 현재시간: {now_time.strftime('%H:%M')} ({trading_tz})")

            # ✅ ws_mode (WebSocket 모드) 우선 감지
            if 'ws_mode' in self.config['polling']:
                for time_range in self.config['polling']['ws_mode'].get('time_ranges', []):
                    start_time = dtime(*[int(x) for x in time_range['start'].split(':')])
                    end_time = dtime(*[int(x) for x in time_range['end'].split(':')])
                    if start_time <= now_time < end_time:
                        logger.debug(f"✅ ws_mode: {time_range['start']} ~ {time_range['end']}")
                        return 'ws_mode'

            # aggressive 모드 (프리마켓)
            for time_range in self.aggressive_config['time_ranges']:
                start_time = dtime(*[int(x) for x in time_range['start'].split(':')])
                end_time = dtime(*[int(x) for x in time_range['end'].split(':')])
                if start_time <= now_time < end_time:
                    logger.debug(f"✅ aggressive: {time_range['start']} ~ {time_range['end']}")
                    return 'aggressive'
        
            # smart 모드 (애프터마켓)
            for time_range in self.smart_config['time_ranges']:
                start_time = dtime(*[int(x) for x in time_range['start'].split(':')])
                end_time = dtime(*[int(x) for x in time_range['end'].split(':')])
                if start_time <= now_time < end_time:
                    logger.debug(f"✅ smart: {time_range['start']} ~ {time_range['end']}")
                    return 'smart'
        
            # 장 마감 시간
            logger.debug(f"⏸️ 장 마감 시간 (off)")
            return 'off'
        
        except Exception as e:
            logger.error(f"모드 판별 오류: {e}")
            return 'smart'  # 기본값

    def switch_mode_if_needed(self):
        """필요 시 모드 전환 및 상태 알림"""
        new_mode = self.get_current_trading_mode()
        if new_mode != self.current_mode:
            old_mode = self.current_mode
            self.current_mode = new_mode
            self.last_mode_change = datetime.now()
            self.stats['mode_switches'] += 1
            logger.info(f"🔄 매매 모드 전환: {old_mode} → {new_mode}")
            if self.telegram_bot:
                # ✅ [수정] ws_mode 텔레그램 알림 추가
                mode_names = {
                    'aggressive': '🔥 집중 매매 (3초 간격)', 
                    'smart': '🧠 스마트 폴링 (5-20초)', 
                    'off': '⏸️ 중지 (취침)',
                    'ws_mode': '⚡️ 실시간 (WebSocket)'
                }
                message = f"🔄 모드 전환\n{mode_names.get(old_mode, old_mode)} → {mode_names.get(new_mode, new_mode)}"
                self.telegram_bot.send_message(message)
            self.save_state()
            if new_mode == 'off':
                self.stop_for_off_hours()
            return True
        return False

    def stop_for_off_hours(self):
        """중지 시간 처리"""
        logger.info("⏸️ 매매 중지 시간 - 모니터링 일시 중지")
        if self.telegram_bot:
            next_start = "17:00 KST"
            message = f"😴 취침 모드 시작\n⏰ 다음 시작: {next_start}\n📊 오늘 통계:\n- 총 요청: {self.stats['total_requests']}회\n- 성공 감지: {self.stats['successful_detections']}회\n- WS 감지: {self.stats['ws_detections']}회\n- Rate Limit: {self.stats['rate_limit_violations']}회"
            self.telegram_bot.send_message(message)

    def calculate_polling_interval(self, order_no, order_info):
        """모드별 폴링 간격 계산"""
        # ws_mode일 경우 이 함수가 호출되지 않아야 하지만, 안전장치로 추가
        if self.current_mode == 'ws_mode':
            return 3600 # 1시간 (사실상 폴링 안 함)
        if self.current_mode == 'off':
            return 3600
        elif self.current_mode == 'aggressive':
            return self.aggressive_interval
        elif self.current_mode == 'smart':
            return self.calculate_smart_interval(order_info)
        return self.smart_initial_interval

    def calculate_smart_interval(self, order_info):
        now = datetime.now()
        order_age_minutes = (now - order_info['created_at']).total_seconds() / 60
        base_interval = self.smart_initial_interval
        for age_config in self.smart_config['order_age_factor']:
            if 'minutes' in age_config and order_age_minutes >= age_config['minutes']:
                base_interval = age_config['interval']
            elif 'default' in age_config:
                base_interval = age_config['default']
        if order_info['no_change_count'] > self.smart_config['no_change_threshold']:
            excess_count = order_info['no_change_count'] - self.smart_config['no_change_threshold']
            backoff_factor = self.backoff_multiplier ** min(excess_count, 4)
            base_interval = min(int(base_interval * backoff_factor), self.smart_max_interval)
        if (self.smart_config.get('consecutive_success_speedup', False) and order_info.get('consecutive_successes', 0) > 3):
            base_interval = max(int(base_interval * 0.9), 5)
        return base_interval

    def can_make_request(self):
        """Rate Limit 및 연속 요청 체크"""
        self.reset_counters_if_needed()
        
        # ws_mode 또는 off 모드에서는 REST API 요청 금지
        if self.current_mode in ['ws_mode', 'off']:
            return False
            
        current_mode = self.get_current_trading_mode() # 이중 확인
        if current_mode in ['ws_mode', 'off']:
            return False
            
        now_time = time.time()
        min_interval = self.rate_config.get('min_request_interval', 2.5)
        if now_time - self.last_request_time < min_interval:
            return False
        consecutive_limit = self.rate_config.get('consecutive_limit', 10)
        if self.consecutive_requests >= consecutive_limit:
            logger.warning(f"⚠️ 연속 요청 제한 도달: {self.consecutive_requests}/{consecutive_limit}")
            time.sleep(5)
            self.consecutive_requests = 0
        if self.daily_api_count >= self.rate_config['daily_limit']:
            logger.warning(f"⚠️ 일일 API 한도 도달: {self.daily_api_count}/{self.rate_config['daily_limit']}")
            return False
        if self.hourly_api_count >= self.rate_config['hourly_limit']:
            logger.warning(f"⚠️ 시간당 API 한도 도달: {self.hourly_api_count}/{self.rate_config['hourly_limit']}")
            return False
        mode_limits = {'aggressive': self.rate_config['aggressive_mode_limit'], 'smart': self.rate_config['smart_mode_limit']}
        mode_count = self.stats.get(f'{current_mode}_mode_calls', 0)
        mode_limit = mode_limits.get(current_mode, 1000)
        if mode_count >= mode_limit:
            logger.warning(f"⚠️ {current_mode} 모드 한도 도달: {mode_count}/{mode_limit}")
            return False
        return True

    def reset_counters_if_needed(self):
        now = datetime.now()
        if now.date() != self.last_reset_date:
            logger.info(f"📊 일일 통계 리셋 - API: {self.daily_api_count}, 성공: {self.stats['successful_detections']}, WS: {self.stats['ws_detections']}")
            self.daily_api_count = 0
            self.last_reset_date = now.date()
            self.stats['successful_detections'] = 0
            self.stats['ws_detections'] = 0
            self.stats['aggressive_mode_calls'] = 0
            self.stats['smart_mode_calls'] = 0
            self.stats['rate_limit_violations'] = 0
            self.stats['api_errors'] = {}
            self.processed_ws_orders.clear() # 날짜 변경 시 WS 중복 방지 셋 초기화
        if now.hour != self.last_hour_reset:
            logger.debug(f"📊 시간별 API 리셋: {self.hourly_api_count}회")
            self.hourly_api_count = 0
            self.last_hour_reset = now.hour
            self.consecutive_requests = 0

    def handle_api_error(self, error_code, error_msg):
        self.stats['api_errors'][error_code] = self.stats['api_errors'].get(error_code, 0) + 1
        if error_code in ['EGW00101', 'EGW00102']:
            self.stats['rate_limit_violations'] += 1
            wait_time = self.rate_config.get('cooldown_on_limit', 60)
            logger.error(f"🚨 Rate Limit 감지! {wait_time}초 대기 (오류: {error_code})")
            if self.telegram_bot:
                message = f"⚠️ Rate Limit 감지\n🔸 오류: {error_code}\n⏰ 대기: {wait_time}초\n📊 일일 호출: {self.daily_api_count}회"
                self.telegram_bot.send_message(message)
            time.sleep(wait_time)
            return True
        elif error_code in ['EGW90001']:
            logger.warning(f"⚠️ 일시적 오류: {error_code} - {error_msg}")
            time.sleep(5)
            return False
        else:
            logger.error(f"❌ API 오류: {error_code} - {error_msg}")
        return False

    def add_order_to_monitor(self, order_no, ticker, quantity, buy_price, order_time=None):
        if not order_time:
            order_time = datetime.now()
        order_info = {
            'ticker': ticker,
            'quantity': quantity,
            'buy_price': buy_price,
            'created_at': order_time,
            'last_checked': None,
            'check_count': 0,
            'no_change_count': 0,
            'consecutive_successes': 0,
            'consecutive_failures': 0,
            'last_status': None,
            'mode_when_created': self.get_current_trading_mode()
        }
        self.monitoring_orders[order_no] = order_info
        current_mode = self.get_current_trading_mode()
        logger.info(f"📝 주문 등록: {order_no} ({ticker} {quantity}주 @ ${buy_price}) - 모드: {current_mode}")
        self.save_state()
        if self.telegram_bot:
            mode_emoji = {'aggressive': '🔥', 'smart': '🧠', 'off': '⏸️', 'ws_mode': '⚡️'}
            message = f"{mode_emoji.get(current_mode, '📝')} 주문 등록\n📄 {order_no}\n🏷️ {ticker} {quantity}주\n💰 ${buy_price}"
            self.telegram_bot.send_message(message)

    def check_order_status_smart(self, order_no):
        if not self.can_make_request():
            return None
        try:
            url = f"{self.config['api']['base_url']}/uapi/overseas-stock/v1/trading/inquire-nccs"
            token = self.token_manager.get_access_token()
            if not token:
                logger.error("토큰을 가져올 수 없습니다.")
                return None
            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": self.config['api_key'],
                "appsecret": self.config['api_secret'],
                "tr_id": "TTTS3035R"
            }
            today = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.fromisoformat(last_check).strftime("%Y%m%d") 
                          if last_check else today)
            params = {
                "CANO": self.config['cano'],
                "ACNT_PRDT_CD": self.config['acnt_prdt_cd'],
                "OVRS_EXCG_CD": "NASD",
                "ORD_STRT_DT": today,
                "ORD_END_DT": today,
                "SLL_BUY_DVSN": "02", # 매수만 조회 (공식 파라미터)
                "CCLD_DVSN": "01",       # 체결된 것만 조회 (공식)
                "PDNO": "",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": ""
            }
            request_start = time.time()
            response = requests.get(url, headers=headers, params=params, timeout=15)
            self.last_request_time = time.time()
            self.consecutive_requests += 1
            self.daily_api_count += 1
            self.hourly_api_count += 1
            self.stats['total_requests'] += 1
            current_mode = self.get_current_trading_mode()
            self.stats[f'{current_mode}_mode_calls'] = self.stats.get(f'{current_mode}_mode_calls', 0) + 1
            if response.status_code != 200:
                logger.error(f"HTTP 오류: {response.status_code}")
                return None
            data = response.json()
            error_code = data.get("msg_cd", "")
            if error_code and error_code != "MCA00000":
                error_msg = data.get('msg1', 'Unknown error')
                if self.handle_api_error(error_code, error_msg):
                    return None
            if data.get("rt_cd") != "0":
                logger.error(f"API 오류: {data.get('msg1', 'Unknown')}")
                return None
            response_time = time.time() - request_start
            if response_time > 5:
                logger.warning(f"⏰ 느린 API 응답: {response_time:.2f}초")
            for item in data.get("output", []):
                if item.get("odno") == order_no:
                    ord_status = item.get("ord_stcd", "")
                    ccld_qty = item.get("ccld_qty", "0")
                    ccld_unpr = item.get("ccld_unpr", "0")
                    return {
                        'status': ord_status,
                        'filled_qty': int(ccld_qty) if ccld_qty.isdigit() else 0,
                        'filled_price': float(ccld_unpr) if ccld_unpr.replace('.', '').isdigit() else 0.0
                    }
            return {'status': '조회없음', 'filled_qty': 0, 'filled_price': 0.0}
        except requests.exceptions.Timeout:
            logger.warning(f"⏰ API 타임아웃: {order_no}")
            return None
        except Exception as e:
            logger.error(f"상태 확인 오류: {e}")
            return None

    def execute_auto_sell(self, order_info, filled_price):
        try:
            from order import place_sell_order # order.py에서 함수 import
            current_mode = self.get_current_trading_mode()
            profit_margin = self.config.get('strategy', {}).get('smart_strategy', {}).get('target_profit_margin', 0.03)
            
            # ws_mode일 때도 aggressive 전략을 따르도록 설정 (또는 별도 ws_strategy 설정)
            if current_mode in ['aggressive', 'ws_mode'] and 'aggressive_strategy' in self.config.get('strategy', {}):
                profit_margin = self.config['strategy']['aggressive_strategy']['target_profit_margin']
                
            sell_price = round(filled_price * (1 + profit_margin), 2)
            execution_data = {
                'ticker': order_info['ticker'],
                'quantity': order_info['quantity'],
                'price': filled_price
            }
            logger.info(f"🎯 체결 감지! {execution_data['ticker']} ${filled_price} → 매도 ${sell_price} (모드: {current_mode})")
            
            success = place_sell_order(self.config, self.token_manager, execution_data, self.telegram_bot)
            
            if success:
                # 통계는 WS와 REST 구분
                if current_mode == 'ws_mode':
                    self.stats['ws_detections'] += 1
                    total_detected = self.stats['ws_detections']
                else:
                    self.stats['successful_detections'] += 1
                    total_detected = self.stats['successful_detections']
                
                logger.info(f"✅ 자동 매도 성공: {execution_data['ticker']} (총 감지: {total_detected}회)")
                
                if self.telegram_bot:
                    mode_emoji = {'aggressive': '🔥', 'smart': '🧠', 'ws_mode': '⚡️'}
                    message = f"{mode_emoji.get(current_mode, '🎉')} 매도 성공!\n🏷️ {execution_data['ticker']}\n💰 ${filled_price} → ${sell_price}\n📈 +{profit_margin*100:.1f}%\n📊 총 {current_mode} 감지: {total_detected}회"
                    self.telegram_bot.send_message(message)
            return success
        except Exception as e:
            logger.error(f"자동 매도 실행 오류: {e}")
            return False

    # ✅ [추가] WebSocket 메시지 핸들러
    def handle_ws_message(self, message):
        """WebSocket으로부터 실시간 체결 메시지 처리 (H0STCNI0)"""
        try:
            # KIS WebSocket 메시지는 JSON 문자열
            data = json.loads(message)
            
            # 실시간 체결 데이터 (H0STCNI0)
            if data.get('header', {}).get('tr_id') == 'H0STCNI0':
                body = data.get('body', {})
                if not body:
                    return

                # 'output'이 리스트일 수 있음 (여러 체결 동시)
                outputs = body.get('output', [])
                if not isinstance(outputs, list):
                    outputs = [outputs] # 단일 객체면 리스트로 감싸기

                for item in outputs:
                    # 매수(02) 체결만 처리
                    if item.get('sll_buy_dvsn_cd') != '02':
                        continue
                        
                    order_no = item.get("odno", "")
                    if not order_no:
                        continue

                    # ✅ 중복 처리 방지
                    if order_no in self.processed_ws_orders:
                        logger.debug(f"이미 처리된 WS 체결: {order_no}")
                        continue

                    ticker = item.get("pdno", "")
                    try:
                        ccld_qty = int(item.get("ccld_qty", "0"))
                        ccld_price = float(item.get("ccld_unpr", "0"))
                    except ValueError:
                        logger.warning(f"WS 데이터 파싱 오류: {item}")
                        continue

                    if ccld_qty > 0 and ccld_price > 0:
                        logger.info(f"🎉 [WS] 신규 매수 체결 발견! {order_no}: {ticker} {ccld_qty}주 @ ${ccld_price}")
                        
                        order_info = {
                            'ticker': ticker,
                            'quantity': ccld_qty,
                            'buy_price': ccld_price,
                            'created_at': datetime.now(),
                            # (execute_auto_sell에 필요한 최소 정보)
                        }
                        
                        # 즉시 자동 매도 실행
                        success = self.execute_auto_sell(order_info, ccld_price)
                        
                        if success:
                            logger.info(f"✅ [WS] 자동 매도 주문 즉시 성공: {ticker}")
                            self.processed_ws_orders.add(order_no) # 성공 시 중복 방지 셋에 추가
                        else:
                            logger.error(f"❌ [WS] 자동 매도 주문 실패: {ticker}. REST 폴링으로 전환.")
                            # 실패 시 REST 폴링 모니터링에 등록 (다음 모드 전환 시 폴링됨)
                            self.add_order_to_monitor(order_no, ticker, ccld_qty, ccld_price)

        except json.JSONDecodeError:
            logger.debug(f"WS 메시지 파싱 실패 (JSON 아님): {message[:50]}...")
        except Exception as e:
            logger.error(f"WS 메시지 처리 오류: {e} - 메시지: {message}")

    def scan_for_new_buy_orders(self):
        """MTS 매수 주문 자동 감지 및 모니터링 등록 (폴링 모드용)"""
        try:
            if not self.can_make_request():
                return
            url = f"{self.config['api']['base_url']}/uapi/overseas-stock/v1/trading/inquire-nccs"
            token = self.token_manager.get_access_token()
            if not token:
                logger.error("토큰을 가져올 수 없습니다.")
                return
            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": self.config['api_key'],
                "appsecret": self.config['api_secret'],
                "tr_id": "TTTS3035R"
            }
            today = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.fromisoformat(last_check).strftime("%Y%m%d") 
                          if last_check else today)
            params = {
                "CANO": self.config['cano'],
                "ACNT_PRDT_CD": self.config['acnt_prdt_cd'],
                "OVRS_EXCG_CD": "NASD",
                "ORD_STRT_DT": today,
                "ORD_END_DT": today,
                "SLL_BUY_DVSN": "02", # 매수 체결만
                "CCLD_DVSN": "01",       # 체결된 것만
                "PDNO": "",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": ""
            }
            response = requests.get(url, headers=headers, params=params, timeout=15)
            self.last_request_time = time.time()
            self.consecutive_requests += 1
            self.daily_api_count += 1
            self.hourly_api_count += 1
            self.stats['total_requests'] += 1
            if response.status_code != 200:
                logger.error(f"매수 감지 HTTP 오류: {response.status_code}")
                return
            data = response.json()
            if data.get("rt_cd") != "0":
                return
            for order in data.get("output", []):
                order_no = order.get("odno", "")
                ord_status = order.get("ord_stcd", "")
                
                # ✅ [수정] 폴링 감지 시 WS에서 이미 처리했는지 확인
                if order_no in self.monitoring_orders or order_no in self.processed_ws_orders:
                    continue
                    
                if ord_status in ["02", "체결완료"] and order.get("SLL_BUY_DVSN_CD") == "02":
                    ticker = order.get("pdno", "")
                    ccld_qty = order.get("ccld_qty", "0")
                    ccld_price = order.get("ccld_unpr", "0")
                    try:
                        ccld_qty = int(ccld_qty) if ccld_qty else 0
                        ccld_price = float(ccld_price) if ccld_price else 0.0
                    except:
                        continue
                    if ccld_qty > 0 and ccld_price > 0:
                        logger.info(f"🎉 [POLL] 신규 매수 체결 발견! {order_no}: {ticker} {ccld_qty}주 @ ${ccld_price}")
                        order_info = {
                            'ticker': ticker,
                            'quantity': ccld_qty,
                            'buy_price': ccld_price,
                            'created_at': datetime.now(),
                            'last_checked': None,
                            'check_count': 0,
                            'no_change_count': 0,
                            'consecutive_successes': 0,
                            'consecutive_failures': 0,
                            'last_status': None,
                            'mode_when_created': self.get_current_trading_mode()
                        }
                        success = self.execute_auto_sell(order_info, ccld_price)
                        if success:
                            logger.info(f"✅ [POLL] 자동 매도 주문 즉시 성공: {ticker}")
                            if self.telegram_bot:
                                profit_rate = self.config.get('strategy', {}).get('smart_strategy', {}).get('target_profit_margin', 0.03) * 100
                                message = f"🎉 [POLL] 자동 매수 감지 & 매도 성공!\n🏷️ {ticker} {ccld_qty}주\n💰 매수: ${ccld_price}\n📈 목표 수익: +{profit_rate}%"
                                self.telegram_bot.send_message(message)
                        else:
                            logger.error(f"❌ [POLL] 자동 매도 주문 실패: {ticker}. 폴링 리스트 추가.")
                            self.add_order_to_monitor(order_no, ticker, ccld_qty, ccld_price)
                            
        except Exception as e:
            logger.error(f"매수 감지 스캔 오류: {e}")

    def cleanup_expired_orders(self):
        now = datetime.now()
        expired_orders = []
        for order_no, order_info in self.monitoring_orders.items():
            age_hours = (now - order_info['created_at']).total_seconds() / 3600
            current_mode = self.get_current_trading_mode()
            max_hours = 0.5 if current_mode == 'aggressive' else 2
            if age_hours > max_hours:
                expired_orders.append(order_no)
        for order_no in expired_orders:
            order_info = self.monitoring_orders.pop(order_no, None)
            if order_info:
                age_hours = (now - order_info['created_at']).total_seconds() / 3600
                logger.info(f"⏰ 감시 시간 만료: {order_no} ({age_hours:.1f}시간)")
        if expired_orders:
            self.save_state()

    def smart_monitor_loop(self):
        logger.info("🚀 KIS API 실전 최적화 폴링/WS 시스템 시작")
        while self.is_running:
            try:
                # ✅ [수정] 모드 전환 (ws_mode 포함)
                if self.switch_mode_if_needed():
                    if self.current_mode == 'off':
                        time.sleep(300) # 'off' 모드면 5분 대기
                        continue

                # ✅ [추가] ws_mode일 경우 폴링 로직 전체를 건너뛰기
                if self.current_mode == 'ws_mode':
                    logger.debug("⚡️ 실시간 WebSocket 모드... 폴링 중지.")
                    time.sleep(5) # CPU 방지를 위해 5초 대기
                    continue
                
                # ▼▼▼ 기존 'aggressive' 또는 'smart' 모드일 때만 아래 폴링 로직 실행 ▼▼▼
                
                current_time = time.time()
                if not hasattr(self, 'last_buy_scan') or current_time - self.last_buy_scan > 15:
                    logger.debug("🔍 자동 매수 감지 스캔 시작...")
                    self.scan_for_new_buy_orders()
                    self.last_buy_scan = current_time
                    
                if not self.monitoring_orders:
                    time.sleep(30)
                    continue
                    
                current_mode = self.get_current_trading_mode() # 폴링 중 모드 변경 대비
                self.cleanup_expired_orders()
                processed_count = 0
                
                for order_no, order_info in list(self.monitoring_orders.items()):
                    if not self.is_running: break
                    
                    polling_interval = self.calculate_polling_interval(order_no, order_info)
                    now = datetime.now()
                    
                    if (order_info['last_checked'] and (now - order_info['last_checked']).total_seconds() < polling_interval):
                        continue
                        
                    status_info = self.check_order_status_smart(order_no)
                    order_info['last_checked'] = now
                    order_info['check_count'] += 1
                    processed_count += 1
                    
                    if status_info is None:
                        order_info['consecutive_failures'] += 1
                        order_info['consecutive_successes'] = 0
                        continue
                        
                    order_info['consecutive_failures'] = 0
                    order_info['consecutive_successes'] += 1
                    current_status = status_info['status']
                    
                    if current_status == order_info['last_status']:
                        order_info['no_change_count'] += 1
                    else:
                        order_info['no_change_count'] = 0
                        logger.debug(f"🔄 상태 변화: {order_no} → {current_status}")
                    
                    order_info['last_status'] = current_status
                    
                    if current_status in ['02','체결완료','완전체결'] and status_info['filled_qty'] > 0:
                        logger.info(f"🎉 [POLL] 체결 완료: {order_no} (모드: {current_mode}, 체크: {order_info['check_count']}회)")
                        self.execute_auto_sell(order_info, status_info['filled_price'])
                        self.monitoring_orders.pop(order_no, None)
                        self.save_state()
                        
                    time.sleep(max(1, self.rate_config.get('min_request_interval', 2.5)-1))
                    
                if current_mode == 'aggressive':
                    time.sleep(2)
                elif current_mode == 'smart':
                    time.sleep(5)
                else: # 'off' 모드로 변경된 경우
                    time.sleep(60)
                    
                if processed_count > 0 and processed_count % 20 == 0:
                    self.save_state()
                    
                if self.stats['total_requests'] > 0 and self.stats['total_requests'] % 100 == 0:
                    rate_limit_rate = (self.stats['rate_limit_violations'] / self.stats['total_requests']) * 100
                    logger.info(f"📊 통계 - 요청: {self.stats['total_requests']}, 성공: {self.stats['successful_detections']}, WS: {self.stats['ws_detections']}, Rate Limit: {rate_limit_rate:.1f}%")
                    
            except Exception as e:
                logger.error(f"메인 루프 오류: {e}")
                time.sleep(30)
                
        logger.info("🛑 KIS API 실전 최적화 폴링/WS 시스템 종료")

    def start(self):
        if self.is_running:
            logger.warning("이미 모니터링이 실행 중입니다.")
            return
        self.current_mode = self.get_current_trading_mode()
        self.is_running = True
        
        # ✅ [추가] WebSocket 클라이언트 시작
        logger.info("🔌 WebSocket 클라이언트 시작 시도...")
        self.ws_client.start()
        
        self.monitor_thread = threading.Thread(target=self.smart_monitor_loop, daemon=True)
        self.monitor_thread.start()
        logger.info(f"🚀 KIS API 실전 모니터링 시작 - 초기 모드: {self.current_mode}")

    def stop(self):
        if not self.is_running:
            return
            
        logger.info("🛑 모니터링 중지 요청...")
        self.is_running = False
        
        # ✅ [추가] WebSocket 클라이언트 중지
        logger.info("🔌 WebSocket 클라이언트 중지 시도...")
        self.ws_client.stop()

        self.save_state()
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=10)
        logger.info(f"🛑 모니터링 중지 완료 - 최종 통계: 요청 {self.stats['total_requests']}회, 성공 {self.stats['successful_detections']}회, WS {self.stats['ws_detections']}회")

    def get_monitoring_count(self):
        return len(self.monitoring_orders)

    def get_detailed_stats(self):
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
            'aggressive_calls': self.stats.get('aggressive_mode_calls', 0),
            'smart_calls': self.stats.get('smart_mode_calls', 0),
            'rate_limit_violations': self.stats['rate_limit_violations'],
            'api_errors': self.stats['api_errors'],
            'consecutive_requests': self.consecutive_requests
        }