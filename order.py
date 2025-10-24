# order.py - 해외주식 자동매도 시스템 (기획서 v1.1 완전 반영)
# 한국투자증권 공식 API 사용

import requests
import json
import logging
import time
import threading
from datetime import datetime, time as dtime, timedelta
from pytz import timezone

logger = logging.getLogger(__name__)


# ============================================================================
# 시장 시간 판별 함수 (기획서 2.2, 2.3)
# ============================================================================

def is_market_hours(trading_timezone='US/Eastern'):
    """
    기획서 2.2: 시장 시간 상태 반환
    
    운영 시간 (ET 기준):
    - 프리마켓: 04:00-09:30
    - 정규장: 09:30-12:00
    - 수면 모드: 12:00-04:00 (다음날)
    
    Returns: 'premarket', 'regular', 'closed'
    """
    try:
        tz = timezone(trading_timezone)
        now = datetime.now(tz).time()
        
        # 기획서 2.2: 운영 시간 정의
        premarket_start = dtime(4, 0)     # 04:00 ET (한국 17:00)
        regular_start = dtime(9, 30)      # 09:30 ET (한국 22:30)
        system_end = dtime(12, 0)         # 12:00 ET (한국 01:00)
        
        if premarket_start <= now < regular_start:
            return 'premarket'
        elif regular_start <= now < system_end:
            return 'regular'
        else:
            return 'closed'  # 수면 모드
    except Exception as e:
        logger.warning(f"⚠️ 시간 판별 오류: {e}")
        return 'unknown'


def is_extended_hours(trading_timezone='US/Eastern'):
    """
    기획서 2.3: 프리마켓 시간인지 판별
    정규장(09:30-12:00 ET) 외 시간이면 True 반환
    
    Returns:
        bool: 프리마켓 시간이면 True
    """
    try:
        tz = timezone(trading_timezone)
        now = datetime.now(tz).time()
        
        regular_start = dtime(9, 30)
        system_end = dtime(12, 0)
        
        # 정규장이 아니면 True (프리마켓 또는 종료)
        return not (regular_start <= now < system_end)
    except Exception as e:
        logger.warning(f"⚠️ 시간 판별 오류: {e}, 기본값(정규장) 사용")
        return False


def should_system_run(trading_timezone='US/Eastern'):
    """
    기획서 2.2: 시스템 운영 여부 확인
    
    Returns:
        bool: ET 04:00-12:00 사이면 True
    """
    try:
        tz = timezone(trading_timezone)
        now = datetime.now(tz).time()
        
        start_time = dtime(4, 0)   # 04:00 ET
        end_time = dtime(12, 0)    # 12:00 ET
        
        return start_time <= now < end_time
    except Exception as e:
        logger.error(f"❌ 시스템 운영 시간 체크 오류: {e}")
        return False


# ============================================================================
# 🔴 [v1.1 신규] 보유 종목 조회 함수 (기획서 5.1절)
# ============================================================================

def get_holdings(config, token_manager, exchange_code='NASD', currency_code='USD'):
    """
    해외주식 잔고 조회 (기획서 5.1절 - WebSocket 다중 종목 구독용)
    
    한국투자증권 공식 API:
    - TR_ID: TTTS3012R (실전) / VTTS3012R (모의)
    - 엔드포인트: /uapi/overseas-stock/v1/trading/inquire-balance
    
    Args:
        config: 설정 딕셔너리
        token_manager: TokenManager 인스턴스
        exchange_code: 거래소 코드 (기본값: 'NASD' 미국전체)
        currency_code: 통화 코드 (기본값: 'USD')
    
    Returns:
        list: 보유 종목 리스트
        [
            {
                'ticker': 'AAPL',           # 종목코드
                'pdno': 'AAPL',             # 상품번호 (동일)
                'quantity': 10,             # 보유수량
                'avg_price': 150.50,        # 매입평균가격
                'current_price': 155.20,    # 현재가
                'profit_loss': 47.00,       # 평가손익
                'profit_rate': 3.12,        # 수익률 (%)
                'eval_amt': 1552.00,        # 평가금액
                'purchase_amt': 1505.00     # 매입금액
            },
            ...
        ]
    
    Example:
        >>> holdings = get_holdings(config, token_manager)
        >>> print(f"보유 종목: {len(holdings)}개")
        >>> for h in holdings:
        ...     print(f"{h['ticker']}: {h['quantity']}주 @ ${h['avg_price']}")
    """
    try:
        # API 엔드포인트
        url = f"{config['api']['base_url']}/uapi/overseas-stock/v1/trading/inquire-balance"
        
        # 액세스 토큰 확인
        token = token_manager.get_access_token()
        if not token:
            logger.error("❌ 보유 종목 조회: 액세스 토큰 없음")
            return []
        
        # 헤더 설정
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": config["api_key"],
            "appsecret": config["api_secret"],
            "tr_id": "TTTS3012R",  # 실전투자용
            "custtype": "P"
        }
        
        # 파라미터 설정 (한국투자증권 공식)
        params = {
            "CANO": config["cano"],              # 종합계좌번호
            "ACNT_PRDT_CD": config["acnt_prdt_cd"],  # 계좌상품코드
            "OVRS_EXCG_CD": exchange_code,       # 해외거래소코드 (NASD: 미국전체)
            "TR_CRCY_CD": currency_code,         # 거래통화코드 (USD: 미국달러)
            "CTX_AREA_FK200": "",                # 연속조회검색조건200 (첫 조회시 공백)
            "CTX_AREA_NK200": ""                 # 연속조회키200 (첫 조회시 공백)
        }
        
        logger.info(f"📋 보유 종목 조회 시작: {exchange_code} ({currency_code})")
        
        # GET 요청
        response = requests.get(url, headers=headers, params=params, timeout=15)
        
        if response.status_code != 200:
            logger.error(f"❌ 보유 종목 조회 HTTP 오류: {response.status_code}")
            logger.error(f"응답: {response.text}")
            return []
        
        # JSON 파싱
        data = response.json()
        
        # 정상 응답 확인
        rt_cd = data.get("rt_cd", "")
        if rt_cd != "0":
            msg1 = data.get("msg1", "알 수 없는 오류")
            logger.warning(f"⚠️ 보유 종목 조회 실패: {msg1} (rt_cd={rt_cd})")
            return []
        
        # output2: 보유 종목 리스트
        output2 = data.get("output2", [])
        
        if not output2:
            logger.info("📋 보유 종목 없음")
            return []
        
        # 종목 정보 파싱
        holdings = []
        for item in output2:
            try:
                # 한국투자증권 공식 필드명
                ticker = item.get("ovrs_pdno", "").strip()  # 해외상품번호 (종목코드)
                quantity_str = item.get("ovrs_cblc_qty", "0")  # 해외잔고수량
                avg_price_str = item.get("pchs_avg_pric", "0")  # 매입평균가격
                current_price_str = item.get("now_pric2", "0")  # 현재가격2
                profit_loss_str = item.get("frcr_evlu_pfls_amt", "0")  # 외화평가손익금액
                profit_rate_str = item.get("evlu_pfls_rt", "0")  # 평가손익율
                eval_amt_str = item.get("ovrs_stck_evlu_amt", "0")  # 해외주식평가금액
                purchase_amt_str = item.get("frcr_pchs_amt1", "0")  # 외화매입금액1
                
                # 타입 변환
                quantity = int(quantity_str) if quantity_str else 0
                avg_price = float(avg_price_str) if avg_price_str else 0.0
                current_price = float(current_price_str) if current_price_str else 0.0
                profit_loss = float(profit_loss_str) if profit_loss_str else 0.0
                profit_rate = float(profit_rate_str) if profit_rate_str else 0.0
                eval_amt = float(eval_amt_str) if eval_amt_str else 0.0
                purchase_amt = float(purchase_amt_str) if purchase_amt_str else 0.0
                
                # 유효한 종목만 추가 (수량 > 0)
                if ticker and quantity > 0:
                    holding_info = {
                        'ticker': ticker,
                        'pdno': ticker,  # 동일
                        'symbol': ticker,  # 호환성
                        'stock_code': ticker,  # 호환성
                        'quantity': quantity,
                        'avg_price': avg_price,
                        'current_price': current_price,
                        'profit_loss': profit_loss,
                        'profit_rate': profit_rate,
                        'eval_amt': eval_amt,
                        'purchase_amt': purchase_amt,
                        'exchange_code': exchange_code,
                        'currency_code': currency_code
                    }
                    holdings.append(holding_info)
                    
                    logger.debug(f"📊 {ticker}: {quantity}주 @ ${avg_price:.2f} "
                               f"(현재가: ${current_price:.2f}, 손익: {profit_rate:+.2f}%)")
            
            except (ValueError, TypeError) as e:
                logger.warning(f"⚠️ 종목 정보 파싱 오류: {e}, 항목: {item}")
                continue
        
        logger.info(f"✅ 보유 종목 조회 완료: {len(holdings)}개")
        
        # 수익률 기준 내림차순 정렬 (WebSocket 구독 우선순위용)
        holdings.sort(key=lambda x: x.get('profit_rate', 0), reverse=True)
        
        return holdings
    
    except requests.exceptions.Timeout:
        logger.error("❌ 보유 종목 조회 타임아웃 (15초)")
        return []
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ 보유 종목 조회 네트워크 오류: {e}")
        return []
    except Exception as e:
        logger.error(f"❌ 보유 종목 조회 오류: {e}")
        import traceback
        traceback.print_exc()
        return []


# ============================================================================
# HashKey 생성 함수 (한국투자증권 공식)
# ============================================================================

def get_hash_key(config, token_manager, order_data):
    """
    한국투자증권 주문용 HashKey 생성
    
    Args:
        config: 설정 딕셔너리
        token_manager: TokenManager 인스턴스
        order_data: 주문 데이터 딕셔너리
    
    Returns:
        str: HashKey 또는 None (실패 시)
    """
    try:
        url = f"{config['api']['base_url']}/uapi/hashkey"
        token = token_manager.get_access_token()
        
        if not token:
            logger.error("❌ HashKey 생성: 토큰 없음")
            return None
        
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": config['api_key'],
            "appsecret": config['api_secret']
        }
        
        response = requests.post(url, headers=headers, json=order_data, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            hashkey = data.get("HASH")
            if hashkey:
                logger.debug(f"✅ HashKey 생성 성공: {hashkey[:20]}...")
                return hashkey
            else:
                logger.error(f"❌ HashKey 응답에 HASH 없음: {data}")
                return None
        else:
            logger.error(f"❌ HashKey HTTP 오류 {response.status_code}: {response.text}")
            return None
    
    except Exception as e:
        logger.error(f"❌ HashKey 생성 오류: {e}")
        return None


# ============================================================================
# 자동 매도 주문 실행 함수 (핵심!)
# ============================================================================

def place_sell_order(config, token_manager, execution_data, telegram_bot=None):
    """
    기획서 4장: 자동 매도 주문 실행 함수
    
    Args:
        config: 설정 딕셔너리
        token_manager: TokenManager 인스턴스
        execution_data: 체결 데이터 {'ticker', 'quantity', 'price'}
        telegram_bot: TelegramBot 인스턴스 (선택)
    
    Returns:
        bool: 매도 주문 성공 여부
        
    매도 실패 처리 (기획서 4.4):
        - "주문수량이 가능수량보다 큽니다" 오류 → 즉시 포기
        - 재시도 없음
        - 텔레그램 알림만
    """
    try:
        # 기획서 4.1: 매도가 계산 (수익률 3% 고정)
        buy_price = execution_data['price']
        
        # 기획서 4.2: config에서 수익률 가져오기
        # order_settings.target_profit_rate: 3.0 (%)
        target_profit_rate = config.get('order_settings', {}).get('target_profit_rate', 3.0)
        profit_margin = target_profit_rate / 100  # 3.0 → 0.03
        
        sell_price = round(buy_price * (1 + profit_margin), 2)
        
        logger.info(f"🎯 매도 주문 준비: {execution_data['ticker']} "
                   f"{execution_data['quantity']}주 @ ${sell_price} "
                   f"(매수가: ${buy_price}, 목표 수익: +{target_profit_rate}%)")
        
        # 거래소 코드 결정
        exchange_code = config.get('order_settings', {}).get('exchange_code', 'NASD')
        
        # 티커로 거래소 자동 판별 (선택적)
        ticker = execution_data['ticker']
        if ticker in ['AAPL', 'MSFT', 'GOOGL', 'TSLA', 'NVDA', 'AMZN']:
            exchange_code = 'NASD'  # NASDAQ
        
        logger.debug(f"📊 거래소 코드: {exchange_code}")
        
        # 주문 데이터 생성 (한국투자증권 공식 파라미터)
        order_data = {
            "CANO": config['cano'],
            "ACNT_PRDT_CD": config['acnt_prdt_cd'],
            "OVRS_EXCG_CD": exchange_code,
            "PDNO": ticker,
            "ORD_QTY": str(execution_data['quantity']),
            "OVRS_ORD_UNPR": str(sell_price),
            "CTAC_TLNO": "",
            "MGCO_APTM_ODNO": "",
            "SLL_TYPE": "00",         # 매도 유형 (00: 지정가)
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00"
        }

        safe_order_data = order_data.copy()
        if 'CANO' in safe_order_data:
            cano = safe_order_data['CANO']
            safe_order_data['CANO'] = cano[:4] + '****'  # 앞 4자리만 표시

        logger.debug(f"📤 주문 데이터: {json.dumps(order_data, ensure_ascii=False)}")
        
        # HashKey 생성 (필수!)
        hashkey = get_hash_key(config, token_manager, order_data)

        if not hashkey:
            logger.error("❌ HashKey 생성 실패, 주문 중단")
            if telegram_bot:
                telegram_bot.send_error_notification("매도 주문 실패: HashKey 생성 불가")
            return False
        
        # 액세스 토큰 확인
        token = token_manager.get_access_token()
        if not token:
            logger.error("❌ 유효한 토큰을 가져올 수 없습니다.")
            if telegram_bot:
                telegram_bot.send_error_notification("매도 주문 실패: 토큰 없음")
            return False
        
        # API 요청 헤더 설정
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": config['api_key'],
            "appsecret": config['api_secret'],
            "tr_id": "TTTT1006U",  # 해외주식 매도 주문
            "custtype": "P",
            "hashkey": hashkey
        }
        
        # 매도 주문 API 호출
        sell_url = f"{config['api']['base_url']}/uapi/overseas-stock/v1/trading/order"
        
        logger.info(f"📤 매도 주문 전송 중: {ticker} {execution_data['quantity']}주 @ ${sell_price}")
        
        response = requests.post(sell_url, headers=headers, json=order_data, timeout=10)
        
        # 응답 확인
        if response.status_code == 200:
            result = response.json()
            rt_cd = result.get("rt_cd", "")
            
            if rt_cd == "0":
                order_no = result.get("output", {}).get("ODNO", "")
                logger.info(f"✅ 매도 주문 성공: {ticker} (주문번호: {order_no})")
                
                # 텔레그램 알림
                if telegram_bot:
                    message = f"""✅ 자동 매도 주문 성공

📊 종목: {ticker}
📈 수량: {execution_data['quantity']}주
💰 매도가: ${sell_price}
🎯 목표 수익률: +{target_profit_rate}%
📝 주문번호: {order_no}"""
                    telegram_bot.send_message(message)
                
                return True
            else:
                # 기획서 4.4: 매도 실패 처리
                msg1 = result.get("msg1", "알 수 없는 오류")
                logger.error(f"❌ 매도 주문 실패: {msg1} (rt_cd={rt_cd})")
                
                # "주문수량이 가능수량보다 큽니다" 오류 → 즉시 포기
                if "가능수량" in msg1 or "주문수량" in msg1:
                    logger.warning(f"⚠️ 이미 매도된 주식으로 판단, 재시도 없이 무시")
                    return False
                
                # 텔레그램 알림
                if telegram_bot:
                    telegram_bot.send_error_notification(f"매도 주문 실패: {msg1}")
                
                return False
        else:
            logger.error(f"❌ 매도 주문 HTTP 오류: {response.status_code}")
            logger.error(f"응답: {response.text}")
            
            if telegram_bot:
                telegram_bot.send_error_notification(f"매도 주문 HTTP 오류: {response.status_code}")
            
            return False
    
    except Exception as e:
        logger.error(f"❌ 매도 주문 실행 중 오류: {e}")
        
        if telegram_bot:
            telegram_bot.send_error_notification(f"매도 주문 오류: {str(e)}")
        
        return False


# ============================================================================
# 주문 모니터링 클래스 (프리마켓용 REST 폴링)
# ============================================================================

class OrderMonitor:
    """
    기획서 3장: 프리마켓 REST 폴링 모니터
    
    정규장은 WebSocket 사용, 프리마켓은 REST 폴링
    """
    
    def __init__(self, config, token_manager, telegram_bot=None):
        self.config = config
        self.token_manager = token_manager
        self.telegram_bot = telegram_bot
        self.monitoring_orders = {}  # {order_no: order_info}
        self.is_running = False
        self.monitor_thread = None

    def add_order_to_monitor(self, order_no, ticker, quantity, buy_price):
        """모니터링할 주문 추가"""
        order_info = {
            'ticker': ticker,
            'quantity': quantity,
            'buy_price': buy_price,
            'created_at': datetime.now(),
            'attempts': 0,
            'max_attempts': 360  # 30분 (5초 간격 × 360회)
        }
        
        self.monitoring_orders[order_no] = order_info
        logger.info(f"📝 주문 모니터링 등록: {order_no} ({ticker} {quantity}주 @ ${buy_price})")

    def check_order_status(self, order_no):
        """
        기획서 3장: 해외주식 주문/체결내역 조회 (REST 폴링)
        
        한국투자증권 공식 API:
        - TR_ID: TTTS3035R (실전투자)
        - 엔드포인트: /uapi/overseas-stock/v1/trading/inquire-ccnl
        
        Returns:
            dict: 체결 정보 또는 None
        """
        try:
            # 한국투자증권 공식 API 엔드포인트
            url = f"{self.config['api']['base_url']}/uapi/overseas-stock/v1/trading/inquire-ccnl"
        
            # 액세스 토큰 확인
            token = self.token_manager.get_access_token()
            if not token:
                logger.error("❌ 액세스 토큰 없음")
                return None
    
            # 헤더 설정
            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": self.config["api_key"],
                "appsecret": self.config["api_secret"],
                "tr_id": "TTTS3035R",  # 실전투자용
                "custtype": "P"
            }
        
            # 파라미터 설정
            today = datetime.now().strftime("%Y%m%d")
        
            # 한국투자증권 공식 파라미터 (GitHub 확인 완료)
            params = {
                "CANO": self.config["cano"],
                "ACNT_PRDT_CD": self.config["acnt_prdt_cd"],
                "PDNO": "",                    # 전종목
                "ORD_STRT_DT": today,
                "ORD_END_DT": today,
                "SLL_BUY_DVSN": "02",          # 매수만
                "CCLD_NCCS_DVSN": "01",        # 체결만
                "OVRS_EXCG_CD": "NASD",
                "SORT_SQN": "DS",
                "ORD_DT": "",
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "CTX_AREA_NK200": "",
                "CTX_AREA_FK200": ""
            }
        
            # GET 요청
            response = requests.get(url, headers=headers, params=params, timeout=10)
        
            if response.status_code != 200:
                logger.error(f"❌ 주문조회 HTTP 오류: {response.status_code}")
                return None
        
            # JSON 파싱
            data = response.json()
        
            # 정상 응답 확인
            if data.get("rt_cd") != "0":
                logger.warning(f"⚠️ 주문조회 실패: {data.get('msg1', '')}")
                return None
        
            # 주문 내역에서 해당 주문번호 찾기
            orders = data.get("output", [])
            if not orders:
                return None
        
            # 주문번호로 매칭
            for order in orders:
                if order.get("odno") == order_no:
                    # 한국투자증권 공식 필드명 (GitHub 확인 완료)
                    ccld_qty = order.get("ft_ccld_qty", "0")       # FT체결수량
                    ccld_unpr = order.get("ft_ccld_unpr3", "0")   # FT체결단가3
                
                    logger.debug(f"🔍 주문 발견: {order_no} - 체결량: {ccld_qty}")
                
                    return {
                        'status': '02',  # 체결완료
                        'filled_qty': int(ccld_qty) if ccld_qty else 0,
                        'filled_price': float(ccld_unpr) if ccld_unpr else 0.0,
                        'order_data': order
                    }
        
            return None
    
        except Exception as e:
            logger.error(f"❌ check_order_status() 오류: {e}")
            return None
            
    def execute_auto_sell(self, order_info, filled_price):
        """기획서 4장: 자동 매도 주문 실행"""
        try:
            execution_data = {
                'ticker': order_info['ticker'],
                'quantity': order_info['quantity'],
                'price': filled_price
            }
            
            logger.info(f"🎯 체결 감지: {execution_data['ticker']} ${filled_price}")
            
            # 자동 매도 주문 실행
            success = place_sell_order(
                self.config,
                self.token_manager,
                execution_data,
                self.telegram_bot
            )
            
            return success
        
        except Exception as e:
            logger.error(f"❌ 자동 매도 실행 중 오류: {e}")
            return False

    def monitor_orders(self):
        """주문 모니터링 메인 루프 (기획서 3장: 스마트 폴링)"""
        logger.info("🔍 주문 모니터링 시작 (프리마켓 REST 폴링)")
        
        while self.is_running:
            try:
                # 기획서 2.2: 시스템 운영 시간 체크
                if not should_system_run():
                    logger.info("🌙 시스템 운영 시간 종료 (ET 12:00), 모니터링 중지")
                    self.stop()
                    break
                
                orders_to_check = dict(self.monitoring_orders)
                completed_orders = []
                
                for order_no, order_info in orders_to_check.items():
                    if not self.is_running:
                        break
                    
                    # 최대 시도 횟수 확인
                    order_info['attempts'] += 1
                    if order_info['attempts'] > order_info['max_attempts']:
                        logger.warning(f"⏰ 주문 모니터링 시간 초과: {order_no}")
                        completed_orders.append(order_no)
                        continue
                    
                    # 주문 상태 확인
                    status_info = self.check_order_status(order_no)
                    
                    if status_info is None:
                        continue
                    
                    # 체결 완료 확인
                    if status_info['filled_qty'] > 0 and status_info['filled_price'] > 0:
                        logger.info(
                            f"🎉 체결 완료: {order_no} "
                            f"(체결가: ${status_info['filled_price']}, "
                            f"체결량: {status_info['filled_qty']})"
                        )
                        
                        # 자동 매도 실행
                        self.execute_auto_sell(order_info, status_info['filled_price'])
                        completed_orders.append(order_no)
                
                # 완료된 주문 제거
                for order_no in completed_orders:
                    self.monitoring_orders.pop(order_no, None)
                
                # 기획서 3.2: 스마트 폴링 간격 (5초 기본)
                if self.is_running:
                    time.sleep(5)
            
            except Exception as e:
                logger.error(f"❌ 주문 모니터링 루프 오류: {e}")
                time.sleep(10)
        
        logger.info("🛑 주문 모니터링 종료")

    def start(self):
        """모니터링 시작"""
        if self.is_running:
            logger.warning("⚠️ 이미 주문 모니터링이 실행 중입니다.")
            return
        
        self.is_running = True
        self.monitor_thread = threading.Thread(target=self.monitor_orders, daemon=True)
        self.monitor_thread.start()
        logger.info("🚀 주문 모니터링 시작됨 (프리마켓 REST 폴링)")

    def stop(self):
        """모니터링 중지"""
        if not self.is_running:
            return
        
        self.is_running = False
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
        logger.info("🛑 주문 모니터링 중지됨")

    def get_monitoring_count(self):
        """현재 모니터링 중인 주문 수"""
        return len(self.monitoring_orders)

    def clear_old_orders(self):
        """24시간 이상된 주문 정리"""
        cutoff_time = datetime.now() - timedelta(hours=24)
        old_orders = [
            order_no for order_no, order_info in self.monitoring_orders.items()
            if order_info['created_at'] < cutoff_time
        ]
        
        for order_no in old_orders:
            self.monitoring_orders.pop(order_no, None)
            logger.info(f"🗑️ 오래된 주문 제거: {order_no}")