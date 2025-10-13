import requests
import logging
import json
import asyncio
from datetime import datetime
import subprocess
import os
import signal

class TelegramBot:
    def __init__(self, bot_token, chat_id):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.logger = logging.getLogger(__name__)
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.is_running = False

    def send_message(self, message, parse_mode='HTML'):
        """메시지 전송"""
        try:
            url = f"{self.base_url}/sendMessage"
            data = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": parse_mode
            }
            
            response = requests.post(url, data=data, timeout=10)
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
🚀 <b>자동 매도 시스템 시작</b>

• 상태: 실행중
• 시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
• 감시 대상: 미국 주식 매수 체결
• 수익률 목표: +3%

시스템이 정상적으로 작동하고 있습니다.
        """
        return self.send_message(message.strip())

    def send_sell_order_notification(self, ticker, quantity, buy_price, sell_price, profit_rate):
        """매도 주문 알림"""
        message = f"""
📈 <b>자동 매도 주문 실행</b>

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
⚠️ <b>시스템 오류 발생</b>

오류 내용: {error_message}
발생 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

시스템 상태를 확인해 주세요.
        """
        return self.send_message(message.strip())

    def send_shutdown_notification(self):
        """시스템 종료 알림"""
        message = f"""
🛑 <b>자동 매도 시스템 종료</b>

• 종료 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
• 상태: 정상 종료

시스템이 안전하게 종료되었습니다.
        """
        return self.send_message(message.strip())

    def get_updates(self, offset=None):
        """업데이트 가져오기"""
        try:
            url = f"{self.base_url}/getUpdates"
            params = {}
            if offset:
                params['offset'] = offset
            
            response = requests.get(url, params=params, timeout=10)
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
🤖 <b>자동 매도 시스템 봇</b>

사용 가능한 명령어:
• /status - 시스템 상태 확인
• /stop - 시스템 종료
• /help - 도움말 보기

현재 시스템이 실행 중입니다.
                """
                
            elif command == '/status':
                # 시스템 상태 확인
                message = f"""
📊 <b>시스템 상태</b>

• 상태: ✅ 실행중
• 시작 시간: {datetime.now().strftime('%a %Y-%m-%d %H:%M:%S UTC')}
• 확인 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

시스템이 정상적으로 작동하고 있습니다.
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
                message = """
📚 <b>도움말</b>

<b>명령어 목록:</b>
• /start - 봇 시작 및 소개
• /status - 현재 시스템 상태 확인
• /stop - 시스템 안전 종료
• /help - 이 도움말 보기

<b>기능:</b>
• 미국 주식 매수 체결 실시간 감시
• 자동 +3% 매도 주문 실행
• 실시간 알림 서비스

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
            }, timeout=10)
            
        except Exception as e:
            self.logger.error(f"명령어 처리 오류: {e}")

    def start_polling(self):
        """텔레그램 봇 폴링 시작 - 개선된 버전"""
        self.logger.info("텔레그램 봇 폴링을 시작합니다...")
        
        # 시작 시 메시지 큐 정리
        self.clear_message_queue()
        
        self.is_running = True
        offset = None
        start_time = datetime.now().timestamp()
        
        while self.is_running:
            try:
                updates = self.get_updates(offset)
                
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
                
                # 폴링 간격
                if self.is_running:
                    import time
                    time.sleep(2)  # 1초에서 2초로 증가
                    
            except Exception as e:
                self.logger.error(f"폴링 오류: {e}")
                if self.is_running:
                    import time
                    time.sleep(5)  # 오류 발생 시 5초 대기

    def stop_polling(self):
        """폴링 중지"""
        self.logger.info("텔레그램 봇 폴링을 중지합니다...")
        self.is_running = False