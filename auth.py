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
    
    def get_websocket_approval_key(self):
        """WebSocket 접속을 위한 approval key 발급"""
        try:
            if not self.websocket_approval_key:
                # Access Token이 필요하므로 먼저 발급
                access_token = self.get_access_token()
                
                url = f"{self.config['api']['base_url']}/oauth2/Approval"
                headers = {
                    "Content-Type": "application/json",
                    "authorization": f"Bearer {access_token}",
                    "appkey": self.config['api_key'],
                    "appsecret": self.config['api_secret']
                }
                
                body = {
                    "grant_type": "client_credentials",
                    "appkey": self.config['api_key'],
                    "secretkey": self.config['api_secret']
                }
                
                response = requests.post(url, headers=headers, json=body, timeout=10)
                response.raise_for_status()
                
                data = response.json()
                self.websocket_approval_key = data.get("approval_key", "")
                
                if not self.websocket_approval_key:
                    # approval_key가 없으면 access_token을 사용
                    logging.warning("⚠️ WebSocket approval key를 받지 못했습니다. access_token을 사용합니다.")
                    self.websocket_approval_key = access_token
                else:
                    logging.info("✅ WebSocket approval key 발급 완료")
                    
            return self.websocket_approval_key
            
        except Exception as e:
            logging.error(f"❌ WebSocket approval key 발급 실패: {e}")
            # 실패 시 access_token을 대신 사용
            return self.get_access_token()