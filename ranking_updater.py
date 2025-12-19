"""
ranking_updater.py

한국투자증권 API를 통한 상승률 TOP 3 조회 및 업데이트

작성일: 2025-12-06
수정일: 2025-12-19 (토큰 만료 자동 갱신 로직 추가)
버전: 1.1
기획서: v3.0 섹션 6.2
"""

import requests
import logging
from datetime import datetime
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


class RankingUpdater:
    """
    한국투자증권 API를 통한 상승률 TOP 3 조회
    
    Attributes:
        config (dict): 설정 정보
        token_manager (TokenManager): 토큰 관리자
        exchange (str): 거래소 코드 (NYS, NAS, AMS)
        volume_filter (str): 거래량 필터
    """
    
    def __init__(self, config: dict, token_manager):
        """
        초기화
        
        Args:
            config: 전체 설정 딕셔너리
            token_manager: TokenManager 인스턴스
        """
        self.config = config
        self.token_manager = token_manager
        
        # 설정 로드
        auto_config = config['auto_trader']
        self.exchange = auto_config['ranking_api']['exchange']
        self.volume_filter = auto_config['ranking_api']['volume_filter']
        
        # API 정보
        self.base_url = config['api']['base_url']
        self.tr_id = "HHDFS76290000"
        self.timeout = config['api'].get('request_timeout', 10)
        
        logger.info(
            f"RankingUpdater 초기화: {self.exchange}, "
            f"거래량필터={self.volume_filter}"
        )
    
    def get_top3_gainers(self) -> List[Dict[str, Any]]:
        """
        상승률 TOP 3 조회 (토큰 만료 시 자동 갱신 기능 포함)
        
        Returns:
            list: [
                {
                    'ticker': 'TSLA',
                    'name': 'Tesla Inc',
                    'price': 250.50,
                    'rate': 8.50,
                    'volume': 45000000,
                    'rank': 1
                },
                ...
            ]
        
        Raises:
            APIError: API 호출 실패
            NetworkError: 네트워크 오류
        """
        url = f"{self.base_url}/uapi/overseas-stock/v1/ranking/updown-rate"
        
        # 파라미터 구성 (변경되지 않음)
        params = {
            'KEYB': '',  # 공백
            'AUTH': '',  # 공백
            'EXCD': self.exchange,  # 거래소
            'GUBN': '1',  # 1: 상승율
            'NDAY': '0',  # 0: 당일
            'VOL_RANG': self.volume_filter  # 거래량 필터
        }
        
        # [수정된 로직] 토큰 만료 시 재시도를 위해 최대 2번 반복
        data = None
        for attempt in range(2):
            try:
                # 두 번째 시도(attempt=1)라면 토큰 강제 갱신(force_refresh=True)
                is_retry = (attempt > 0)
                token = self.token_manager.get_access_token(force_refresh=is_retry)
                
                if not token:
                    if is_retry:
                        logger.error("❌ 토큰 갱신 실패로 중단합니다.")
                        return []
                    continue

                # 헤더 구성 (갱신된 토큰 적용)
                headers = {
                    'content-type': 'application/json; charset=utf-8',
                    'authorization': f'Bearer {token}',
                    'appkey': self.config['api_key'],
                    'appsecret': self.config['api_secret'],
                    'tr_id': self.tr_id,
                    'custtype': 'P'
                }
                
                logger.debug(f"📊 상승률 조회 시작: {self.exchange} (시도 {attempt+1})")
                
                response = requests.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=self.timeout
                )
                
                data = response.json()
                
                # 1. 토큰 만료 오류(EGW00123) 체크
                msg_cd = data.get('msg_cd')
                if msg_cd == 'EGW00123':
                    logger.warning(f"⚠️ 토큰 만료 감지 (EGW00123). 갱신 후 재시도합니다. (시도 {attempt+1}/2)")
                    continue  # 루프의 다음 단계로 이동하여 토큰 갱신 시도
                
                # 2. 기타 API 오류 체크
                if data.get('rt_cd') != '0':
                    error_msg = data.get('msg1', 'Unknown error')
                    logger.error(f"❌ API 오류: {error_msg}")
                    raise APIError(f"상승률 조회 실패: {error_msg}")
                
                # 성공 시 루프 탈출
                break
                
            except requests.exceptions.Timeout:
                logger.error("❌ API 타임아웃")
                raise NetworkError("상승률 조회 타임아웃")
            
            except requests.exceptions.RequestException as e:
                logger.error(f"❌ 네트워크 오류: {e}")
                raise NetworkError(f"상승률 조회 실패: {e}")
            
            except Exception as e:
                logger.error(f"❌ 예상치 못한 오류: {e}")
                return []
        
        # 루프가 끝난 후 데이터 확인
        if not data or 'output2' not in data or not data['output2']:
            if data and data.get('rt_cd') == '0':
                logger.warning("⚠️ 조회 결과 없음")
            return []
        
        # TOP 3만 추출
        top3 = []
        for idx, item in enumerate(data['output2'][:3]):
            try:
                ticker = item.get('symb', '').strip()
                if not ticker:
                    logger.warning(f"⚠️ 종목코드 없음: {item}")
                    continue
                top3.append({
                    'ticker': item['symb'],
                    'name': item['name'],
                    'price': float(item['last']),
                    'rate': float(item['rate']),
                    'volume': int(item['tvol']),
                    'rank': idx + 1
                })
            except (KeyError, ValueError) as e:
                logger.error(f"❌ 데이터 파싱 오류: {e}, item={item}")
                continue
        
        # 로그 기록
        if top3:
            tickers_str = ', '.join([
                f"{t['ticker']}(+{t['rate']}%)" 
                for t in top3
            ])
            logger.info(f"✅ 상승률 TOP 3: {tickers_str}")
        
        return top3
    
    def get_top3_with_retry(self, max_retries: int = 3) -> List[Dict[str, Any]]:
        """
        재시도 로직이 포함된 TOP 3 조회
        
        Args:
            max_retries: 최대 재시도 횟수
        
        Returns:
            list: TOP 3 목록
        """
        import time
        
        for attempt in range(max_retries):
            try:
                return self.get_top3_gainers()
            
            except NetworkError as e:
                if attempt < max_retries - 1:
                    wait_time = 5 * (attempt + 1)
                    logger.warning(
                        f"⚠️ 재시도 {attempt + 1}/{max_retries} "
                        f"({wait_time}초 후)"
                    )
                    time.sleep(wait_time)
                else:
                    logger.error("❌ 최대 재시도 횟수 도달")
                    raise
            
            except APIError as e:
                logger.error(f"❌ API 오류로 재시도 중단: {e}")
                raise
        
        return []


class APIError(Exception):
    """API 호출 오류"""
    pass


class NetworkError(Exception):
    """네트워크 오류"""
    pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 코드 (직접 실행 시에만 동작)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == '__main__':
    """
    테스트 실행 방법:
    python3 ranking_updater.py
    """
    import yaml
    import sys
    
    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    print("=" * 60)
    print("RankingUpdater 테스트")
    print("=" * 60)
    
    try:
        # 1. 설정 로드
        print("\n1️⃣ 설정 파일 로드 중...")
        with open('config.yaml', 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        print("✅ 설정 로드 완료")
        
        # 2. TokenManager 임포트 및 초기화
        print("\n2️⃣ TokenManager 초기화 중...")
        from auth import TokenManager
        token_manager = TokenManager(config)
        
        # 토큰 발급 (수정: issue_token 대신 get_access_token 사용)
        print("🔑 토큰 발급 중...")
        token_manager.get_access_token(force_refresh=True)
        print("✅ 토큰 발급 완료")
        
        # 3. RankingUpdater 초기화
        print("\n3️⃣ RankingUpdater 초기화 중...")
        updater = RankingUpdater(config, token_manager)
        print("✅ RankingUpdater 초기화 완료")
        
        # 4. TOP 3 조회
        print("\n4️⃣ 상승률 TOP 3 조회 중...")
        top3 = updater.get_top3_gainers()
        
        # 5. 결과 출력
        print("\n" + "=" * 60)
        print("📊 상승률 TOP 3 조회 결과")
        print("=" * 60)
        
        if not top3:
            print("⚠️ 조회 결과가 없습니다.")
        else:
            for item in top3:
                print(f"\n{item['rank']}. {item['ticker']} ({item['name']})")
                print(f"   현재가: ${item['price']:.2f}")
                print(f"   상승률: +{item['rate']}%")
                print(f"   거래량: {item['volume']:,}주")
        
        print("\n" + "=" * 60)
        print("✅ 테스트 완료")
        print("=" * 60)
    
    except FileNotFoundError:
        print("❌ config.yaml 파일을 찾을 수 없습니다.")
        sys.exit(1)
    
    except ImportError as e:
        print(f"❌ 모듈 임포트 실패: {e}")
        print("auth.py 파일이 같은 디렉토리에 있는지 확인하세요.")
        sys.exit(1)
    
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)