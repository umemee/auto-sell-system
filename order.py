# order.py - 수정된 전체 코드 (config 구조 호환성 수정)

import requests
import json
import logging
import time
from datetime import datetime, time as dtime
from pytz import timezone

# 한국투자증권 API 에러 코드 매핑 (더 완성된 버전)
ORDER_ERROR_CODES = {
    # 일반적인 주문 오류
    '40310000': '주문수량이 부족합니다',
    '40320000': '주문가격이 올바르지 않습니다',
    '40330000': '주문 가능 수량을 초과했습니다',
    '40340000': '매수 주문이 불가능한 종목입니다',
    '40350000': '계좌 잔고가 부족합니다',
    '40360000': '주문 가능 시간이 아닙니다',
    '40370000': '종목 정보를 찾을 수 없습니다',
    '40380000': '거래 정지 종목입니다',
    '40390000': '단가 오류입니다',
    # 인증 관련 오류
    '40013': '접근토큰이 만료되었습니다',
    '40014': '접근토큰이 유효하지 않습니다',
    '40015': 'API 키가 유효하지 않습니다',
    # 계좌 관련 오류
    '40020': '계좌번호가 유효하지 않습니다',
    '40021': '계좌 접근 권한이 없습니다',
    # 시스템 오류
    '50000': '시스템 내부 오류입니다',
    '50001': '일시적인 시스템 오류입니다'
}


def is_extended_hours(trading_timezone='US/Eastern'):
    """
    미국 동부시간 기준으로 프리마켓/애프터마켓 시간인지 판별
    정규장: 09:30–16:00 ET
    정규장 외 시간이면 True 반환 (프리/애프터마켓)
    """
    try:
        tz = timezone(trading_timezone)
        now = datetime.now(tz).time()
        regular_start = dtime(9, 30)
        regular_end = dtime(16, 0)
        return not (regular_start <= now <= regular_end)
    except Exception as e:
        logging.getLogger(__name__).warning(f"시간 판별 오류: {e}, 기본값(정규장) 사용")
        return False


def place_sell_order(config, token_manager, execution_data, telegram_bot=None):
    """
    미국 주식 매도 주문 - main.py 호출 방식에 맞게 수정된 함수
    
    Args:
        config: 설정 dict
        token_manager: TokenManager 인스턴스
        execution_data: 체결 데이터 dict {'ticker': str, 'quantity': int, 'price': float}
        telegram_bot: TelegramBot 인스턴스 (선택사항)
    
    Returns:
        bool: 주문 성공 여부
    """
    logger = logging.getLogger(__name__)
    try:
        # execution_data에서 필요한 정보 추출
        ticker = execution_data.get('ticker')
        quantity = execution_data.get('quantity', 0)
        buy_price = execution_data.get('price', 0)
        
        # 데이터 검증
        if not ticker or quantity <= 0 or buy_price <= 0:
            logger.error(f"잘못된 체결 데이터: {execution_data}")
            return False
        
        # 매도가 계산 (3% 수익률)
        profit_margin = config['trading']['profit_margin']
        sell_price = round(buy_price * (1 + profit_margin), 2)
        
        logger.info(f"📊 [{ticker}] 매도 주문 준비: 매수가 ${buy_price:.2f} → 매도가 ${sell_price:.2f} (+{profit_margin*100:.1f}%)")
        
        # 실제 매도 주문 실행
        order_success = _execute_sell_order(config, token_manager, ticker, quantity, sell_price)
        
        # 텔레그램 알림 전송 (성공 시)
        if order_success and telegram_bot:
            try:
                profit_rate = profit_margin * 100
                telegram_bot.send_sell_order_notification(
                    ticker, quantity, buy_price, sell_price, profit_rate
                )
                logger.debug("텔레그램 매도 주문 알림 전송 완료")
            except Exception as e:
                logger.warning(f"텔레그램 알림 전송 실패: {e}")
        
        return order_success
        
    except Exception as e:
        logger.error(f"매도 주문 처리 중 오류: {e}")
        return False


def _execute_sell_order(config, token_manager, ticker, quantity, price):
    """
    실제 매도 주문을 실행하는 내부 함수
    """
    logger = logging.getLogger(__name__)
    max_retries = config['system']['order_retry_attempts']
    
    # 프리마켓/애프터마켓 판별 (수정된 부분)
    extended = is_extended_hours(config['trading']['timezone'])
    
    for attempt in range(max_retries):
        try:
            # 토큰 획득
            token = token_manager.get_access_token()
            if not token:
                logger.error("유효한 토큰을 가져올 수 없습니다.")
                return False
            
            # 주문 데이터 검증
            if not ticker or quantity <= 0 or price <= 0:
                logger.error(f"잘못된 주문 데이터: ticker={ticker}, quantity={quantity}, price={price}")
                return False
            
            # 가격 반올림 (소수점 2자리)
            rounded_price = round(price, 2)
            
            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {token}",
                "appKey": config['api_key'],
                "appSecret": config['api_secret'],
                "tr_id": "JTTT1002U",  # 해외주식 주문
                "custtype": "P"
            }
            
            body = {
                "CANO": config['cano'],
                "ACNT_PRDT_CD": config['acnt_prdt_cd'],
                "OVRS_EXCG_CD": config['trading']['exchange_code'],  # NASD
                "PDNO": ticker.upper(),  # 티커는 대문자로
                "ORD_DVSN": config['trading']['default_order_type'],  # 00: 지정가
                "ORD_QTY": str(quantity),
                "OVRS_ORD_UNPR": str(rounded_price),
                "SLL_BUY_DVSN_CD": "01",  # 01: 매도
                "EXT_HOURS_YN": "Y" if extended else "N"  # 시간외 거래 플래그
            }
            
            url = f"{config['api']['base_url']}/uapi/overseas-stock/v1/trading/order"
            
            # 프리마켓/애프터마켓 로그 추가
            market_type = "(프리/애프터마켓)" if extended else "(정규장)"
            logger.debug(f"주문 요청 시작 {market_type}: {ticker} {quantity}주 @ ${rounded_price}")
            
            response = requests.post(
                url,
                headers=headers,
                data=json.dumps(body),
                timeout=30  # 타임아웃 30초
            )
            
            # HTTP 상태 코드 확인
            if response.status_code != 200:
                logger.error(f"HTTP 오류 ({response.status_code}): {response.text}")
                # 5xx 에러는 재시도
                if 500 <= response.status_code < 600 and attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.info(f"서버 오류로 {wait_time}초 후 재시도합니다.")
                    time.sleep(wait_time)
                    continue
                return False
            
            # API 응답 파싱
            try:
                result = response.json()
            except json.JSONDecodeError as e:
                logger.error(f"JSON 파싱 오류: {e}")
                return False
            
            # API 성공 여부 확인
            rt_cd = result.get("rt_cd", "Unknown")
            if rt_cd == '0':
                # 성공
                order_no = result.get("output", {}).get("ODNO", "N/A")
                logger.info(
                    f"✅ [{ticker}] +{config['trading']['profit_margin']*100:.1f}% 자동 매도 주문 성공! "
                    f"(수량: {quantity}, 가격: ${rounded_price}, 주문번호: {order_no}) {market_type}"
                )
                return True
            else:
                # 실패
                error_msg = ORDER_ERROR_CODES.get(rt_cd, result.get("msg1", "알 수 없는 오류"))
                logger.error(f"❌ [{ticker}] 매도 주문 실패 ({rt_cd}): {error_msg}")
                
                # 토큰 관련 오류인 경우 토큰 갱신 후 재시도
                if rt_cd in ['40013', '40014', '40015'] and attempt < max_retries - 1:
                    logger.info("토큰 만료/무효 오류. 토큰을 갱신하고 재시도합니다.")
                    success = token_manager.get_access_token(force_refresh=True)
                    if success:
                        time.sleep(1)  # 1초 대기 후 재시도
                        continue
                
                # 일시적 시스템 오류인 경우 재시도
                if rt_cd in ['50001'] and attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.info(f"일시적 오류로 {wait_time}초 후 재시도합니다.")
                    time.sleep(wait_time)
                    continue
                
                return False
                
        except requests.exceptions.Timeout:
            logger.warning(f"[{ticker}] 주문 요청 시간 초과 (시도 {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # 지수적 백오프
                continue
                
        except requests.exceptions.ConnectionError as e:
            logger.error(f"[{ticker}] 네트워크 연결 오류: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
                
        except requests.exceptions.RequestException as e:
            logger.error(f"[{ticker}] HTTP 요청 오류: {e}")
            return False
            
        except Exception as e:
            logger.error(f"[{ticker}] 주문 실행 중 예상치 못한 오류: {e}")
            return False
    
    logger.error(f"❌ [{ticker}] 최대 재시도 횟수({max_retries})를 초과하여 주문 실패")
    return False