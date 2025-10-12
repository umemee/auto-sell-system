import requests
import logging
import json
import asyncio
from datetime import datetime
import subprocess
import os

class TelegramBot:
    def __init__(self, bot_token, chat_id):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.logger = logging.getLogger(__name__)
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        
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
        message = """
🚀 <b>자동 매도 시스템 시작</b>

• 상태: 실행중
• 시작 시간: {}
• 감시 대상: 미국 주식 매수 체결
• 수익률 목표: +3%

시스템이 정상적으로 작동하고 있습니다.
        """.format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        
        return self.send_message(message)
    
    def send_buy_notification(self, ticker, quantity, price):
        """매수 감지 알림"""
        message = f"""
🚨 <b>매수 체결 감지!</b>

• 종목: <code>{ticker}</code>
• 수량: {quantity}주
• 체결가: ${price:.2f}
• 감지 시간: {datetime.now().strftime("%H:%M:%S")}

+3% 자동 매도 주문을 진행합니다...
        """
        
        return self.send_message(message)
    
    def send_sell_notification(self, ticker, quantity, sell_price, success=True):
        """매도 주문 결과 알림"""
        if success:
            message = f"""
✅ <b>자동 매도 주문 성공!</b>

• 종목: <code>{ticker}</code>
• 수량: {quantity}주
• 매도가: ${sell_price:.2f}
• 주문 시간: {datetime.now().strftime("%H:%M:%S")}

주문이 정상적으로 접수되었습니다.
            """
        else:
            message = f"""
❌ <b>자동 매도 주문 실패</b>

• 종목: <code>{ticker}</code>
• 수량: {quantity}주
• 시도한 매도가: ${sell_price:.2f}

주문 실패 원인을 로그에서 확인하세요.
            """
        
        return self.send_message(message)
    
    def send_error_notification(self, error_message):
        """오류 알림"""
        message = f"""
⚠️ <b>시스템 오류 발생</b>

오류 내용: {error_message}
발생 시간: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

시스템 상태를 확인해 주세요.
        """
        
        return self.send_message(message)
    
    def get_updates(self, offset=None):
        """메시지 업데이트 가져오기"""
        try:
            url = f"{self.base_url}/getUpdates"
            params = {"timeout": 10}
            if offset:
                params["offset"] = offset
                
            response = requests.get(url, params=params, timeout=15)
            
            if response.status_code == 200:
                return response.json().get("result", [])
            else:
                self.logger.error(f"업데이트 가져오기 실패: {response.text}")
                return []
                
        except Exception as e:
            self.logger.error(f"업데이트 가져오기 중 오류: {e}")
            return []
    
    def handle_command(self, command):
        """명령어 처리"""
        command = command.lower().strip()
        
        if command == "/start":
            return self.handle_start_command()
        elif command == "/status":
            return self.handle_status_command()
        elif command == "/logs":
            return self.handle_logs_command()
        elif command == "/stop":
            return self.handle_stop_command()
        elif command == "/restart":
            return self.handle_restart_command()
        else:
            return self.handle_help_command()
    
    def handle_start_command(self):
        """시작 명령어 처리"""
        message = """
🤖 <b>자동 매도 시스템 봇</b>

사용 가능한 명령어:
• /status - 현재 상태 확인
• /logs - 최근 로그 보기
• /stop - 시스템 중지
• /restart - 시스템 재시작

시스템이 정상 작동 중입니다.
        """
        return self.send_message(message)
    
    def handle_status_command(self):
        """상태 확인 명령어"""
        try:
            # systemctl로 서비스 상태 확인
            result = subprocess.run(['systemctl', 'is-active', 'auto-sell.service'], 
                                  capture_output=True, text=True)
            
            if result.stdout.strip() == "active":
                status = "✅ 실행중"
                
                # 업타임 확인
                uptime_result = subprocess.run(['systemctl', 'show', 'auto-sell.service', 
                                              '--property=ActiveEnterTimestamp'], 
                                             capture_output=True, text=True)
                uptime_info = uptime_result.stdout.strip().split('=')[1] if '=' in uptime_result.stdout else "Unknown"
                
                message = f"""
📊 <b>시스템 상태</b>

• 상태: {status}
• 시작 시간: {uptime_info}
• 확인 시간: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

시스템이 정상적으로 작동하고 있습니다.
                """
            else:
                message = """
❌ <b>시스템 중지됨</b>

시스템이 현재 실행되지 않고 있습니다.
/restart 명령으로 재시작할 수 있습니다.
                """
            
            return self.send_message(message)
            
        except Exception as e:
            return self.send_message(f"상태 확인 중 오류 발생: {e}")
    
    def handle_logs_command(self):
        """로그 확인 명령어"""
        try:
            # 최근 로그 20줄 가져오기
            result = subprocess.run(['tail', '-20', '/home/ubuntu/auto-sell-system/trading.log'], 
                                  capture_output=True, text=True)
            
            if result.stdout:
                logs = result.stdout[-3000:]  # Telegram 메시지 길이 제한
                message = f"📋 <b>최근 로그</b>\n\n<pre>{logs}</pre>"
            else:
                message = "로그 파일을 찾을 수 없습니다."
                
            return self.send_message(message)
            
        except Exception as e:
            return self.send_message(f"로그 확인 중 오류 발생: {e}")
    
    def handle_stop_command(self):
        """시스템 중지 명령어"""
        try:
            result = subprocess.run(['sudo', 'systemctl', 'stop', 'auto-sell.service'], 
                                  capture_output=True, text=True)
            
            if result.returncode == 0:
                message = "⛔ 시스템이 중지되었습니다."
            else:
                message = f"시스템 중지 실패: {result.stderr}"
                
            return self.send_message(message)
            
        except Exception as e:
            return self.send_message(f"시스템 중지 중 오류 발생: {e}")
    
    def handle_restart_command(self):
        """시스템 재시작 명령어"""
        try:
            result = subprocess.run(['sudo', 'systemctl', 'restart', 'auto-sell.service'], 
                                  capture_output=True, text=True)
            
            if result.returncode == 0:
                message = "🔄 시스템이 재시작되었습니다."
            else:
                message = f"시스템 재시작 실패: {result.stderr}"
                
            return self.send_message(message)
            
        except Exception as e:
            return self.send_message(f"시스템 재시작 중 오류 발생: {e}")
    
    def handle_help_command(self):
        """도움말 명령어"""
        message = """
❓ <b>사용 가능한 명령어</b>

• /status - 현재 상태 확인
• /logs - 최근 로그 보기 (20줄)
• /stop - 시스템 중지
• /restart - 시스템 재시작

더 자세한 제어는 웹 인터페이스를 이용하세요.
        """
        return self.send_message(message)
    
    def start_polling(self):
        """봇 폴링 시작 (명령어 수신 대기)"""
        self.logger.info("Telegram 봇 폴링 시작")
        offset = None
        
        while True:
            try:
                updates = self.get_updates(offset)
                
                for update in updates:
                    offset = update["update_id"] + 1
                    
                    if "message" in update:
                        message = update["message"]
                        if "text" in message and message["text"].startswith("/"):
                            self.handle_command(message["text"])
                
                # 1초 대기
                asyncio.sleep(1)
                
            except KeyboardInterrupt:
                self.logger.info("Telegram 봇 폴링 중지")
                break
            except Exception as e:
                self.logger.error(f"Telegram 봇 폴링 중 오류: {e}")
                asyncio.sleep(5)
