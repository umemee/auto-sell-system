# order.py - 해외주식 자동매도 시스템 (기획서 v1.0 완전 반영)
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
            "tr_id": "TTTT1006U",    # 해외주식 매도주문 (실전)
            "custtype": "P",          # 개인: P, 법인: B
            "hashkey": hashkey
        }
        
        logger.debug(f"📤 요청 헤더: {headers}")
        
        # API 호출
        url = f"{config['api']['base_url']}/uapi/overseas-stock/v1/trading/order"
        
        logger.info(f"📡 매도 주문 API 호출: {url}")
        
        response = requests.post(url, headers=headers, json=order_data, timeout=15)
        
        # 응답 처리
        logger.debug(f"📥 응답 상태 코드: {response.status_code}")
        logger.debug(f"📥 응답 본문: {response.text}")
        
        if response.status_code != 200:
            logger.error(f"❌ HTTP 오류 {response.status_code}: {response.text}")
            if telegram_bot:
                telegram_bot.send_error_notification(
                    f"매도 주문 HTTP 오류 {response.status_code}"
                )
            return False
        
        # JSON 응답 파싱
        try:
            data = response.json()
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON 파싱 오류: {e}")
            logger.error(f"원본 응답: {response.text}")
            return False
        
        # 성공 여부 확인
        rt_cd = data.get("rt_cd", "")
        msg_cd = data.get("msg_cd", "")
        msg1 = data.get("msg1", "Unknown error")
        
        logger.debug(f"📑 응답 코드: rt_cd={rt_cd}, msg_cd={msg_cd}, msg={msg1}")
        
        if rt_cd == "0":
            # 성공!
            output = data.get("output", {})
            order_no = output.get("ODNO", output.get("ORD_NO", "Unknown"))
            order_time = output.get("ORD_TMD", datetime.now().strftime("%H:%M:%S"))
            
            logger.info(
                f"✅ 자동 매도 주문 성공!\n"
                f"   🏷️ 종목: {ticker}\n"
                f"   📦 수량: {execution_data['quantity']}주\n"
                f"   💰 매도가: ${sell_price}\n"
                f"   📄 주문번호: {order_no}\n"
                f"   ⏰ 주문시간: {order_time}"
            )
            
            # 기획서 6.1: 텔레그램 알림
            if telegram_bot:
                profit_rate = (sell_price - buy_price) / buy_price * 100
                telegram_bot.send_sell_order_notification(
                    ticker,
                    execution_data['quantity'],
                    buy_price,
                    sell_price,
                    profit_rate
                )
            
            return True
        
        else:
            # 실패
            error_msg = msg1 if msg1 else f"오류 코드: {msg_cd}"
            
            # 기획서 4.4: "주문수량이 가능수량보다 큽니다" 오류 처리
            if "가능수량" in error_msg or "수량" in error_msg:
                logger.warning(f"⚠️ 이미 매도된 주식: {error_msg}")
                logger.info("🔄 매도 실패 즉시 포기 (기획서 4.4)")
                # 텔레그램 알림은 보내지만 재시도하지 않음
                if telegram_bot:
                    telegram_bot.send_info_notification(
                        f"매도 대상 없음: {ticker} (이미 매도됨)"
                    )
                return False
            
            # 기타 오류
            logger.error(f"❌ 매도 주문 API 오류: {error_msg}")
            logger.error(f"📄 전체 응답: {json.dumps(data, ensure_ascii=False)}")
            
            # 일반적인 오류 처리
            if "OVRS_EXCG_CD" in error_msg or "거래소" in error_msg:
                logger.error(f"💡 거래소 코드 확인 필요: {exchange_code}")
            
            if "ACNT" in error_msg or "계좌" in error_msg:
                logger.error(f"💡 계좌번호 확인: {config['cano']}-{config['acnt_prdt_cd']}")
            
            if telegram_bot:
                telegram_bot.send_error_notification(f"매도 주문 실패: {error_msg}")
            
            return False
    
    except requests.exceptions.Timeout:
        logger.error("❌ 매도 주문 타임아웃 (15초 초과)")
        if telegram_bot:
            telegram_bot.send_error_notification("매도 주문 타임아웃")
        return False
    
    except requests.exceptions.ConnectionError as e:
        logger.error(f"❌ 네트워크 연결 오류: {e}")
        if telegram_bot:
            telegram_bot.send_error_notification(f"네트워크 오류: {str(e)}")
        return False
    
    except Exception as e:
        logger.error(f"❌ 매도 주문 실행 중 예상치 못한 오류: {e}")
        if telegram_bot:
            telegram_bot.send_error_notification(f"매도 주문 오류: {str(e)}")
        return False


# ============================================================================
# OrderMonitor 클래스 (기획서 3장: 프리마켓용 REST 폴링)
# ============================================================================

class OrderMonitor:
    """
    기획서 3장: 프리마켓용 주문 체결 모니터링 시스템
    
    - 프리마켓(ET 04:00-09:30)에서 REST 폴링으로 매수 체결 감지
    - 정규장(ET 09:30-12:00)에서는 WebSocket 사용
    - 기획서 3.3: 스마트 폴링으로 API 비용 절감 (58%)
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
            # 🆘 한국투자증권 공식 API 엔드포인트
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
        
            # 🆘 한국투자증권 공식 파라미터 (GitHub 확인 완료)
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
                    # 🆘 한국투자증권 공식 필드명 (GitHub 확인 완료)
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