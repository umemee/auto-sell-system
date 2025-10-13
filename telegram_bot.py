# telegram_bot.py - AWS 최적화 및 수정된 전체 코드

import requests
import logging
import json
import time
import os
import signal
from datetime import datetime

class TelegramBot:
    def __init__(self, bot_token, chat_id, config=None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.logger = logging.getLogger(__name__)
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.is_running = False
        
        # AWS 최적화: config에서 폴링 설정 가져오기
        if config and 'telegram' in config:
            self.polling_interval = config['telegram'].get('polling_interval', 10)
            self.error_polling_interval = config['telegram'].get('error_polling_interval', 30)
            self.timeout = config['telegram'].get('timeout', 10)
        else:
            # 기본값 - AWS 비용 절약을 위한 설정
            self.polling_interval = 10  # 2초 → 10초로 증가
            self.error_polling_interval = 30
            self.timeout = 10

    def send_message(self, message, parse_mode='HTML'):
        """메시지 전송"""
        try:
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
        """시스템 시작 알림"""
        message = f"""
🚀 자동 매도 시스템 시작

• 상태: 실행중
• 시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
• 감시 대상: 미국 주식 매수 체결
• 수익률 목표: +3%
• 폴링 간격: {self.polling_interval}초 (AWS 최적화)

시스템이 정상적으로 작동하고 있습니다.
"""
        return self.send_message(message.strip())

    def send_sell_order_notification(self, ticker, quantity, buy_price, sell_price, profit_rate):
        """매도 주문 알림"""
        message = f"""
📈 자동 매도 주문 실행

• 종목: {ticker}
• 수량: {quantity:,}주
• 매수가: ${buy_price:.2f}
• 매도가: ${sell_price:.2f}
• 수익률: +{profit_rate:.1f}%

매도 주문이 성공적으로 실행되었습니다.
"""
        return self.send_message(message.strip())

    def send_error_notification(self, error_message):
        """오류 알림"""
        message = f"""
⚠️ 시스템 오류 발생

오류 내용: {error_message}
발생 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

시스템 상태를 확인해 주세요.
"""
        return self.send_message(message.strip())

    def send_shutdown_notification(self):
        """시스템 종료 알림"""
        message = f"""
🛑 자동 매도 시스템 종료

• 종료 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
• 상태: 정상 종료

시스템이 안전하게 종료되었습니다.
"""
        return self.send_message(message.strip())

    def send_reconnection_notification(self, attempt, max_attempts):
        """재연결 시도 알림"""
        message = f"""
🔄 WebSocket 재연결 시도

• 시도: {attempt}/{max_attempts}
• 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

연결을 복구하려고 시도하고 있습니다.
"""
        return self.send_message(message.strip())

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

    def clear_message_queue(self):
        """메시지 큐 정리 - 시작 시 이전 메시지들을 모두 무시"""
        try:
            updates = self.get_updates()
            if updates:
                latest_update_id = updates[-1]['update_id']
                # 모든 이전 메시지를 읽음 처리
                self.get_updates(offset=latest_update_id + 1)
                self.logger.info(f"텔레그램 메시지 큐 정리: {len(updates)}개 메시지 무시됨")
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
🤖 자동 매도 시스템 봇

사용 가능한 명령어:
• /status - 시스템 상태 확인
• /stop - 시스템 종료
• /help - 도움말 보기

현재 시스템이 실행 중입니다.
"""
            elif command == '/status':
                # 시스템 상태 확인
                message = f"""
📊 시스템 상태

• 상태: ✅ 실행중
• 확인 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
• 폴링 간격: {self.polling_interval}초
• AWS 최적화: 활성화

시스템이 정상적으로 작동하고 있습니다.
"""
            elif command == '/stop':
                message = """
🛑 시스템 종료 요청

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
📚 도움말

명령어 목록:
• /start - 봇 시작 및 소개
• /status - 현재 시스템 상태 확인
• /stop - 시스템 안전 종료
• /help - 이 도움말 보기

기능:
• 미국 주식 매수 체결 실시간 감시
• 자동 +3% 매도 주문 실행
• 실시간 알림 서비스

AWS 최적화:
• 폴링 간격: {self.polling_interval}초
• 네트워크 트래픽 최소화

문의사항이 있으시면 관리자에게 연락하세요.
"""
            else:
                message = f"""
❓ 알 수 없는 명령어: {command}

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
        """텔레그램 봇 폴링 시작 - AWS 최적화된 버전"""
        self.logger.info(f"텔레그램 봇 폴링을 시작합니다... (간격: {self.polling_interval}초)")

        # 시작 시 메시지 큐 정리
        self.clear_message_queue()
        self.is_running = True
        offset = None
        start_time = datetime.now().timestamp()
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

                            # 권한 확인 (설정된 chat_id와 일치하는 경우만 처리)
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

                # AWS 최적화: 설정 가능한 폴링 간격
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

    def stop_polling(self):
        """폴링 중지"""
        self.logger.info("텔레그램 봇 폴링을 중지합니다...")
        self.is_running = False

    def send_aws_optimization_report(self):
        """AWS 최적화 리포트 전송"""
        message = f"""
💰 AWS 비용 최적화 리포트

• 폴링 간격: {self.polling_interval}초
• 예상 월간 요청 수: {int(86400 * 30 / self.polling_interval):,}회
• 네트워크 효율성: 최적화됨
• 오류 시 대기: {self.error_polling_interval}초

비용 효율적으로 운영되고 있습니다.
"""
        return self.send_message(message.strip())