# auth.py - WebSocket 승인키 기능 추가된 완전한 버전

import requests
import json
import logging
from datetime import datetime, timedelta

class TokenManager:
    def __init__(self, config):
        self.config = config
        self.access_token = None
        self.token_expires_at = None
        self.websocket_approval_key = None

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

    def get_websocket_approval_key(self, force_refresh=False):
        """
        WebSocket 접속을 위한 approval key 발급/재사용
    
        한국투자증권은 하나의 승인키로 하나의 WebSocket 세션만 유지.
        기존 승인키가 있으면 재사용하고, 없거나 force_refresh=True일 때만 새로 발급.
        """
        try:
           # ✅ 기존 승인키가 있고 강제 갱신이 아니면 재사용
            if self.websocket_approval_key and not force_refresh:
                logging.info("🔑 기존 WebSocket 승인키 재사용")
                return self.websocket_approval_key
        
        # 새 승인키 발급
            logging.info("🔑 WebSocket 승인키 발급 요청")
        
            url = f"{self.config['api']['base_url']}/oauth2/Approval"
            body = {
                "grant_type": "client_credentials",
                "appkey": self.config['api_key'],
                "secretkey": self.config['api_secret']
            }
        
            headers = {"Content-Type": "application/json"}
        
            response = requests.post(url, headers=headers, json=body, timeout=10)
            response.raise_for_status()
        
            data = response.json()
            self.websocket_approval_key = data.get("approval_key", "")
        
            if self.websocket_approval_key:
                logging.info(f"✅ WebSocket approval key 발급 완료: ***{self.websocket_approval_key[-4:]}")
                return self.websocket_approval_key
            else:
                logging.error("❌ approval_key가 응답에 없습니다.")
                return None
            
        except Exception as e:
            logging.error(f"❌ WebSocket approval key 발급 실패: {e}")
            return None