# infra/kis_auth.py
import requests
import json
import time
import os
import threading
from datetime import datetime, timedelta
from config import Config
from infra.utils import get_logger 

logger = get_logger()

class KisAuth: 
    def __init__(self):
        self._lock = threading.RLock()
        self.token_file = "token_store.json"
        self.access_token = None
        self.token_expired = None
        self._load_token_from_disk()

    def get_token(self):
        with self._lock:
            if self._is_token_valid():
                return self.access_token
            logger.info("토큰이 없거나 만료되었습니다. 신규 발급을 시도합니다.")
            return self._issue_new_token()

    def _is_token_valid(self):
        if self.access_token is None or self.token_expired is None:
            return False
        if datetime.now() < (self.token_expired - timedelta(minutes=1)):
            return True
        return False

    def _issue_new_token(self):
        # [Fix] Config.URL_BASE 사용 (Config() 인스턴스화 제거)
        url = f"{Config.URL_BASE}/oauth2/tokenP"
        headers = {"content-type": "application/json"}
        body = {
            "grant_type": "client_credentials",
            "appkey": Config.APP_KEY,
            "appsecret": Config.APP_SECRET
        }

        try:
            res = requests.post(url, headers=headers, data=json.dumps(body))
            res.raise_for_status()
            res_json = res.json()

            self.access_token = res_json['access_token']
            expires_in = int(res_json.get('expires_in', 86400))
            self.token_expired = datetime.now() + timedelta(seconds=expires_in)
            
            logger.info(f"Access Token 신규 발급 완료. 만료: {self.token_expired}")
            self._save_token_to_disk()
            return self.access_token

        except Exception as e:
            logger.error(f"Token 발급 실패: {e}")
            raise

    def _save_token_to_disk(self):
        data = {
            "access_token": self.access_token,
            "token_expired": self.token_expired.strftime("%Y-%m-%d %H:%M:%S")
        }
        try:
            with open(self.token_file, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            logger.error(f"토큰 파일 저장 실패: {e}")

    def _load_token_from_disk(self):
        if not os.path.exists(self.token_file):
            return
        try:
            with open(self.token_file, 'r') as f:
                data = json.load(f)
            saved_token = data.get('access_token')
            saved_expired_str = data.get('token_expired')
            
            if saved_token and saved_expired_str:
                saved_expired = datetime.strptime(saved_expired_str, "%Y-%m-%d %H:%M:%S")
                if datetime.now() < saved_expired:
                    self.access_token = saved_token
                    self.token_expired = saved_expired
                    logger.info("기존 유효 토큰 로드 완료.")
        except Exception as e:
            logger.error(f"토큰 로드 오류: {e}")
