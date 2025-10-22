# auth.py - 한국투자증권 API 인증/토큰 관리 (기획서 v1.0 완전 준수 버전)

import requests
import json
import logging
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


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
    한국투자증권 API용 TokenManager (기획서 v1.0 완전 준수)
    
    주요 기능:
    - REST 액세스 토큰 자동 갱신
    - WebSocket Approval Key 자동 갱신
    - Rate Limit 고려한 재시도 로직 (기획서 5.1절)
    - 오류 유형별 처리 (기획서 8.1절)
    - 텔레그램 알림 (기획서 6.1절)
    """

    def __init__(self, config, telegram_bot=None):
        self.config = config
        self.telegram_bot = telegram_bot
        self.access_token = None
        self.token_expires_at = None
        self.approval_key = None
        self.approval_expires_at = None

        # ✅ 토큰 만료 마진 (토큰 만료 전 재갱신)
        self.token_refresh_margin = config.get('system', {}).get('token_refresh_margin_minutes', 5)
        
        # ✅ Approval Key 만료 간격: 실제 만료 시간 추정 (한투증권 문서 기준 24시간)
        # 하지만 안전을 위해 30분마다 갱신하는 것은 너무 빈번함
        # 12시간(43200초)으로 설정하고, 만료 1시간 전에 갱신
        self.approval_validity_seconds = config.get('system', {}).get('approval_validity_seconds', 43200)  # 12시간
        self.approval_refresh_margin = config.get('system', {}).get('approval_refresh_margin_seconds', 3600)  # 1시간 전 갱신
        
        # ✅ 추가: 재시도 설정 (기획서 8.1절)
        self.max_retries = 3
        self.retry_delays = [1, 3, 5]  # 초 단위
        
        # ✅ 추가: Rate Limit 추적
        self.last_token_request_time = None
        self.last_approval_request_time = None
        self.min_request_interval = 1.0  # 최소 1초 간격

    def _wait_for_rate_limit(self, last_request_time):
        """
        ✅ 추가: Rate Limit 고려한 대기 (기획서 5.1절)
        """
        if last_request_time:
            elapsed = time.time() - last_request_time
            if elapsed < self.min_request_interval:
                wait_time = self.min_request_interval - elapsed
                logger.debug(f"⏳ Rate Limit 보호: {wait_time:.2f}초 대기")
                time.sleep(wait_time)

    def _send_telegram_alert(self, message, level="warning"):
        """
        ✅ 추가: 텔레그램 알림 전송 (기획서 6.1절)
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
        
        ✅ 개선: 기획서 8.1절 "오류 유형별 처리" 준수
        """
        url = f"{self.config['api']['base_url']}/oauth2/tokenP"
        headers = {"Content-Type": "application/json"}
        body = {
            "grant_type": "client_credentials",
            "appkey": self.config['api_key'],
            "appsecret": self.config['api_secret']
        }
        
        # ✅ Rate Limit 보호
        self._wait_for_rate_limit(self.last_token_request_time)
        
        # ✅ 재시도 로직
        for attempt in range(self.max_retries):
            try:
                logger.info(f"🔑 Access Token 요청 중... (시도 {attempt + 1}/{self.max_retries})")
                
                resp = requests.post(url, headers=headers, json=body, timeout=10)
                self.last_token_request_time = time.time()
                
                # ✅ Rate Limit 오류 처리
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get('Retry-After', 60))
                    logger.warning(f"⚠️ Rate Limit 초과 (429), {retry_after}초 후 재시도")
                    self._send_telegram_alert(f"Access Token Rate Limit 초과\n{retry_after}초 대기 중", "warning")
                    time.sleep(retry_after)
                    continue
                
                # ✅ 인증 오류 처리
                if resp.status_code in [401, 403]:
                    logger.error(f"❌ 인증 실패 (HTTP {resp.status_code})")
                    self._send_telegram_alert(f"Access Token 인증 실패\nHTTP {resp.status_code}\n설정 확인 필요", "critical")
                    raise AuthenticationError(f"HTTP {resp.status_code}: 인증 실패")
                
                resp.raise_for_status()
                data = resp.json()
                
                token = data.get("access_token")
                expires_in = data.get("expires_in", 86400)
                
                if token:
                    self.access_token = token
                    self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
                    logger.info(f"✅ Access Token 발급 성공 (만료: {self.token_expires_at.strftime('%Y-%m-%d %H:%M:%S')})")
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
                # 인증 오류는 재시도하지 않음
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
        """
        유효한 Access Token 반환
        
        - 토큰 만료 5분 전이면 자동 갱신 (기본값)
        - force_refresh=True 시 즉시 갱신
        """
        now = datetime.now()
        
        # 강제 갱신 또는 토큰 없음
        if force_refresh or not self.access_token or not self.token_expires_at:
            logger.info("🔄 Access Token 발급/갱신 시작")
            return self._request_access_token()
        
        # ✅ 만료 전 마진 체크
        time_until_expiry = (self.token_expires_at - now).total_seconds()
        margin_seconds = self.token_refresh_margin * 60
        
        if time_until_expiry <= margin_seconds:
            logger.info(f"🔄 Access Token 만료 임박 ({time_until_expiry:.0f}초 남음), 자동 갱신")
            return self._request_access_token()
        
        # 유효한 토큰 반환
        logger.debug(f"✅ Access Token 유효 ({time_until_expiry:.0f}초 남음)")
        return self.access_token

    def _request_approval_key(self):
        """
        WebSocket용 Approval Key 요청 (재시도 로직 포함)
        
        ✅ 개선: 기획서 8.1절 "오류 유형별 처리" 준수
        """
        url = f"{self.config['api']['base_url']}/oauth2/Approval"
        
        # ✅ Access Token 확보
        token = self.get_access_token()
        if not token:
            logger.error("❌ Approval Key 요청 시 Access Token 없음")
            self._send_telegram_alert("Approval Key 발급 불가\nAccess Token 없음", "critical")
            return None

        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": self.config['api_key'],
            "appsecret": self.config['api_secret']
        }
        
        # ✅ Rate Limit 보호
        self._wait_for_rate_limit(self.last_approval_request_time)
        
        # ✅ 재시도 로직
        for attempt in range(self.max_retries):
            try:
                logger.info(f"🔐 Approval Key 요청 중... (시도 {attempt + 1}/{self.max_retries})")
                resp = requests.post(url, headers=headers, timeout=10)
                self.last_approval_request_time = time.time()
                
                # ✅ Rate Limit 오류 처리
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get('Retry-After', 60))
                    logger.warning(f"⚠️ Rate Limit 초과 (429), {retry_after}초 후 재시도")
                    self._send_telegram_alert(f"Approval Key Rate Limit 초과\n{retry_after}초 대기 중", "warning")
                    time.sleep(retry_after)
                    continue
                
                # ✅ 인증 오류 처리
                if resp.status_code in [401, 403]:
                    logger.error(f"❌ 인증 실패 (HTTP {resp.status_code}), Access Token 갱신 시도")
                    # Access Token 갱신 후 재시도
                    token = self.get_access_token(force_refresh=True)
                    if token:
                        headers["authorization"] = f"Bearer {token}"
                        continue
                    else:
                        self._send_telegram_alert(f"Approval Key 인증 실패\nHTTP {resp.status_code}", "critical")
                        raise AuthenticationError(f"HTTP {resp.status_code}: 인증 실패")
                
                resp.raise_for_status()
                data = resp.json()
                
                key = data.get("approval_key")
                
                if key:
                    self.approval_key = key
                    # ✅ 개선: 실제 만료 시간 계산
                    self.approval_expires_at = datetime.now() + timedelta(seconds=self.approval_validity_seconds)
                    logger.info(f"✅ Approval Key 발급 성공 (만료: {self.approval_expires_at.strftime('%Y-%m-%d %H:%M:%S')})")
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
                # 인증 오류는 재시도하지 않음
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
        유효한 Approval Key 반환
        
        ✅ 개선: 만료 시간 계산 로직 개선
        - Approval Key 만료 1시간 전이면 자동 갱신 (기본값)
        - force_refresh=True 시 즉시 갱신
        """
        now = datetime.now()
        
        # 강제 갱신 또는 키 없음
        if force_refresh or not self.approval_key or not self.approval_expires_at:
            logger.info("🔄 Approval Key 발급/갱신 시작")
            return self._request_approval_key()
        
        # ✅ 만료 전 마진 체크
        time_until_expiry = (self.approval_expires_at - now).total_seconds()
        
        if time_until_expiry <= self.approval_refresh_margin:
            logger.info(f"🔄 Approval Key 만료 임박 ({time_until_expiry:.0f}초 남음), 자동 갱신")
            return self._request_approval_key()
        
        # 유효한 키 반환
        logger.debug(f"✅ Approval Key 유효 ({time_until_expiry:.0f}초 남음)")
        return self.approval_key

    def get_token_status(self):
        """
        ✅ 추가: 토큰 상태 확인 (디버깅/모니터링용)
        """
        now = datetime.now()
        
        access_status = {
            'exists': bool(self.access_token),
            'expires_at': self.token_expires_at.strftime('%Y-%m-%d %H:%M:%S') if self.token_expires_at else None,
            'expires_in_seconds': (self.token_expires_at - now).total_seconds() if self.token_expires_at else None,
            'is_valid': (self.token_expires_at - now).total_seconds() > 0 if self.token_expires_at else False
        }
        
        approval_status = {
            'exists': bool(self.approval_key),
            'expires_at': self.approval_expires_at.strftime('%Y-%m-%d %H:%M:%S') if self.approval_expires_at else None,
            'expires_in_seconds': (self.approval_expires_at - now).total_seconds() if self.approval_expires_at else None,
            'is_valid': (self.approval_expires_at - now).total_seconds() > 0 if self.approval_expires_at else False
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
    print("🧪 TokenManager 테스트 (기획서 v1.0 준수 버전)")
    print("=" * 80)
    
    cfg = load_config('production')
    tm = TokenManager(cfg)

    print("\n1️⃣ Access Token 발급 테스트")
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

    print("\n2️⃣ Approval Key 발급 테스트")
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
    
    print("\n3️⃣ 자동 갱신 테스트")
    print("-" * 80)
    try:
        # 강제 갱신 테스트
        print("🔄 강제 갱신 시도...")
        token = tm.get_access_token(force_refresh=True)
        print(f"✅ 갱신된 Access Token: {token[:30]}...")
    except Exception as e:
        print(f"❌ 실패: {e}")
    
    print("\n" + "=" * 80)
    print("✅ 테스트 완료")
    print("=" * 80)