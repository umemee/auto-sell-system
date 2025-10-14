# smart_order_monitor.py - KIS API 실전 환경 최적화 (피드백 반영)

import requests
import json
import logging
import time
import threading
import os
from datetime import datetime, timedelta, time as dtime
from pytz import timezone

logger = logging.getLogger(__name__)

class SmartOrderMonitor:
    """KIS API 실전 환경 최적화 집중/스마트 폴링 시스템"""

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
        
        # 집중 폴링 설정 (안전성 강화)
        self.aggressive_config = config['polling']['aggressive']
        self.aggressive_interval = self.aggressive_config['interval']  # 3초
        
        # 스마트 폴링 설정
        self.smart_config = config['polling']['smart']
        self.smart_initial_interval = self.smart_config['initial_interval']
        self.smart_max_interval = self.smart_config['max_interval']
        self.backoff_multiplier = self.smart_config['backoff_multiplier']
        
        # ✅ Rate Limit 보호 강화 (피드백 반영)
        self.rate_config = config['rate_limit']
        self.daily_api_count = 0
        self.last_reset_date = datetime.now().date()
        self.hourly_api_count = 0
        self.last_hour_reset = datetime.now().hour
        self.last_request_time = 0
        self.consecutive_requests = 0
        
        # ✅ 상태 영속화 (피드백 반영)
        self.state_file = config['system'].get('state_file', '/tmp/auto-sell-order-state.json')
        self.load_persisted_state()
        
        # 통계
        self.stats = {
            'total_requests': 0,
            'successful_detections': 0,
            'aggressive_mode_calls': 0,
            'smart_mode_calls': 0,
            'mode_switches': 0,
            'rate_limit_violations': 0,
            'api_errors': {}
        }

    def load_persisted_state(self):
        """저장된 상태 복원"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                    
                # 저장된 주문 복원 (최근 1시간 이내만)
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
            state = {
                'timestamp': datetime.now().isoformat(),
                'orders': {}
            }
            
            for order_no, order_data in self.monitoring_orders.items():
                # datetime 객체를 문자열로 변환
                order_copy = order_data.copy()
                order_copy['created_at'] = order_data['created_at'].isoformat()
                if order_data.get('last_checked'):
                    order_copy['last_checked'] = order_data['last_checked'].isoformat()
                state['orders'][order_no] = order_copy
            
            # 디렉토리 생성
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            logger.warning(f"상태 저장 실패: {e}")

    def get_current_trading_mode(self):
        """현재 시간에 따른 매매 모드 판별 (시간대 구분)"""
        try:
            # ✅ 표시용은 KST, 내부 로직은 ET (피드백 반영)
            kst = timezone('Asia/Seoul')
            now_kst = datetime.now(kst).time()
            
            # 완전 중지 시간 (01:00-17:00 KST)
            off_start = dtime(1, 0)
            off_end = dtime(17, 0)
            
            if off_start <= now_kst < off_end:
                return 'off'
            
            # 집중 매매 시간 체크
            for time_range in self.aggressive_config['time_ranges']:
                start_time = dtime(*[int(x) for x in time_range['start'].split(':')])
                end_time = dtime(*[int(x) for x in time_range['end'].split(':')])
                
                if start_time <= now_kst < end_time:
                    return 'aggressive'
            
            # 스마트 폴링 시간 체크
            for time_range in self.smart_config['time_ranges']:
                start_time = dtime(*[int(x) for x in time_range['start'].split(':')])
                end_time = dtime(*[int(x) for x in time_range['end'].split(':')])
                
                if start_time <= now_kst < end_time:
                    return 'smart'
            
            return 'off'
            
        except Exception as e:
            logger.error(f"모드 판별 오류: {e}")
            return 'smart'  # 기본값

    def switch_mode_if_needed(self):
        """필요 시 모드 전환"""
        new_mode = self.get_current_trading_mode()
        
        if new_mode != self.current_mode:
            old_mode = self.current_mode
            self.current_mode = new_mode
            self.last_mode_change = datetime.now()
            self.stats['mode_switches'] += 1
            
            logger.info(f"🔄 매매 모드 전환: {old_mode} → {new_mode}")
            
            if self.telegram_bot:
                mode_names = {
                    'aggressive': '🔥 집중 매매 (3초 간격)',
                    'smart': '🧠 스마트 폴링 (5-20초)',
                    'off': '⏸️ 중지 (취침)'
                }
                message = f"🔄 모드 전환\\n{mode_names.get(old_mode, old_mode)} → {mode_names.get(new_mode, new_mode)}"
                self.telegram_bot.send_message(message)
            
            # 상태 저장
            self.save_state()
            
            # 중지 모드일 때 처리
            if new_mode == 'off':
                self.stop_for_off_hours()
                return True
                
        return False

    def stop_for_off_hours(self):
        """중지 시간 처리"""
        logger.info("⏸️ 매매 중지 시간 - 모니터링 일시 중지")
        
        if self.telegram_bot:
            next_start = "17:00 KST"
            message = f"😴 취침 모드 시작\\n⏰ 다음 시작: {next_start}\\n📊 오늘 통계:\\n- 총 요청: {self.stats['total_requests']}회\\n- 성공 감지: {self.stats['successful_detections']}회\\n- Rate Limit: {self.stats['rate_limit_violations']}회"
            self.telegram_bot.send_message(message)

    def calculate_polling_interval(self, order_no, order_info):
        """모드별 폴링 간격 계산"""
        current_mode = self.get_current_trading_mode()
        
        if current_mode == 'off':
            return 3600  # 1시간 (사실상 중지)
        
        elif current_mode == 'aggressive':
            # 집중 매매: 안전한 빠른 폴링 (3초)
            return self.aggressive_interval
        
        elif current_mode == 'smart':
            # 스마트 폴링: 적응형 간격
            return self.calculate_smart_interval(order_info)
        
        return self.smart_initial_interval

    def calculate_smart_interval(self, order_info):
        """스마트 폴링 간격 계산 (안전성 강화)"""
        now = datetime.now()
        
        # 주문 나이별 기본 간격 설정
        order_age_minutes = (now - order_info['created_at']).total_seconds() / 60
        
        # config의 order_age_factor 사용
        base_interval = self.smart_initial_interval
        for age_config in self.smart_config['order_age_factor']:
            if 'minutes' in age_config and order_age_minutes >= age_config['minutes']:
                base_interval = age_config['interval']
            elif 'default' in age_config:
                base_interval = age_config['default']
        
        # 상태 변화 없음에 따른 백오프
        if order_info['no_change_count'] > self.smart_config['no_change_threshold']:
            excess_count = order_info['no_change_count'] - self.smart_config['no_change_threshold']
            backoff_factor = self.backoff_multiplier ** min(excess_count, 4)  # 최대 4제곱까지
            base_interval = min(int(base_interval * backoff_factor), self.smart_max_interval)
        
        # 연속 성공 시 간격 단축 (제한적)
        if (self.smart_config.get('consecutive_success_speedup', False) and 
            order_info.get('consecutive_successes', 0) > 3):
            base_interval = max(int(base_interval * 0.9), 5)  # 최소 5초
        
        return base_interval

    def can_make_request(self):
        """✅ Rate Limit 체크 강화 (피드백 반영)"""
        self.reset_counters_if_needed()
        
        current_mode = self.get_current_trading_mode()
        
        if current_mode == 'off':
            return False
        
        # ✅ 최소 간격 보장 (EGW00101 방지)
        now_time = time.time()
        min_interval = self.rate_config.get('min_request_interval', 2.5)
        
        if now_time - self.last_request_time < min_interval:
            return False
        
        # ✅ 연속 요청 제한
        consecutive_limit = self.rate_config.get('consecutive_limit', 10)
        if self.consecutive_requests >= consecutive_limit:
            logger.warning(f"⚠️ 연속 요청 제한 도달: {self.consecutive_requests}/{consecutive_limit}")
            time.sleep(5)  # 5초 대기
            self.consecutive_requests = 0
        
        # 일일 한도 체크
        if self.daily_api_count >= self.rate_config['daily_limit']:
            logger.warning(f"⚠️ 일일 API 한도 도달: {self.daily_api_count}/{self.rate_config['daily_limit']}")
            return False
        
        # 시간당 한도 체크
        if self.hourly_api_count >= self.rate_config['hourly_limit']:
            logger.warning(f"⚠️ 시간당 API 한도 도달: {self.hourly_api_count}/{self.rate_config['hourly_limit']}")
            return False
        
        # 모드별 한도 체크
        mode_limits = {
            'aggressive': self.rate_config['aggressive_mode_limit'],
            'smart': self.rate_config['smart_mode_limit']
        }
        
        mode_count = self.stats.get(f'{current_mode}_mode_calls', 0)
        mode_limit = mode_limits.get(current_mode, 1000)
        
        if mode_count >= mode_limit:
            logger.warning(f"⚠️ {current_mode} 모드 한도 도달: {mode_count}/{mode_limit}")
            return False
        
        return True

    def reset_counters_if_needed(self):
        """카운터 리셋 (일일/시간별)"""
        now = datetime.now()
        
        # 일일 리셋
        if now.date() != self.last_reset_date:
            logger.info(f"📊 일일 통계 리셋 - API: {self.daily_api_count}, 성공: {self.stats['successful_detections']}")
            self.daily_api_count = 0
            self.last_reset_date = now.date()
            self.stats['successful_detections'] = 0
            self.stats['aggressive_mode_calls'] = 0
            self.stats['smart_mode_calls'] = 0
            self.stats['rate_limit_violations'] = 0
            self.stats['api_errors'] = {}
        
        # 시간별 리셋
        if now.hour != self.last_hour_reset:
            logger.debug(f"📊 시간별 API 리셋: {self.hourly_api_count}회")
            self.hourly_api_count = 0
            self.last_hour_reset = now.hour
            self.consecutive_requests = 0  # 시간별로 연속 요청도 리셋

    def handle_api_error(self, error_code, error_msg):
        """✅ KIS API 오류 코드별 처리 (피드백 반영)"""
        self.stats['api_errors'][error_code] = self.stats['api_errors'].get(error_code, 0) + 1
        
        # EGW00101, EGW00102: Rate Limit 오류
        if error_code in ['EGW00101', 'EGW00102']:
            self.stats['rate_limit_violations'] += 1
            wait_time = self.rate_config.get('cooldown_on_limit', 60)
            
            logger.error(f"🚨 Rate Limit 감지! {wait_time}초 대기 (오류: {error_code})")
            
            if self.telegram_bot:
                message = f"⚠️ Rate Limit 감지\\n🔸 오류: {error_code}\\n⏰ 대기: {wait_time}초\\n📊 일일 호출: {self.daily_api_count}회"
                self.telegram_bot.send_message(message)
            
            time.sleep(wait_time)
            return True
            
        # EGW90001: 일시적 오류 (재시도)
        elif error_code in ['EGW90001']:
            logger.warning(f"⚠️ 일시적 오류: {error_code} - {error_msg}")
            time.sleep(5)
            return False
            
        else:
            logger.error(f"❌ API 오류: {error_code} - {error_msg}")
            return False

    def add_order_to_monitor(self, order_no, ticker, quantity, buy_price, order_time=None):
        """주문 모니터링 등록"""
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
        
        # 상태 저장
        self.save_state()
        
        if self.telegram_bot:
            mode_emoji = {'aggressive': '🔥', 'smart': '🧠', 'off': '⏸️'}
            message = f"{mode_emoji.get(current_mode, '📝')} 주문 등록\\n📄 {order_no}\\n🏷️ {ticker} {quantity}주\\n💰 ${buy_price}"
            self.telegram_bot.send_message(message)

    def check_order_status_smart(self, order_no):
        """주문 상태 확인 (안전성 강화)"""
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
                "tr_id": "JTTT3010R"
            }

            today = datetime.now().strftime("%Y%m%d")
            params = {
                "CANO": self.config['cano'],
                "ACNT_PRDT_CD": self.config['acnt_prdt_cd'],
                "ORD_STRT_DT": today,
                "ORD_END_DT": today,
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": ""
            }

            # ✅ 요청 시간 기록
            request_start = time.time()
            
            response = requests.get(url, headers=headers, params=params, 
                                 timeout=self.config.get('kis_api', {}).get('request_timeout', 15))
            
            # 카운터 업데이트
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
            
            # ✅ 오류 코드 처리
            error_code = data.get("msg_cd", "")
            if error_code and error_code != "MCA00000":  # 정상 응답이 아닌 경우
                error_msg = data.get('msg1', 'Unknown error')
                if self.handle_api_error(error_code, error_msg):
                    return None  # Rate Limit 등으로 대기 중
            
            if data.get("rt_cd") != "0":
                logger.error(f"API 오류: {data.get('msg1', 'Unknown')}")
                return None

            # 응답 시간 로깅
            response_time = time.time() - request_start
            if response_time > 5:
                logger.warning(f"⏰ 느린 API 응답: {response_time:.2f}초")

            # 주문 찾기
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
        """자동 매도 실행"""
        try:
            from order import place_sell_order
            
            # 현재 모드에 따른 수익률 조정
            current_mode = self.get_current_trading_mode()
            if current_mode == 'aggressive' and 'aggressive_strategy' in self.config.get('strategy', {}):
                profit_margin = self.config['strategy']['aggressive_strategy']['target_profit_margin']
            else:
                profit_margin = self.config.get('strategy', {}).get('smart_strategy', {}).get('target_profit_margin', 0.03)
            
            sell_price = round(filled_price * (1 + profit_margin), 2)

            execution_data = {
                'ticker': order_info['ticker'],
                'quantity': order_info['quantity'],
                'price': filled_price
            }

            logger.info(f"🎯 체결 감지! {execution_data['ticker']} ${filled_price} → 매도 ${sell_price} (모드: {current_mode})")

            success = place_sell_order(self.config, self.token_manager, execution_data, self.telegram_bot)

            if success:
                self.stats['successful_detections'] += 1
                logger.info(f"✅ 자동 매도 성공: {execution_data['ticker']} (총 감지: {self.stats['successful_detections']}회)")

                if self.telegram_bot:
                    mode_emoji = {'aggressive': '🔥', 'smart': '🧠'}
                    message = f"{mode_emoji.get(current_mode, '🎉')} 매도 성공!\\n🏷️ {execution_data['ticker']}\\n💰 ${filled_price} → ${sell_price}\\n📈 +{profit_margin*100:.1f}%\\n📊 총 감지: {self.stats['successful_detections']}회"
                    self.telegram_bot.send_message(message)

            return success

        except Exception as e:
            logger.error(f"자동 매도 실행 오류: {e}")
            return False

    def cleanup_expired_orders(self):
        """만료된 주문 정리"""
        now = datetime.now()
        expired_orders = []

        for order_no, order_info in self.monitoring_orders.items():
            age_hours = (now - order_info['created_at']).total_seconds() / 3600
            
            # 모드별 최대 보유 시간 설정
            current_mode = self.get_current_trading_mode()
            if current_mode == 'aggressive':
                max_hours = 0.5  # 30분
            else:
                max_hours = 2    # 2시간
                
            if age_hours > max_hours:
                expired_orders.append(order_no)

        for order_no in expired_orders:
            order_info = self.monitoring_orders.pop(order_no, None)
            if order_info:
                age_hours = (now - order_info['created_at']).total_seconds() / 3600
                logger.info(f"⏰ 감시 시간 만료: {order_no} ({age_hours:.1f}시간)")
                
        if expired_orders:
            self.save_state()  # 상태 저장

    def smart_monitor_loop(self):
        """메인 모니터링 루프 (안정성 강화)"""
        logger.info("🚀 KIS API 실전 최적화 폴링 시스템 시작")
        
        while self.is_running:
            try:
                # 모드 전환 체크
                if self.switch_mode_if_needed():
                    if self.get_current_trading_mode() == 'off':
                        # 중지 시간 대기
                        time.sleep(300)  # 5분 대기
                        continue
                
                # 모니터링할 주문 없으면 대기
                if not self.monitoring_orders:
                    time.sleep(30)
                    continue
                
                current_mode = self.get_current_trading_mode()
                
                # 만료된 주문 정리
                self.cleanup_expired_orders()
                
                # 각 주문별 처리
                processed_count = 0
                for order_no, order_info in list(self.monitoring_orders.items()):
                    if not self.is_running:
                        break
                    
                    # 폴링 간격 계산
                    polling_interval = self.calculate_polling_interval(order_no, order_info)
                    
                    # 간격 체크
                    now = datetime.now()
                    if (order_info['last_checked'] and 
                        (now - order_info['last_checked']).total_seconds() < polling_interval):
                        continue
                    
                    # 상태 확인
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
                    
                    # 상태 변화 확인
                    if current_status == order_info['last_status']:
                        order_info['no_change_count'] += 1
                    else:
                        order_info['no_change_count'] = 0
                        logger.debug(f"🔄 상태 변화: {order_no} → {current_status}")
                    
                    order_info['last_status'] = current_status
                    
                    # 체결 완료 확인
                    if current_status in ['체결완료', '완전체결'] and status_info['filled_qty'] > 0:
                        logger.info(f"🎉 체결 완료: {order_no} (모드: {current_mode}, 체크: {order_info['check_count']}회)")
                        
                        self.execute_auto_sell(order_info, status_info['filled_price'])
                        self.monitoring_orders.pop(order_no, None)
                        self.save_state()  # 상태 저장
                    
                    # ✅ API 부하 방지 (안전 간격)
                    time.sleep(max(1, self.rate_config.get('min_request_interval', 2.5) - 1))
                
                # 메인 루프 간격 (모드별)
                if current_mode == 'aggressive':
                    time.sleep(2)  # 2초
                elif current_mode == 'smart':
                    time.sleep(5)  # 5초
                else:
                    time.sleep(60)  # 60초
                
                # 주기적 상태 저장 (5분마다)
                if processed_count > 0 and processed_count % 20 == 0:
                    self.save_state()
                
                # 통계 출력 (100회마다)
                if self.stats['total_requests'] > 0 and self.stats['total_requests'] % 100 == 0:
                    rate_limit_rate = (self.stats['rate_limit_violations'] / self.stats['total_requests']) * 100
                    logger.info(f"📊 통계 - 요청: {self.stats['total_requests']}, 성공: {self.stats['successful_detections']}, Rate Limit: {rate_limit_rate:.1f}%")

            except Exception as e:
                logger.error(f"메인 루프 오류: {e}")
                time.sleep(30)

        logger.info("🛑 KIS API 실전 최적화 폴링 시스템 종료")

    def start(self):
        """모니터링 시작"""
        if self.is_running:
            logger.warning("이미 모니터링이 실행 중입니다.")
            return

        self.current_mode = self.get_current_trading_mode()
        self.is_running = True
        self.monitor_thread = threading.Thread(target=self.smart_monitor_loop, daemon=True)
        self.monitor_thread.start()
        
        logger.info(f"🚀 KIS API 실전 모니터링 시작 - 초기 모드: {self.current_mode}")

    def stop(self):
        """모니터링 중지"""
        if not self.is_running:
            return

        self.is_running = False
        
        # 상태 저장
        self.save_state()
        
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=10)
        
        logger.info(f"🛑 모니터링 중지 - 최종 통계: 요청 {self.stats['total_requests']}회, 성공 {self.stats['successful_detections']}회")

    def get_monitoring_count(self):
        """현재 모니터링 중인 주문 수"""
        return len(self.monitoring_orders)

    def get_detailed_stats(self):
        """상세 통계"""
        current_mode = self.get_current_trading_mode()
        return {
            'monitoring_count': len(self.monitoring_orders),
            'current_mode': current_mode,
            'daily_api_calls': self.daily_api_count,
            'hourly_api_calls': self.hourly_api_count,
            'total_requests': self.stats['total_requests'],
            'successful_detections': self.stats['successful_detections'],
            'mode_switches': self.stats['mode_switches'],
            'aggressive_calls': self.stats.get('aggressive_mode_calls', 0),
            'smart_calls': self.stats.get('smart_mode_calls', 0),
            'rate_limit_violations': self.stats['rate_limit_violations'],
            'api_errors': self.stats['api_errors'],
            'consecutive_requests': self.consecutive_requests
        }


# 유틸리티 함수
def is_trading_hours():
    """거래 시간 여부 확인 (KST 기준)"""
    try:
        kst = timezone('Asia/Seoul')
        now = datetime.now(kst).time()
        
        # 거래 시간: 17:00-01:00 KST
        if dtime(17, 0) <= now or now < dtime(1, 0):
            return True
        return False
    except:
        return False


def is_market_hours(trading_timezone='US/Eastern'):
    """✅ 시장 상태 반환 (US Eastern 기준 유지)"""
    try:
        # 내부 로직은 US/Eastern 기준 (피드백 반영)
        et = timezone(trading_timezone)
        now_et = datetime.now(et).time()
        
        # KST 변환은 표시용으로만 사용
        kst = timezone('Asia/Seoul')
        now_kst = datetime.now(kst).time()
        
        if dtime(1, 0) <= now_kst < dtime(17, 0):
            return 'closed'
        elif dtime(17, 0) <= now_kst < dtime(18, 0):
            return 'aggressive'  # 집중 매매
        elif dtime(21, 30) <= now_kst < dtime(23, 0):
            return 'aggressive'  # 집중 매매
        else:
            return 'smart'  # 스마트 폴링
    except:
        return 'smart'