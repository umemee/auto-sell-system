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
            requests.get(url, params=params, timeout=5)
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
        """/status: 현재 시스템 상태 조회"""
        if not self.status_provider:
            self.send_message("⚠️ 시스템 연결 대기 중...")
            return

        # main.py에서 데이터 가져오기
        data = self.status_provider()
        
        # 포지션 정보 포맷팅
        pos_info = "없음 (스캐닝 중... 🔭)"
        if data['position']:
            p = data['position']
            curr_price = p.get('current_price', p['entry_price'])
            pnl_pct = ((curr_price - p['entry_price']) / p['entry_price']) * 100
            icon = "🔴" if pnl_pct < 0 else "🟢"
            pos_info = (
                f"\n   📦 <b>{p['symbol']}</b> {p['qty']}주"
                f"\n   평단: ${p['entry_price']}"
                f"\n   현재: ${curr_price} ({icon} {pnl_pct:.2f}%)"
            )

        # 타겟 리스트 포맷팅
        targets = data['targets']
        target_str = ", ".join(targets) if targets else "없음"

        # One-Shot 졸업생
        oneshot_list = list(data['oneshot'])
        oneshot_str = ", ".join(oneshot_list) if oneshot_list else "없음"

        msg = (
            f"📊 <b>[GapZone Dashboard]</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💰 <b>예수금:</b> ${data['cash']:,.2f}\n"
            f"📉 <b>금일 손실:</b> ${data['loss']:.2f} (Limit: ${data['loss_limit']})\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🔭 <b>감시 중 ({len(targets)}):</b>\n"
            f"👉 {target_str}\n\n"
            f"🎣 <b>현재 포지션:</b> {pos_info}\n\n"
            f"✅ <b>One-Shot 완료:</b> {oneshot_str}\n"
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