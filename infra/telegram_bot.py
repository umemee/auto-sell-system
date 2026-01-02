import requests
import time
from config import Config
from utils import get_logger

logger = get_logger()

class TelegramBot:
    def __init__(self):
        self.token = Config.TELEGRAM_BOT_TOKEN
        self.chat_id = Config.TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.token}/sendMessage"

    def send_message(self, text):
        """텔레그램 메시지 전송"""
        if not self.token or not self.chat_id:
            logger.warning("텔레그램 설정이 없습니다. (메시지 전송 스킵)")
            return

        try:
            params = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML" # 보기 좋게 HTML 모드 사용
            }
            # 타임아웃을 짧게 줘서 매매 루프에 방해 안 되게 함
            requests.get(self.base_url, params=params, timeout=3)
        except Exception as e:
            logger.error(f"텔레그램 전송 실패: {e}")