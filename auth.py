# auth.py - 한국투자증권 API 인증/토큰 관리 (자동 갱신 및 WebSocket Approval Key 포함)

import requests
import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class TokenManager:
    """
    한국투자증권 API용 TokenManager
    - REST 액세스 토큰 자동 갱신
    - WebSocket Approval Key 자동 갱신
    """

    def __init__(self, config, telegram_bot=None):
        self.config = config
        self.telegram_bot = telegram_bot
        self.access_token = None
        self.token_expires_at = None
        self.approval_key = None
        self.approval_expires_at = None

        # 토큰 만료 마진 (토큰 만료 전 재갱신)
        self.token_refresh_margin = config.get('system', {}).get('token_refresh_margin_minutes', 5)
        # Approval Key 만료 간격 (초)
        self.approval_margin_seconds = config.get('system', {}).get('approval_margin_seconds', 30 * 60)

    def _request_access_token(self):
        """
        REST API용 Access Token 요청
        """
        url = f"{self.config['api']['base_url']}/oauth2/tokenP"
        headers = {"Content-Type": "application/json"}
        body = {
            "grant_type": "client_credentials",
            "appkey": self.config['api_key'],
            "appsecret": self.config['api_secret']
        }
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            token = data.get("access_token")
            expires_in = data.get("expires_in", 86400)
            if token:
                self.access_token = token
                self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
                logger.info(f"✅ Access Token 발급 성공 (만료: {self.token_expires_at.strftime('%H:%M:%S')})")
                return token
            else:
                logger.error(f"❌ Access Token 응답 누락: {data}")
        except Exception as e:
            logger.error(f"❌ Access Token 요청 오류: {e}")
        return None

    def get_access_token(self, force_refresh=False):
        """
        유효한 Access Token 반환
        - 토큰 만료 5분 전이면 자동 갱신
        """
        now = datetime.now()
        if force_refresh or not self.access_token or not self.token_expires_at:
            return self._request_access_token()
        # 만료 전 마진 체크
        if now + timedelta(minutes=self.token_refresh_margin) >= self.token_expires_at:
            logger.info("🔄 Access Token 만료 임박, 자동 갱신")
            return self._request_access_token()
        return self.access_token

    def _request_approval_key(self):
        """
        WebSocket용 Approval Key 요청
        """
        url = f"{self.config['api']['base_url']}/uapi/overseas-stock/v1/websocket/approval"
        token = self.get_access_token()
        if not token:
            logger.error("❌ Approval Key 요청 시 Access Token 없음")
            return None

        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": self.config['api_key'],
            "appsecret": self.config['api_secret']
        }
        try:
            resp = requests.post(url, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            key = data.get("approval_key")
            if key:
                self.approval_key = key
                self.approval_expires_at = datetime.now() + timedelta(seconds=self.approval_margin_seconds)
                logger.info(f"✅ Approval Key 발급 성공 (만료: {self.approval_expires_at.strftime('%H:%M:%S')})")
                return key
            else:
                logger.error(f"❌ Approval Key 응답 누락: {data}")
        except Exception as e:
            logger.error(f"❌ Approval Key 요청 오류: {e}")
        return None

    def get_approval_key(self, force_refresh=False):
        """
        유효한 Approval Key 반환
        - Approval Key 만료 30분 전이면 자동 갱신
        """
        now = datetime.now()
        if force_refresh or not self.approval_key or not self.approval_expires_at:
            return self._request_approval_key()
        if now >= self.approval_expires_at - timedelta(seconds=self.approval_margin_seconds):
            return self._request_approval_key()
        return self.approval_key


# 테스트 코드
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    from config import load_config

    cfg = load_config('production')
    tm = TokenManager(cfg)

    print("=" * 80)
    print("1) Access Token 발급 테스트")
    token = tm.get_access_token()
    print(f"Access Token: {token[:20]}...")

    print("\n2) Approval Key 발급 테스트")
    key = tm.get_approval_key()
    print(f"Approval Key: {key[:20]}...")
