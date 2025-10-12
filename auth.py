import requests
import json
import logging
from datetime import datetime, timedelta

class TokenManager:
    def __init__(self, config):
        self.config = config
        self.access_token = None
        self.token_expires_at = None

    def get_access_token(self, force_refresh=False):
        """
        최초 발급 후 만료 10분 전까진 기존 토큰을 재사용.
        오직 최초 실행 시와 만료 직전(10분 미만 남았을 때)만 새 토큰 발급.
        """
        now = datetime.now()
        if (not force_refresh 
            and self.access_token 
            and self.token_expires_at 
            and now < self.token_expires_at - timedelta(minutes=10)):
            return self.access_token

        # 토큰 발급/갱신
        logging.info("🔑 토큰 발급/갱신 요청")
        url = f"{self.config['api']['base_url']}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.config['api_key'],
            "appsecret": self.config['api_secret']
        }
        res = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(body),
            timeout=10
        )
        res.raise_for_status()
        data = res.json()
        self.access_token = data["access_token"]

        # expires_in이 없으면 기본 86400초(24시간) 사용
        expires_in = data.get("expires_in", 86400)
        self.token_expires_at = now + timedelta(seconds=expires_in)
        logging.info(f"✅ 토큰 발급 완료 (만료: {self.token_expires_at})")
        return self.access_token
