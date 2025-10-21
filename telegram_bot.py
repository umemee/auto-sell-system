# telegram_bot.py - 기획서 v1.0 완전 준수 버전

import requests
import logging
import json
import time
import os
import signal
import threading
from datetime import datetime, timedelta
from collections import defaultdict

class TelegramBot:
    """
    텔레그램 봇 (기획서 v1.0 완전 준수)
    
    주요 기능:
    - 시스템 시작/종료 알림 (기획서 6.1절)
    - 매수/매도 알림 (기획서 6.1절)
    - Rate Limit 경고 (기획서 6.1절)
    - 오류 알림 (기획서 6.1절)
    - 일일 통계 요약 (기획서 6.1절)
    - 알림 중복 방지
    """
    
    def __init__(self, bot_token, chat_id, config=None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.logger = logging.getLogger(__name__)
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.is_running = False
        self.polling_thread = None
        
        # 설정
        if config and 'telegram' in config:
            self.polling_interval = config['telegram'].get('polling_interval', 10)
            self.error_polling_interval = config['telegram'].get('error_polling_interval', 30)
            self.timeout = config['telegram'].get('timeout', 10)
        else:
            # 기본값 - AWS 비용 절약
            self.polling_interval = 10
            self.error_polling_interval = 30
            self.timeout = 10
        
        # ✅ 추가: 알림 중복 방지 (기획서 6.1절)
        self.last_alert_time = {}  # 알림 타입별 마지막 전송 시간
        self.alert_cooldown = 300  # 5분 내 같은 알림 중복 방지
        
        # ✅ 추가: 통계 추적
        self.stats = {
            'total_buys': 0,
            'total_sells': 0,
            'successful_sells': 0,
            'failed_sells': 0,
            'total_profit': 0.0,
            'errors': 0,
            'start_time': datetime.now()
        }

    def _should_send_alert(self, alert_type):
        """
        ✅ 추가: 알림 중복 방지 체크 (기획서 6.1절)
        
        같은 타입의 알림을 5분 이내에 다시 보내지 않음
        """
        current_time = time.time()
        last_time = self.last_alert_time.get(alert_type, 0)
        
        if current_time - last_time < self.alert_cooldown:
            self.logger.debug(f"알림 중복 방지: {alert_type} (마지막 전송: {current_time - last_time:.0f}초 전)")
            return False
        
        self.last_alert_time[alert_type] = current_time
        return True

    def send_message(self, message, parse_mode='HTML', force=False, alert_type=None):
        """
        메시지 전송 (알림 중복 방지 포함)
        
        Parameters:
            message: 전송할 메시지
            parse_mode: HTML 또는 Markdown
            force: True시 중복 방지 무시
            alert_type: 알림 타입 (중복 방지용)
        """
        try:
            # 알림 중복 방지 체크
            if not force and alert_type and not self._should_send_alert(alert_type):
                return False
            
            url = f"{self.base_url}/sendMessage"
            data = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": parse_mode
            }

            response = requests.post(url, data=data, timeout=self.timeout)
            if response.status_code == 200:
                self.logger.debug("Telegram 메시지 전송 성공")
                return True
            else:
                self.logger.error(f"Telegram 메시지 전송 실패: {response.text}")
                return False
        except Exception as e:
            self.logger.error(f"Telegram 메시지 전송 중 오류: {e}")
            return False

    def send_startup_notification(self):
        """✅ 시스템 시작 알림 (기획서 6.1절)"""
        message = f"""
🚀 <b>자동 매도 시스템 시작</b>

• 상태: ✅ 실행중
• 시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
• 감시 대상: 미국 주식 매수 체결
• 수익률 목표: +3% (기획서 4.1절)
• 폴링 간격: {self.polling_interval}초

<b>기획서 v1.0 준수 시스템</b>
✅ Rate Limit 안전 모드
✅ 적응형 폴링
✅ WebSocket 자동 전환

시스템이 정상적으로 작동하고 있습니다.
"""
        return self.send_message(message.strip(), force=True)

    def send_buy_detection_notification(self, ticker, quantity, price, source='websocket'):
        """
        ✅ 추가: 매수 감지 알림 (기획서 6.1절)
        
        Parameters:
            ticker: 종목 코드
            quantity: 수량
            price: 매수가
            source: 감지 소스 (websocket/polling)
        """
        self.stats['total_buys'] += 1
        
        source_emoji = "⚡" if source == 'websocket' else "🔍"
        source_text = "WebSocket (정규장)" if source == 'websocket' else "REST 폴링 (장외)"
        
        message = f"""
{source_emoji} <b>매수 체결 감지</b>

• 종목: <b>{ticker}</b>
• 수량: {quantity:,}주
• 매수가: ${price:.2f}
• 감지: {source_text}
• 시각: {datetime.now().strftime('%H:%M:%S')}

🎯 목표 수익률 +3% 도달 시 자동 매도 예정
💰 목표가: ${price * 1.03:.2f}
"""
        return self.send_message(message.strip(), alert_type=f"buy_{ticker}")

    def send_sell_order_notification(self, ticker, quantity, buy_price, sell_price, profit_rate):
        """✅ 매도 주문 알림 (기획서 6.1절)"""
        self.stats['total_sells'] += 1
        self.stats['successful_sells'] += 1
        profit_amount = (sell_price - buy_price) * quantity
        self.stats['total_profit'] += profit_amount
        
        message = f"""
📈 <b>자동 매도 주문 실행</b>

• 종목: <b>{ticker}</b>
• 수량: {quantity:,}주
• 매수가: ${buy_price:.2f}
• 매도가: ${sell_price:.2f}
• 수익률: <b>+{profit_rate:.1f}%</b>
• 수익금: <b>${profit_amount:.2f}</b>
• 시각: {datetime.now().strftime('%H:%M:%S')}

✅ 매도 주문이 성공적으로 실행되었습니다.
"""
        return self.send_message(message.strip(), force=True)

    def send_sell_failure_notification(self, ticker, quantity, reason):
        """✅ 추가: 매도 실패 알림 (기획서 6.1절)"""
        self.stats['failed_sells'] += 1
        
        message = f"""
⚠️ <b>매도 주문 실패</b>

• 종목: <b>{ticker}</b>
• 수량: {quantity:,}주
• 실패 사유: {reason}
• 시각: {datetime.now().strftime('%H:%M:%S')}

⚠️ 수동 확인이 필요합니다.
"""
        return self.send_message(message.strip(), alert_type=f"sell_fail_{ticker}")

    def send_rate_limit_warning(self, current_usage, limit, utilization_pct):
        """
        ✅ 추가: Rate Limit 경고 알림 (기획서 5.2절, 6.1절)
        
        90% 도달 시 경고
        """
        if utilization_pct < 90:
            return False
        
        message = f"""
⚠️ <b>Rate Limit 경고</b>

• 현재 사용량: {current_usage:,}회
• 일일 한도: {limit:,}회
• 사용률: <b>{utilization_pct:.1f}%</b>
• 시각: {datetime.now().strftime('%H:%M:%S')}

<b>기획서 5.2절: Rate Limit 90% 도달</b>

⚠️ API 호출을 자제하고 있습니다.
남은 한도를 효율적으로 사용합니다.
"""
        return self.send_message(message.strip(), alert_type="rate_limit_warning")

    def send_consecutive_errors_alert(self, error_count, error_type):
        """
        ✅ 추가: 연속 오류 알림 (기획서 5.2절, 6.1절)
        
        연속 10회 API 오류 시 알림
        """
        if error_count < 10:
            return False
        
        self.stats['errors'] += 1
        
        message = f"""
🚨 <b>연속 API 오류 감지</b>

• 오류 타입: {error_type}
• 연속 횟수: {error_count}회
• 시각: {datetime.now().strftime('%H:%M:%S')}

<b>기획서 5.2절: 연속 10회 API 오류</b>

⚠️ 시스템 상태를 점검하고 있습니다.
"""
        return self.send_message(message.strip(), alert_type=f"consecutive_errors_{error_type}")

    def send_websocket_failure_alert(self, attempt, max_attempts, is_regular_market):
        """
        ✅ 추가: WebSocket 실패 알림 (기획서 5.2절, 6.1절)
        
        정규장에서 3회 실패 시 시스템 종지 경고
        """
        if is_regular_market and attempt >= max_attempts:
            message = f"""
🚨 <b>긴급: WebSocket 연결 실패</b>

• 시도 횟수: {attempt}/{max_attempts}
• 시장 상태: 정규장 (ET 09:30-12:00)
• 시각: {datetime.now().strftime('%H:%M:%S')}

<b>기획서 5.2절: WebSocket 3회 실패</b>

🛑 정규장에서 WebSocket 연결 불가
⚠️ 시스템이 곧 자동 종지됩니다
"""
            return self.send_message(message.strip(), force=True)
        else:
            message = f"""
⚠️ <b>WebSocket 재연결 시도</b>

• 시도: {attempt}/{max_attempts}
• 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

연결을 복구하려고 시도하고 있습니다.
"""
            return self.send_message(message.strip(), alert_type="websocket_reconnect")

    def send_account_query_failure_alert(self, error_message):
        """✅ 추가: 계좌 정보 조회 실패 알림 (기획서 5.2절, 6.1절)"""
        message = f"""
⚠️ <b>계좌 정보 조회 실패</b>

• 오류 내용: {error_message}
• 시각: {datetime.now().strftime('%H:%M:%S')}

<b>기획서 5.2절: 계좌 정보 조회 실패</b>

⚠️ 실시간 잔고 정보를 확인할 수 없습니다.
수동으로 계좌를 확인해주세요.
"""
        return self.send_message(message.strip(), alert_type="account_query_fail")

    def send_error_notification(self, error_message, level="warning"):
        """
        ✅ 개선: 오류 알림 (레벨 추가)
        
        Parameters:
            error_message: 오류 메시지
            level: info/warning/critical
        """
        emoji_map = {
            'info': 'ℹ️',
            'warning': '⚠️',
            'critical': '🚨'
        }
        
        emoji = emoji_map.get(level, '⚠️')
        
        message = f"""
{emoji} <b>시스템 {level.upper()}</b>

오류 내용: {error_message}
발생 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

시스템 상태를 확인해 주세요.
"""
        return self.send_message(message.strip(), alert_type=f"error_{level}")

    def send_info_notification(self, message):
            """
            ℹ️ 정보성 알림 전송
        
            Parameters:
                message: 전송할 메시지
            """
            return self.send_error_notification(message, level="info")

    def send_daily_summary(self):
        """
        ✅ 추가: 일일 통계 요약 (기획서 6.1절)
        
        매일 01:00 (시스템 종료 시)에 전송
        """
        runtime = datetime.now() - self.stats['start_time']
        runtime_hours = runtime.total_seconds() / 3600
        
        success_rate = 0
        if self.stats['total_sells'] > 0:
            success_rate = (self.stats['successful_sells'] / self.stats['total_sells']) * 100
        
        message = f"""
📊 <b>일일 통계 요약</b>

<b>📈 거래 현황</b>
• 매수 감지: {self.stats['total_buys']}건
• 매도 시도: {self.stats['total_sells']}건
• 매도 성공: {self.stats['successful_sells']}건
• 매도 실패: {self.stats['failed_sells']}건
• 성공률: {success_rate:.1f}%

<b>💰 수익 현황</b>
• 총 수익: ${self.stats['total_profit']:.2f}

<b>⚙️ 시스템</b>
• 가동 시간: {runtime_hours:.1f}시간
• 오류 횟수: {self.stats['errors']}회

<b>기획서 v1.0 준수 시스템</b>
✅ 일일 통계 요약 (기획서 6.1절)
"""
        return self.send_message(message.strip(), force=True)

    def send_shutdown_notification(self):
        """✅ 시스템 종료 알림 (기획서 6.1절)"""
        # 종료 전 일일 통계 전송
        self.send_daily_summary()
        
        time.sleep(1)  # 통계 메시지 전송 후 1초 대기
        
        message = f"""
🛑 <b>자동 매도 시스템 종료</b>

• 종료 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
• 상태: 정상 종료

시스템이 안전하게 종료되었습니다.
"""
        return self.send_message(message.strip(), force=True)

    def send_emergency_stop_notification(self, reason):
        """
        ✅ 추가: 긴급 종지 알림 (기획서 5.2절, 6.1절)
        
        비상 정지 시 전송
        """
        message = f"""
🚨 <b>긴급 시스템 종지</b>

• 종지 사유: <b>{reason}</b>
• 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

<b>기획서 5.2절: 비상 증지 조건 충족</b>

⚠️ 시스템이 안전하게 종료됩니다.
수동 점검이 필요합니다.
"""
        return self.send_message(message.strip(), force=True)

    def get_updates(self, offset=None):
        """업데이트 가져오기"""
        try:
            url = f"{self.base_url}/getUpdates"
            params = {}
            if offset:
                params['offset'] = offset

            response = requests.get(url, params=params, timeout=self.timeout)
            if response.status_code == 200:
                data = response.json()
                if data['ok']:
                    return data['result']
            return []
        except Exception as e:
            self.logger.error(f"업데이트 가져오기 오류: {e}")
            return []

    def clear_message_queue(self, start_time):
        """
        메시지 큐 정리 - 시작 시각 이전 메시지만 무시
        
        Args:
            start_time: 시스템 시작 시각 (timestamp)
        """
        try:
            updates = self.get_updates()
            if not updates:
                return True
            
            old_messages = []
            latest_offset = None
            
            for update in updates:
                message = update.get('message', {})
                message_date = message.get('date', 0)
                
                # 시작 시각 이전 메시지는 무시
                if message_date < start_time:
                    old_messages.append(update['update_id'])
                    latest_offset = update['update_id']
            
            # 오래된 메시지만 읽음 처리
            if latest_offset:
                self.get_updates(offset=latest_offset + 1)
                self.logger.info(f"텔레그램 오래된 메시지 정리: {len(old_messages)}개 무시됨")
            else:
                self.logger.info("텔레그램 큐에 오래된 메시지 없음")
            
            return True
        except Exception as e:
            self.logger.error(f"메시지 큐 정리 오류: {e}")
            return False

    def handle_command(self, command, chat_id, message_date):
        """명령어 처리 - 메시지 시간 검증 추가"""
        try:
            # 메시지가 5분 이상 된 것은 무시 (시스템 시작 전 메시지)
            current_time = datetime.now().timestamp()
            if current_time - message_date > 300:  # 5분 = 300초
                self.logger.info(f"오래된 명령어 무시: {command} (나이: {current_time - message_date:.0f}초)")
                return

            if command == '/start':
                message = """
🤖 <b>자동 매도 시스템 봇</b>

<b>사용 가능한 명령어:</b>
• /status - 시스템 상태 확인
• /stats - 실시간 통계
• /stop - 시스템 종료
• /help - 도움말 보기

현재 시스템이 실행 중입니다.
"""
            elif command == '/status':
                runtime = datetime.now() - self.stats['start_time']
                runtime_hours = runtime.total_seconds() / 3600
                
                message = f"""
📊 <b>시스템 상태</b>

• 상태: ✅ 실행중
• 가동 시간: {runtime_hours:.1f}시간
• 확인 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
• 폴링 간격: {self.polling_interval}초

시스템이 정상적으로 작동하고 있습니다.
"""
            elif command == '/stats':
                success_rate = 0
                if self.stats['total_sells'] > 0:
                    success_rate = (self.stats['successful_sells'] / self.stats['total_sells']) * 100
                
                message = f"""
📈 <b>실시간 통계</b>

<b>거래</b>
• 매수 감지: {self.stats['total_buys']}건
• 매도 시도: {self.stats['total_sells']}건
• 매도 성공: {self.stats['successful_sells']}건
• 성공률: {success_rate:.1f}%

<b>수익</b>
• 총 수익: ${self.stats['total_profit']:.2f}

<b>시스템</b>
• 오류: {self.stats['errors']}회
"""
            elif command == '/stop':
                message = """
🛑 <b>시스템 종료 요청</b>

시스템 종료를 시작합니다...
잠시 후 시스템이 안전하게 종료됩니다.
"""
                # 메시지 전송 후 시스템 종료
                self.send_message(message.strip())
                # 안전한 시스템 종료
                self.logger.info("텔레그램에서 시스템 종료 요청을 받았습니다.")
                os.kill(os.getpid(), signal.SIGTERM)
                return

            elif command == '/help':
                message = f"""
📚 <b>도움말</b>

<b>명령어 목록:</b>
• /start - 봇 시작 및 소개
• /status - 현재 시스템 상태 확인
• /stats - 실시간 거래 통계
• /stop - 시스템 안전 종료
• /help - 이 도움말 보기

<b>기능:</b>
• 미국 주식 매수 체결 실시간 감시
• 자동 +3% 매도 주문 실행
• 실시간 알림 서비스

<b>기획서 v1.0 준수:</b>
• Rate Limit 안전 모드
• 적응형 폴링
• WebSocket 자동 전환
• 비상 정지 메커니즘

문의사항이 있으시면 관리자에게 연락하세요.
"""
            else:
                message = f"""
❓ <b>알 수 없는 명령어: {command}</b>

/help 명령어로 사용 가능한 명령어를 확인하세요.
"""

            # 응답 전송
            requests.post(f"{self.base_url}/sendMessage", data={
                "chat_id": chat_id,
                "text": message.strip(),
                "parse_mode": "HTML"
            }, timeout=self.timeout)

        except Exception as e:
            self.logger.error(f"명령어 처리 오류: {e}")

    def start_polling(self):
        """텔레그램 봇 폴링 시작"""
        self.logger.info(f"텔레그램 봇 폴링을 시작합니다... (간격: {self.polling_interval}초)")
        
        # 시작 시각을 먼저 기록
        start_time = datetime.now().timestamp()
        
        # 시작 시각을 전달하여 메시지 큐 정리
        self.clear_message_queue(start_time)
        self.is_running = True
        
        offset = None
        consecutive_errors = 0
        max_consecutive_errors = 5

        while self.is_running:
            try:
                updates = self.get_updates(offset)
                
                # 연속 오류 카운터 리셋
                consecutive_errors = 0

                for update in updates:
                    try:
                        if 'message' in update:
                            message = update['message']
                            chat_id = message['chat']['id']
                            message_date = message['date']

                            # 권한 확인
                            if str(chat_id) != str(self.chat_id):
                                self.logger.warning(f"권한이 없는 사용자의 메시지 무시: {chat_id}")
                                continue

                            # 시스템 시작 이전 메시지는 무시
                            if message_date < start_time:
                                self.logger.debug(f"시스템 시작 이전 메시지 무시: {message.get('text', '')}")
                                continue

                            if 'text' in message:
                                text = message['text'].strip()
                                self.logger.info(f"텔레그램 명령어 수신: {text}")
                                if text.startswith('/'):
                                    self.handle_command(text, chat_id, message_date)

                        offset = update['update_id'] + 1

                    except Exception as e:
                        self.logger.error(f"업데이트 처리 오류: {e}")
                        continue

                # 폴링 간격
                if self.is_running:
                    time.sleep(self.polling_interval)

            except Exception as e:
                consecutive_errors += 1
                self.logger.error(f"폴링 오류 ({consecutive_errors}/{max_consecutive_errors}): {e}")
                
                # 연속 오류가 너무 많으면 더 긴 대기
                if consecutive_errors >= max_consecutive_errors:
                    self.logger.warning("연속 오류 한계 도달. 긴 대기 시간 적용.")
                    wait_time = self.error_polling_interval * 2
                else:
                    wait_time = self.error_polling_interval

                if self.is_running:
                    time.sleep(wait_time)

        self.logger.info("텔레그램 봇 폴링이 종료되었습니다.")

    def start(self):
        """봇 시작 (메인에서 호출할 메서드)"""
        if self.is_running:
            self.logger.warning("텔레그램 봇이 이미 실행 중입니다.")
            return
            
        self.logger.info("텔레그램 봇을 시작합니다...")
        self.polling_thread = threading.Thread(target=self.start_polling, daemon=True)
        self.polling_thread.start()
        
        # 시작 알림 전송
        self.send_startup_notification()

    def stop(self):
        """봇 중지 (메인에서 호출할 메서드)"""
        if not self.is_running:
            return
            
        self.logger.info("텔레그램 봇을 중지합니다...")
        self.is_running = False
        
        # 종료 알림 전송 (일일 통계 포함)
        self.send_shutdown_notification()
        
        # 폴링 스레드 종료 대기
        if self.polling_thread and self.polling_thread.is_alive():
            self.polling_thread.join(timeout=5)

    def stop_polling(self):
        """폴링 중지 (호환성을 위해 유지)"""
        self.logger.info("텔레그램 봇 폴링을 중지합니다...")
        self.is_running = False