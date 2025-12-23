# auth.py - 한국투자증권 API 인증/토큰 관리 (파일 공유 기능 + 스레드 Lock 적용 버전)

import requests
import json
import logging
import time
import os
import threading  # ✅ [추가] 스레드 락 기능을 위해 추가
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ✅ 토큰을 저장할 파일 경로 (모든 스크립트가 이 파일을 공유함)
TOKEN_FILE_PATH = "token_store.json"


class KISAPIError(Exception):
    """한국투자증권 API 오류 기본 클래스"""
    pass


class RateLimitError(KISAPIError):
    """Rate Limit 초과 오류"""
    pass


class AuthenticationError(KISAPIError):
    """인증 실패 오류"""
    pass


class NetworkError(KISAPIError):
    """네트워크 오류"""
    pass


class TokenManager:
    """
    한국투자증권 API용 TokenManager (기획서 v1.1 완전 준수 + 토큰 파일 공유 + 스레드 안전)
    
    주요 기능:
    - REST 액세스 토큰 자동 갱신 및 파일 공유 (token_store.json)
    - WebSocket Approval Key 자동 갱신 및 파일 공유
    - Rate Limit 고려한 재시도 로직
    - 멀티스레드 환경에서의 충돌 방지 (Lock)
    """

    def __init__(self, config, telegram_bot=None):
        self.config = config
        self.telegram_bot = telegram_bot
        self.access_token = None
        self.token_expires_at = None
        self.approval_key = None
        self.approval_expires_at = None

        # ✅ [추가] 스레드 충돌 방지용 락 생성 (RLock 사용으로 데드락 방지)
        self.lock = threading.RLock()

        # ✅ 토큰 만료 마진 (토큰 만료 전 재갱신)
        self.token_refresh_margin = config.get('system', {}).get('token_refresh_margin_minutes', 5)
        
        # ✅ Approval Key 만료 간격 설정
        self.approval_validity_seconds = config.get('system', {}).get('approval_validity_seconds', 43200)  # 12시간
        self.approval_refresh_margin = config.get('system', {}).get('approval_refresh_margin_seconds', 3600)  # 1시간 전 갱신
        
        # ✅ 재시도 설정
        self.max_retries = 3
        self.retry_delays = [1, 3, 5]  # 초 단위
        
        # ✅ Rate Limit 추적
        self.last_token_request_time = None
        self.last_approval_request_time = None
        self.min_request_interval = 1.0  # 최소 1초 간격

        # ✅ 초기화 시 파일에서 토큰 로드 시도
        self._load_token_from_file()

    def _load_token_from_file(self):
        """
        파일에서 토큰 정보를 불러옵니다. (프로세스 간 공유)
        """
        if not os.path.exists(TOKEN_FILE_PATH):
            return

        try:
            with open(TOKEN_FILE_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                now = datetime.now()
                
                # Access Token 복구
                if 'access_token' in data and 'expires_at' in data:
                    expires_str = data.get('expires_at')
                    if expires_str:
                        expires = datetime.fromisoformat(expires_str)
                        if expires > now:
                            self.access_token = data['access_token']
                            self.token_expires_at = expires
                            logger.info(f"📂 파일에서 Access Token 로드 완료 (만료: {expires})")
                
                # Approval Key 복구
                if 'approval_key' in data and 'approval_expires_at' in data:
                    app_expires_str = data.get('approval_expires_at')
                    if app_expires_str:
                        app_expires = datetime.fromisoformat(app_expires_str)
                        if app_expires > now:
                            self.approval_key = data['approval_key']
                            self.approval_expires_at = app_expires
                            logger.info(f"📂 파일에서 Approval Key 로드 완료 (만료: {app_expires})")

        except Exception as e:
            logger.warning(f"⚠️ 토큰 파일 로드 실패 (무시하고 새로 발급): {e}")

    def _save_token_to_file(self):
        """
        현재 토큰 정보를 파일에 저장합니다.
        """
        try:
            data = {}
            # 기존 파일 내용 읽기 (기존 정보를 덮어쓰지 않기 위해)
            if os.path.exists(TOKEN_FILE_PATH):
                try:
                    with open(TOKEN_FILE_PATH, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                except Exception:
                    data = {}

            # 현재 메모리에 있는 유효한 정보 업데이트
            if self.access_token and self.token_expires_at:
                data['access_token'] = self.access_token
                data['expires_at'] = self.token_expires_at.isoformat()
            
            if self.approval_key and self.approval_expires_at:
                data['approval_key'] = self.approval_key
                data['approval_expires_at'] = self.approval_expires_at.isoformat()
            
            with open(TOKEN_FILE_PATH, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            # logger.debug("💾 토큰 정보 파일 저장 완료")
        except Exception as e:
            logger.error(f"❌ 토큰 파일 저장 실패: {e}")

    def _wait_for_rate_limit(self, last_request_time):
        """
        Rate Limit 고려한 대기
        """
        if last_request_time:
            elapsed = time.time() - last_request_time
            if elapsed < self.min_request_interval:
                wait_time = self.min_request_interval - elapsed
                logger.debug(f"⏳ Rate Limit 보호: {wait_time:.2f}초 대기")
                time.sleep(wait_time)

    def _send_telegram_alert(self, message, level="warning"):
        """
        텔레그램 알림 전송
        """
        if self.telegram_bot and hasattr(self.telegram_bot, 'send_message'):
            try:
                emoji = "🚨" if level == "critical" else "⚠️" if level == "warning" else "ℹ️"
                self.telegram_bot.send_message(f"{emoji} {message}")
            except Exception as e:
                logger.error(f"텔레그램 알림 전송 실패: {e}")

    def _request_access_token(self):
        """
        REST API용 Access Token 요청 (재시도 로직 포함)
        """
        url = f"{self.config['api']['base_url']}/oauth2/tokenP"
        headers = {"Content-Type": "application/json"}
        body = {
            "grant_type": "client_credentials",
            "appkey": self.config['api_key'],
            "appsecret": self.config['api_secret']
        }
        
        # Rate Limit 보호
        self._wait_for_rate_limit(self.last_token_request_time)
        
        # 재시도 로직
        for attempt in range(self.max_retries):
            try:
                logger.info(f"🔑 Access Token 요청 중... (시도 {attempt + 1}/{self.max_retries})")
                
                resp = requests.post(url, headers=headers, json=body, timeout=10)
                self.last_token_request_time = time.time()
                
                # Rate Limit 오류 처리
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get('Retry-After', 60))
                    logger.warning(f"⚠️ Rate Limit 초과 (429), {retry_after}초 후 재시도")
                    self._send_telegram_alert(f"Access Token Rate Limit 초과\n{retry_after}초 대기 중", "warning")
                    time.sleep(retry_after)
                    continue
                
                # 인증 오류 처리
                if resp.status_code in [401, 403]:
                    logger.critical(f"🚨 인증 실패 (HTTP {resp.status_code}) - 시스템을 종료합니다.")
                    self._send_telegram_alert(f"🚨 인증 실패 (HTTP {resp.status_code})\n시스템을 안전하게 종료합니다.", "critical")
                    
                    # [수정] 무한 재부팅을 막기 위해 여기서 프로그램을 강제 종료합니다.
                    import sys
                    sys.exit(1)
                
                resp.raise_for_status()
                data = resp.json()
                
                token = data.get("access_token")
                expires_in = data.get("expires_in", 86400)
                
                if token:
                    self.access_token = token
                    self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
                    logger.info(f"✅ Access Token 발급 성공 (만료: {self.token_expires_at.strftime('%Y-%m-%d %H:%M:%S')})")
                    
                    # ✅ 성공 시 파일 저장
                    self._save_token_to_file()
                    return token
                else:
                    logger.error(f"❌ Access Token 응답 누락: {data}")
                    raise KISAPIError("토큰 응답 누락")
                    
            except requests.exceptions.Timeout:
                logger.warning(f"⏱️ Access Token 요청 타임아웃 (시도 {attempt + 1}/{self.max_retries})")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delays[attempt])
                else:
                    self._send_telegram_alert("Access Token 요청 타임아웃 (3회 실패)", "critical")
                    raise NetworkError("타임아웃 (3회)")
                    
            except requests.exceptions.ConnectionError as e:
                logger.warning(f"🌐 Access Token 네트워크 오류 (시도 {attempt + 1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delays[attempt])
                else:
                    self._send_telegram_alert("Access Token 네트워크 오류 (3회 실패)", "critical")
                    raise NetworkError(f"네트워크 오류: {e}")
                    
            except AuthenticationError:
                raise
                
            except Exception as e:
                logger.error(f"❌ Access Token 요청 중 예외 (시도 {attempt + 1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delays[attempt])
                else:
                    self._send_telegram_alert(f"Access Token 발급 실패\n{str(e)}", "critical")
                    raise KISAPIError(f"토큰 발급 실패: {e}")
        
        return None

    def get_access_token(self, force_refresh=False):
        with self.lock:
            # 🔴 [안전장치] 과속 방지 (1분 쿨타임)
            if force_refresh and self.access_token and self.token_expires_at:
                try:
                    issued_time = self.token_expires_at - timedelta(hours=24)
                    if datetime.now() < issued_time + timedelta(seconds=60):
                        logger.warning("⚠️ 토큰 발급 1분 내 재요청 감지 -> API 보호를 위해 갱신 스킵")
                        return self.access_token
                except Exception:
                    pass

            if not force_refresh:
                self._load_token_from_file()
            
            # ✅ 2. 메모리/파일에 유효한 토큰이 있는지 확인
            if not force_refresh and self.access_token and self.token_expires_at:
                time_until_expiry = (self.token_expires_at - datetime.now()).total_seconds()
                margin_seconds = self.token_refresh_margin * 60
                
                # 만료 시간과 마진을 비교하여 유효하면 반환
                if time_until_expiry > margin_seconds:
                    return self.access_token

            # ✅ 3. 없거나 만료 임박이면 새로 발급
            logger.info("🔄 Access Token 신규 발급/갱신 시작")
            return self._request_access_token()

    def _request_approval_key(self):
        """
        WebSocket용 Approval Key 요청 (재시도 로직 포함)
        """
        url = f"{self.config['api']['base_url']}/oauth2/Approval"
        
        # Access Token 확보
        token = self.get_access_token()
        if not token:
            logger.error("❌ Approval Key 요청 시 Access Token 없음")
            self._send_telegram_alert("Approval Key 발급 불가\nAccess Token 없음", "critical")
            return None

        headers = {
            "Content-Type": "application/json",
            "Accept": "text/plain",
            "charset": "UTF-8"
        }
        
        data = {
            "grant_type": "client_credentials",
            "appkey": self.config['api_key'],
            "secretkey": self.config['api_secret']
        }
        
        # Rate Limit 보호
        self._wait_for_rate_limit(self.last_approval_request_time)
        
        # 재시도 로직
        for attempt in range(self.max_retries):
            try:
                logger.info(f"🔐 Approval Key 요청 중... (시도 {attempt + 1}/{self.max_retries})")
                resp = requests.post(url, data=json.dumps(data), headers=headers, timeout=10)
                self.last_approval_request_time = time.time()
                
                # Rate Limit 오류 처리
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get('Retry-After', 60))
                    logger.warning(f"⚠️ Rate Limit 초과 (429), {retry_after}초 후 재시도")
                    self._send_telegram_alert(f"Approval Key Rate Limit 초과\n{retry_after}초 대기 중", "warning")
                    time.sleep(retry_after)
                    continue
                
                # 인증 오류 처리
                if resp.status_code in [401, 403]:
                    logger.error(f"❌ 인증 실패 (HTTP {resp.status_code}), Access Token 갱신 시도")
                    token = self.get_access_token(force_refresh=True)
                    if token:
                        continue
                    else:
                        self._send_telegram_alert(f"Approval Key 인증 실패\nHTTP {resp.status_code}", "critical")
                        raise AuthenticationError(f"HTTP {resp.status_code}: 인증 실패")
                
                resp.raise_for_status()
                data = resp.json()
                
                key = data.get("approval_key")
                
                if key:
                    self.approval_key = key
                    self.approval_expires_at = datetime.now() + timedelta(seconds=self.approval_validity_seconds)
                    logger.info(f"✅ Approval Key 발급 성공 (만료: {self.approval_expires_at.strftime('%Y-%m-%d %H:%M:%S')})")
                    
                    # ✅ 성공 시 파일 저장
                    self._save_token_to_file()
                    return key
                else:
                    logger.error(f"❌ Approval Key 응답 누락: {data}")
                    raise KISAPIError("Approval Key 응답 누락")
                    
            except requests.exceptions.Timeout:
                logger.warning(f"⏱️ Approval Key 요청 타임아웃 (시도 {attempt + 1}/{self.max_retries})")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delays[attempt])
                else:
                    self._send_telegram_alert("Approval Key 요청 타임아웃 (3회 실패)", "critical")
                    raise NetworkError("타임아웃 (3회)")
                    
            except requests.exceptions.ConnectionError as e:
                logger.warning(f"🌐 Approval Key 네트워크 오류 (시도 {attempt + 1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delays[attempt])
                else:
                    self._send_telegram_alert("Approval Key 네트워크 오류 (3회 실패)", "critical")
                    raise NetworkError(f"네트워크 오류: {e}")
                    
            except AuthenticationError:
                raise
                
            except Exception as e:
                logger.error(f"❌ Approval Key 요청 중 예외 (시도 {attempt + 1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delays[attempt])
                else:
                    self._send_telegram_alert(f"Approval Key 발급 실패\n{str(e)}", "critical")
                    raise KISAPIError(f"Approval Key 발급 실패: {e}")
        
        return None

    def get_approval_key(self, force_refresh=False):
        """
        유효한 Approval Key 반환 (스레드 안전)
        """
        # ✅ [수정] 스레드 Lock을 걸어 동시 접근 제어
        with self.lock:
            now = datetime.now()
            
            # ✅ 1. 강제 갱신이 아니면 파일 내용을 먼저 최신화
            if not force_refresh:
                self._load_token_from_file()

            # ✅ 2. 메모리/파일에 유효한 키가 있는지 확인
            if not force_refresh and self.approval_key and self.approval_expires_at:
                time_until_expiry = (self.approval_expires_at - datetime.now()).total_seconds()
                
                if time_until_expiry > self.approval_refresh_margin:
                    return self.approval_key
            
            # ✅ 3. 없거나 만료 임박이면 새로 발급
            logger.info("🔄 Approval Key 신규 발급/갱신 시작")
            return self._request_approval_key()

    def get_token_status(self):
        """
        토큰 상태 확인 (디버깅/모니터링용)
        """
        now = datetime.now()
        
        # 파일 최신 상태 한번 더 확인
        self._load_token_from_file()
        
        access_status = {
            'exists': bool(self.access_token),
            'expires_at': self.token_expires_at.strftime('%Y-%m-%d %H:%M:%S') if self.token_expires_at else None,
            'expires_in_seconds': (self.token_expires_at - datetime.now()).total_seconds() if self.token_expires_at else None,
            'is_valid': (self.token_expires_at - datetime.now()).total_seconds() > 0 if self.token_expires_at else False
        }
        
        approval_status = {
            'exists': bool(self.approval_key),
            'expires_at': self.approval_expires_at.strftime('%Y-%m-%d %H:%M:%S') if self.approval_expires_at else None,
            'expires_in_seconds': (self.approval_expires_at - datetime.now()).total_seconds() if self.approval_expires_at else None,
            'is_valid': (self.approval_expires_at - datetime.now()).total_seconds() > 0 if self.approval_expires_at else False
        }
        
        return {
            'access_token': access_status,
            'approval_key': approval_status
        }


# 테스트 코드
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, 
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    from config import load_config

    print("=" * 80)
    print("🧪 TokenManager 테스트 (파일 공유 + 스레드 안전 버전)")
    print("=" * 80)
    
    cfg = load_config('production')
    tm = TokenManager(cfg)

    print("\n1️⃣ Access Token 발급/로드 테스트")
    print("-" * 80)
    try:
        token = tm.get_access_token()
        print(f"✅ Access Token: {token[:30]}...")
        
        # 상태 확인
        status = tm.get_token_status()
        print(f"📊 만료 시각: {status['access_token']['expires_at']}")
        print(f"⏰ 남은 시간: {status['access_token']['expires_in_seconds']:.0f}초")
    except Exception as e:
        print(f"❌ 실패: {e}")

    print("\n2️⃣ Approval Key 발급/로드 테스트")
    print("-" * 80)
    try:
        key = tm.get_approval_key()
        print(f"✅ Approval Key: {key[:30]}...")
        
        # 상태 확인
        status = tm.get_token_status()
        print(f"📊 만료 시각: {status['approval_key']['expires_at']}")
        print(f"⏰ 남은 시간: {status['approval_key']['expires_in_seconds']:.0f}초")
    except Exception as e:
        print(f"❌ 실패: {e}")
    
    print("\n3️⃣ 파일 저장 확인")
    print("-" * 80)
    if os.path.exists(TOKEN_FILE_PATH):
        print(f"✅ {TOKEN_FILE_PATH} 파일이 존재합니다.")
        with open(TOKEN_FILE_PATH, 'r', encoding='utf-8') as f:
            print(f"📄 파일 내용: {f.read()[:100]}...")
    else:
        print(f"❌ {TOKEN_FILE_PATH} 파일이 없습니다.")

    print("\n" + "=" * 80)
    print("✅ 테스트 완료")
    print("=" * 80)