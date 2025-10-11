import requests
import json
import logging
import time
from datetime import datetime, timedelta

class TokenManager:
    def __init__(self, config):
        self.config = config
        self.access_token = None
        self.token_expires_at = None
        self.url_base = config['api']['base_url']
        
    def get_access_token(self, force_refresh=False):
        """접근 토큰 발급 및 갱신"""
        if not force_refresh and self.access_token and self.token_expires_at:
            if datetime.now() < self.token_expires_at - timedelta(minutes=5):
                return self.access_token
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                headers = {"content-type": "application/json"}
                body = {
                    "grant_type": "client_credentials",
                    "appkey": self.config['api_key'],
                    "appsecret": self.config['api_secret']
                }
                
                url = f"{self.url_base}/oauth2/tokenP"
                response = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
                response.raise_for_status()
                
                token_data = response.json()
                self.access_token = token_data["access_token"]
                
                # 토큰 만료 시간 설정 (기본 1시간)
                expires_in = token_data.get("expires_in", 3600)
                self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
                
                logging.info("✅ API 접근 토큰 발급/갱신 성공!")
                return self.access_token
                
            except requests.exceptions.RequestException as e:
                logging.warning(f"토큰 발급 시도 {attempt + 1}/{max_retries} 실패: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # 지수적 백오프
                else:
                    logging.error("토큰 발급 최대 재시도 횟수 초과")
                    return None
            except Exception as e:
                logging.error(f"토큰 발급 중 예외 발생: {e}")
                return None
        
        return None
        