import requests
import time
import threading
import json
from datetime import datetime
from config import Config
from infra.utils import get_logger

logger = get_logger()

class TelegramBot:
    def __init__(self):
        self.token = Config.TELEGRAM_BOT_TOKEN
        self.chat_id = Config.TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        
        self.last_update_id = 0
        self.is_running = False
        
        # [UI] 상태 정보를 제공해줄 함수 (main.py에서 주입)
        self.status_provider = None
        
        self.command_handlers = {
            '/status': self._cmd_status,
            '/help': self._cmd_help,
            '/stop': self._cmd_stop
        }

    def set_status_provider(self, provider_func):
        """main.py의 상태를 조회할 수 있는 함수 연결"""
        self.status_provider = provider_func

    def start(self):
        """봇 폴링 시작 (별도 스레드)"""
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
            requests.get(url, params=params, timeout=15)
        except Exception as e:
            logger.error(f"Telegram Send Error: {e}")

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

    # === [Commands] ===
    def _cmd_status(self):
        """/status: 현재 시스템 상태 조회 (밴 리스트 추가됨)"""
        if not self.status_provider:
            self.send_message("⚠️ 시스템 연결 대기 중...")
            return

        data = self.status_provider()
        
        # 1. 포지션 정보 처리
        positions = data.get('positions', {})
        pos_msg = ""
        if not positions:
            pos_msg = "없음 (Empty Slot)"
        else:
            for ticker, p in positions.items():
                pnl = p.get('pnl_pct', 0.0)
                icon = "🔴" if pnl < 0 else "🟢"
                pos_msg += (
                    f"\n   📦 <b>{ticker}</b> {p['qty']}주"
                    f"\n      수익률: {icon} {pnl:.2f}%"
                    f"\n      평가액: ${p['eval_value']:.2f}\n"
                )

        # 2. 타겟 리스트
        targets = data.get('targets', [])
        target_str = ", ".join(targets) if targets else "없음"
        
        # 3. [추가] 밴 리스트 (금일 매매 금지)
        ban_list = data.get('ban_list', [])
        ban_str = ", ".join(ban_list) if ban_list else "없음"

        msg = (
            f"📊 <b>[GapZone Dashboard v5]</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💰 <b>총 자산:</b> ${data['total_equity']:,.2f}\n"
            f"💵 <b>현금:</b> ${data['cash']:,.2f}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🔭 <b>감시 중:</b>\n"
            f"👉 {target_str}\n\n"
            f"🚫 <b>매매 금지(Ban):</b>\n"
            f"👉 {ban_str}\n\n"
            f"🎣 <b>보유 포지션 ({len(positions)}):</b>{pos_msg}\n"
            f"⏰ <b>Update:</b> {datetime.now().strftime('%H:%M:%S')}"
        )
        self.send_message(msg)

    def _cmd_help(self):
        msg = (
            "🤖 <b>GapZone Bot Commands</b>\n\n"
            "/status - 대시보드 (잔고, 포지션, 감시종목)\n"
            "/stop - ⛔ 시스템 긴급 종료\n"
            "/help - 도움말"
        )
        self.send_message(msg)

    def _cmd_stop(self):
        self.send_message("⛔ <b>시스템 종료 요청됨!</b>\n안전하게 종료 절차를 밟습니다.")