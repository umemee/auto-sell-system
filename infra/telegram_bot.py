# infra/telegram_bot.py - v3.1 Interactive
import requests
import time
import threading
import json
from datetime import datetime
from config import Config
from infra.utils import get_logger

logger = get_logger()

class TelegramBot:
    def __init__(self, state_manager=None):
        self.token = Config.TELEGRAM_BOT_TOKEN
        self.chat_id = Config.TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.state_manager = state_manager
        self.last_update_id = 0
        self.is_running = False
        
        # [V1 Feature] 명령어 핸들러 등록
        self.command_handlers = {
            '/status': self._cmd_status,
            '/help': self._cmd_help,
            # '/buy': self._cmd_buy # (위험하므로 필요시 주석 해제하여 구현)
        }

    def start(self):
        """[V1 Feature] 봇 폴링 시작 (별도 스레드)"""
        if not self.token: return
        self.is_running = True
        self.thread = threading.Thread(target=self._polling_loop, daemon=True)
        self.thread.start()
        logger.info("🤖 Interactive Telegram Bot Started")

    def stop(self):
        self.is_running = False

    def send_message(self, text):
        """기본 메시지 전송"""
        if not self.token or not self.chat_id: return
        try:
            url = f"{self.base_url}/sendMessage"
            params = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}
            requests.get(url, params=params, timeout=5)
        except Exception as e:
            logger.error(f"Telegram Send Error: {e}")

    def send_rich_notification(self, type, data):
        """[V2 Feature] 상세 리포트 전송"""
        if type == "BUY":
            emoji = "🚀"
            color_title = "<b>[매수 체결 알림]</b>"
        elif type == "SELL":
            emoji = "💰"
            color_title = "<b>[익절/손절 알림]</b>"
        else:
            emoji = "🔔"
            color_title = f"<b>[{type}]</b>"

        # 수익금 표시 로직
        pnl_str = ""
        if "pnl" in data:
            pnl = data['pnl']
            pnl_icon = "🔴" if pnl < 0 else "🟢"
            pnl_str = f"\n{pnl_icon} 수익률: <b>{pnl:.2f}%</b>"

        msg = (
            f"{emoji} {color_title}\n"
            f"━━━━━━━━━━━━━━\n"
            f"📦 종목: <b>{data.get('symbol')}</b>\n"
            f"🔢 수량: {data.get('qty')}주\n"
            f"💵 가격: ${data.get('price')}\n"
            f"{pnl_str}"
            f"🆔 주문: {data.get('order_no')}\n"
            f"⏰ 시간: {datetime.now().strftime('%H:%M:%S')}"
        )
        self.send_message(msg)

    def _polling_loop(self):
        """텔레그램 서버에서 메시지 수신 (Long Polling)"""
        while self.is_running:
            try:
                url = f"{self.base_url}/getUpdates"
                params = {"offset": self.last_update_id + 1, "timeout": 30}
                res = requests.get(url, params=params, timeout=40)
                data = res.json()
                
                if data.get("ok"):
                    for update in data.get("result", []):
                        self.last_update_id = update["update_id"]
                        self._handle_update(update)
            except Exception as e:
                # logger.error(f"Polling Error: {e}")
                time.sleep(5)
            time.sleep(1)

    def _handle_update(self, update):
        """수신된 메시지 처리"""
        msg = update.get("message", {})
        text = msg.get("text", "")
        chat_id = str(msg.get("chat", {}).get("id"))

        # 내 채팅방 메시지만 처리
        if chat_id != self.chat_id: return

        if text.startswith("/"):
            cmd = text.split()[0]
            if cmd in self.command_handlers:
                self.command_handlers[cmd]()
            else:
                self.send_message(f"❌ 알 수 없는 명령어: {cmd}")

    def _cmd_status(self):
        if self.state_manager:
            state = self.state_manager.get_state().name
            self.send_message(f"📊 현재 상태: <b>{state}</b>")
        else:
            self.send_message("⚠️ 상태 매니저가 연결되지 않았습니다.")

    def _cmd_help(self):
        msg = (
            "🤖 <b>Bot Commands</b>\n"
            "/status - 시스템 상태 확인\n"
            "/stop - (미구현) 시스템 정지\n"
            "/start - (미구현) 시스템 시작"
        )
        self.send_message(msg)