# order_monitor.py - 프리마켓용 REST API 폴링 모니터링 시스템

import requests
import json
import logging
import time
import threading
from datetime import datetime, time as dtime, timedelta
from pytz import timezone

logger = logging.getLogger(__name__)

class OrderMonitor:
    """프리마켓/애프터마켓용 주문 체결 모니터링 시스템"""
    
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
        """개별 주문 상태 확인 - 한국투자증권 해외주식용으로 수정"""
        try:
            # ✅ 해외주식 주문체결내역 조회 API URL
            url = f"{self.config['api']['base_url']}/uapi/overseas-stock/v1/trading/inquire-ccnl"
            
            # 토큰 확인 및 갱신
            token = self.token_manager.get_access_token()
            if not token:
                logger.error("유효한 토큰을 가져올 수 없습니다.")
                return None
                
            # ✅ 해외주식용 헤더 설정
            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": self.config['api_key'],
                "appsecret": self.config['api_secret'],
                "tr_id": "TTTS3012R"  # ✅ 해외주식 주문체결내역 조회용 TR ID
            }

            # 🔥 GET 방식 파라미터로 변경
            params = {
                "CANO": self.config['cano'],
                "ACNT_PRDT_CD": self.config['acnt_prdt_cd'],
                "OVRS_EXCG_CD": "NASD",
                "ORD_DT": "",
                "SLL_BUY_DVSN_CD": "00",
                "INQR_DVSN": "00", 
                "STRT_ODNO": order_no,
                "PDNO": "",
                "CCLD_DVSN": "00",
                "ORD_GNO_BRNO": "",
                "ODNO": order_no,
                "INQR_DVSN_3": "00",
                "INQR_DVSN_1": "",
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": ""
            }

            # 🔥 POST -> GET 방식으로 변경
            response = requests.get(url, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            # 응답 상태 확인
            if data.get("rt_cd") != "0":
                logger.error(f"주문 조회 실패: {data.get('msg1', 'Unknown error')}")
                return None
                
            # 해당 주문번호 찾기 - 해외주식 응답 구조에 맞게 수정
            for item in data.get("output", []):
                if item.get("odno") == order_no:  # 주문번호 매칭
                    # ✅ 해외주식 응답 필드명에 맞게 수정
                    ord_status = item.get("ord_stcd", "")  # 주문상태코드
                    ccld_qty = item.get("ccld_qty", "0")  # 체결수량
                    ccld_unpr = item.get("ccld_unpr", "0")  # 체결단가
                    
                    return {
                        'status': ord_status,
                        'filled_qty': int(float(ccld_qty)) if ccld_qty and ccld_qty != "0" else 0,
                        'filled_price': float(ccld_unpr) if ccld_unpr and ccld_unpr != "0" else 0.0
                    }
                    
            # 주문을 찾지 못한 경우
            logger.debug(f"주문번호 {order_no}를 찾지 못했습니다.")
            return None
            
        except requests.exceptions.Timeout:
            logger.warning(f"주문 상태 조회 타임아웃: {order_no}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"주문 상태 조회 네트워크 오류: {e}")
            return None
        except Exception as e:
            logger.error(f"주문 상태 조회 중 오류: {e}")
            return None
    
    def execute_auto_sell(self, order_info, filled_price):
        """자동 매도 주문 실행"""
        try:
            # 매도가 계산 (3% 수익률)
            profit_margin = self.config['trading']['profit_margin']
            sell_price = round(filled_price * (1 + profit_margin), 2)
            
            execution_data = {
                'ticker': order_info['ticker'],
                'quantity': order_info['quantity'],
                'price': filled_price
            }
            
            logger.info(f"🎯 [REST 폴링] 체결 감지: {execution_data['ticker']} ${filled_price} → 자동 매도 ${sell_price}")
            
            # 자동 매도 주문 실행
            success = place_sell_order(self.config, self.token_manager, execution_data, self.telegram_bot)
            
            if success:
                logger.info(f"✅ [REST 폴링] 자동 매도 주문 성공: {execution_data['ticker']}")
            else:
                logger.error(f"❌ [REST 폴링] 자동 매도 주문 실패: {execution_data['ticker']}")
                
            return success
            
        except Exception as e:
            logger.error(f"자동 매도 실행 중 오류: {e}")
            return False
    
    def monitor_orders(self):
        """주문 모니터링 메인 루프"""
        logger.info("🔍 [REST 폴링] 주문 모니터링 시작")
        
        while self.is_running:
            try:
                # 모니터링 중인 주문들 복사 (thread-safe)
                orders_to_check = dict(self.monitoring_orders)
                completed_orders = []
                
                for order_no, order_info in orders_to_check.items():
                    if not self.is_running:
                        break
                    
                    # 최대 시도 횟수 확인
                    order_info['attempts'] += 1
                    if order_info['attempts'] > order_info['max_attempts']:
                        logger.warning(f"⏰ 주문 모니터링 시간 초과: {order_no} (30분 경과)")
                        completed_orders.append(order_no)
                        continue
                    
                    # 주문 상태 확인
                    status_info = self.check_order_status(order_no)
                    if status_info is None:
                        continue
                    
                    # 체결 완료 확인 - 해외주식 상태코드에 맞게 수정
                    if status_info['filled_qty'] > 0 and status_info['filled_price'] > 0:
                        logger.info(f"🎉 [REST 폴링] 체결 완료 감지: {order_no} (체결가: ${status_info['filled_price']}, 체결량: {status_info['filled_qty']})")
                        
                        # 자동 매도 실행
                        self.execute_auto_sell(order_info, status_info['filled_price'])
                        completed_orders.append(order_no)
                        
                    elif order_info['attempts'] % 12 == 0:  # 1분마다 상태 로그
                        elapsed_min = order_info['attempts'] * 5 // 60
                        logger.debug(f"⏳ 체결 대기 중: {order_no} ({elapsed_min}분 경과, 상태: {status_info.get('status', 'Unknown')})")
                
                # 완료된 주문 제거
                for order_no in completed_orders:
                    self.monitoring_orders.pop(order_no, None)
                    logger.info(f"📋 모니터링 목록에서 제거: {order_no}")
                
                # 5초 대기
                if self.is_running:
                    time.sleep(5)
                    
            except Exception as e:
                logger.error(f"주문 모니터링 루프 오류: {e}")
                time.sleep(10)  # 오류 시 10초 대기
                
        logger.info("🔍 [REST 폴링] 주문 모니터링 종료")
    
    def start(self):
        """모니터링 시작"""
        if self.is_running:
            logger.warning("이미 주문 모니터링이 실행 중입니다.")
            return
        
        self.is_running = True
        self.monitor_thread = threading.Thread(target=self.monitor_orders, daemon=True)
        self.monitor_thread.start()
        logger.info("🚀 [REST 폴링] 주문 모니터링 시작됨")
    
    def stop(self):
        """모니터링 중지"""
        if not self.is_running:
            return
        
        self.is_running = False
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
        logger.info("🛑 [REST 폴링] 주문 모니터링 중지됨")
    
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
        logger.warning(f"시간 판별 오류: {e}, 기본값(정규장) 사용")
        return False


def is_market_hours(trading_timezone='US/Eastern'):
    """
    시장 시간 상태 반환
    Returns: 'premarket', 'regular', 'aftermarket', 'closed'
    """
    try:
        tz = timezone(trading_timezone)
        now = datetime.now(tz).time()
        
        premarket_start = dtime(4, 0)   # 04:00 ET
        regular_start = dtime(9, 30)    # 09:30 ET
        regular_end = dtime(16, 0)      # 16:00 ET
        aftermarket_end = dtime(20, 0)  # 20:00 ET
        
        if premarket_start <= now < regular_start:
            return 'premarket'
        elif regular_start <= now < regular_end:
            return 'regular'
        elif regular_end <= now < aftermarket_end:
            return 'aftermarket'
        else:
            return 'closed'
    except Exception as e:
        logger.warning(f"시간 판별 오류: {e}")
        return 'unknown'


def place_sell_order(config, token_manager, execution_data, telegram_bot=None):
    """
    자동 매도 주문 실행 함수
    Args:
        config: 설정 딕셔너리
        token_manager: TokenManager 인스턴스
        execution_data: 체결 데이터 {'ticker', 'quantity', 'price'}
        telegram_bot: TelegramBot 인스턴스 (선택)
    Returns:
        bool: 매도 주문 성공 여부
    """
    import requests
    import json
    import logging
    from datetime import datetime
    
    logger = logging.getLogger(__name__)
    
    try:
        # 매도가 계산
        buy_price = execution_data['price']
        profit_margin = config['trading']['profit_margin']
        sell_price = round(buy_price * (1 + profit_margin), 2)
        
        # 한국투자증권 해외주식 매도 API 호출
        url = f"{config['api']['base_url']}/uapi/overseas-stock/v1/trading/order"
        
        token = token_manager.get_access_token()
        if not token:
            logger.error("❌ 유효한 토큰을 가져올 수 없습니다.")
            return False
            
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": config['api_key'],
            "appsecret": config['api_secret'],
            "tr_id": "JTTT1006U"  # 해외주식 매도주문
        }
        
        # 주문 데이터
        order_data = {
            "CANO": config['cano'],
            "ACNT_PRDT_CD": config['acnt_prdt_cd'],
            "OVRS_EXCG_CD": config['trading']['exchange_code'],  # "NASD"
            "PDNO": execution_data['ticker'],
            "ORD_QTY": str(execution_data['quantity']),
            "OVRS_ORD_UNPR": str(sell_price),
            "ORD_SVR_DVSN_CD": "0",  # 해외주식 주문서버구분코드
            "ORD_DVSN": config['trading']['default_order_type']  # "00" 지정가
        }
        
        # API 요청
        response = requests.post(url, headers=headers, json=order_data, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            
            if data.get("rt_cd") == "0":
                order_no = data.get("output", {}).get("ODNO", "Unknown")
                logger.info(f"✅ 자동 매도 주문 성공: {execution_data['ticker']} {execution_data['quantity']}주 @ ${sell_price} (주문번호: {order_no})")
                
                # 텔레그램 알림
                if telegram_bot:
                    profit_rate = (sell_price - buy_price) / buy_price * 100
                    telegram_bot.send_sell_order_notification(
                        execution_data['ticker'],
                        execution_data['quantity'],
                        buy_price,
                        sell_price,
                        profit_rate
                    )
                
                return True
            else:
                error_msg = data.get("msg1", "Unknown error")
                logger.error(f"❌ 매도 주문 API 오류: {error_msg}")
                if telegram_bot:
                    telegram_bot.send_error_notification(f"매도 주문 실패: {error_msg}")
                return False
        else:
            logger.error(f"❌ HTTP 오류 {response.status_code}: {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"❌ 매도 주문 실행 중 오류: {e}")
        if telegram_bot:
            telegram_bot.send_error_notification(f"매도 주문 오류: {str(e)}")
        return False