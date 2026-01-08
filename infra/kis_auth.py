# kis_auth.py
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
    """
    KIS 접근 토큰 관리자
    - 파일 캐싱 (token_store.json)
    - 자동 갱신 (Auto Refresh)
    - 스레드 안전 (Thread Safe)
    """
    def __init__(self):
        self._lock = threading.RLock()
        self.token_file = "token_store.json"
        self.access_token = None
        self.token_expired = None # 토큰 만료 시간 (datetime 객체)
        
        # 초기화 시 파일 로드 시도
        self._load_token_from_disk()

    def get_token(self):
        """유효한 토큰 반환 (만료 시 자동 갱신)"""
        with self._lock:
            if self._is_token_valid():
                return self.access_token
            
            logger.info("토큰이 없거나 만료되었습니다. 신규 발급을 시도합니다.")
            return self._issue_new_token()

    def _is_token_valid(self):
        """메모리 상의 토큰 유효성 검사"""
        if self.access_token is None or self.token_expired is None:
            return False
        
        # 만료 시간 1분 전 여유를 두고 체크
        if datetime.now() < (self.token_expired - timedelta(minutes=1)):
            return True
        return False

    def _issue_new_token(self):
        """REST API를 통해 신규 토큰 발급"""
        url = f"{Config().BASE_URL}/oauth2/tokenP"
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
            
            # 유효기간 계산 (response의 expires_in은 초 단위, 보통 86400초=24시간)
            expires_in = int(res_json.get('expires_in', 86400))
            self.token_expired = datetime.now() + timedelta(seconds=expires_in)
            
            logger.info(f"Access Token 신규 발급 완료. 만료시간: {self.token_expired}")
            
            # 파일 저장
            self._save_token_to_disk()
            
            return self.access_token

        except Exception as e:
            logger.error(f"Token 발급 실패: {e}")
            raise

    def refresh_token(self):
        """
        외부에서 호출 가능한 토큰 강제 갱신 메서드
        - main.py의 에러 핸들링에서 사용
        - 인증 에러 발생 시 명시적 갱신 시도
        """
        with self._lock:
            logger.info("🔑 토큰 강제 갱신 요청...")
            try:
                token = self._issue_new_token()
                logger.info("✅ 토큰 강제 갱신 성공")
                return token
            except Exception as e:
                logger.error(f"❌ 토큰 강제 갱신 실패: {e}")
                raise
    
    def get_token_info(self):
        """
        토큰 상태 정보 반환 (디버깅/모니터링용)
        Returns:  dict with 'valid', 'expires_at', 'remaining_seconds'
        """
        with self._lock:
            if self.access_token is None or self.token_expired is None:
                return {
                    'valid': False,
                    'expires_at': None,
                    'remaining_seconds': 0
                }
            
            now = datetime.now()
            remaining = (self.token_expired - now).total_seconds()
            
            return {
                'valid': self._is_token_valid(),
                'expires_at': self.token_expired.strftime("%Y-%m-%d %H:%M:%S"),
                'remaining_seconds': int(remaining)
            }
                
    def _save_token_to_disk(self):
        """토큰 정보를 파일로 저장 (캐싱)"""
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
        """파일에서 토큰 로드"""
        if not os.path.exists(self.token_file):
            return

        try:
            with open(self.token_file, 'r') as f:
                data = json.load(f)
                
            saved_token = data.get('access_token')
            saved_expired_str = data.get('token_expired')
            
            if saved_token and saved_expired_str:
                saved_expired = datetime.strptime(saved_expired_str, "%Y-%m-%d %H:%M:%S")
                
                # 로드 시점에서 만료 여부 1차 체크
                if datetime.now() < saved_expired:
                    self.access_token = saved_token
                    self.token_expired = saved_expired
                    logger.info("기존 유효 토큰을 파일에서 로드했습니다.")
                else:
                    logger.info("저장된 토큰이 만료되었습니다.")
        except Exception as e:

            logger.error(f"토큰 파일 로드 중 오류: {e}")


