import json
import logging
import ssl
import threading
import time
import uuid
from datetime import datetime, time as dtime, timedelta
from pytz import timezone
from websocket import WebSocketApp

logger = logging.getLogger(__name__)

class WebSocketClient:
    """
    한국투자증권 WebSocket 클라이언트 - 완전 안정화 버전
    """

    def __init__(self, config, token_manager, message_handler):
        self.config = config
        self.token_manager = token_manager
        self.message_handler = message_handler

        # WebSocket 연결 상태
        self.ws = None
        self.connected = False
        self.subscribed = False
        self.reconnect_count = 0
        self.max_reconnects = 10
        self.is_running = False

        # 설정값
        self.ws_url = self._fix_websocket_url(self.config['api'].get('websocket_url'))
        self.custtype = self.config.get('custtype', 'P')
        self.tr_type = self.config.get('trtype', '1')
        self.default_symbol = self.config.get('trading', {}).get('default_symbol', 'AAPL')

        # 자동 갱신 타이머
        self.last_approval_key_refresh = time.time()
        self.approval_key_refresh_interval = 1800  # 30분마다

        logger.info(f"🔧 WebSocket URL 설정: {self.ws_url}")
        logger.info(f"✅ WebSocket 클라이언트 초기화: {self.ws_url}")

    def _fix_websocket_url(self, base_url):
        logger.info(f"WebSocket URL: {base_url}")
        return base_url

    def _is_regular_market(self):
        """
        미국 정규장 시간인지 확인 (ET 09:30-16:00)
        Returns: bool - 정규장이면 True
        """
        try:
            et_tz = timezone('US/Eastern')
            et_now = datetime.now(et_tz).time()
            
            regular_start = dtime(9, 30)  # 09:30 ET
            regular_end = dtime(16, 0)    # 16:00 ET
            
            is_regular = regular_start <= et_now <= regular_end
            
            if not is_regular:
                logger.info(f"🌙 현재는 정규장이 아닙니다 (ET {et_now.strftime('%H:%M')}). "
                          f"정규장: {regular_start.strftime('%H:%M')}-{regular_end.strftime('%H:%M')}")
            
            return is_regular
        except Exception as e:
            logger.warning(f"시간 판별 오류: {e}, 기본값(정규장) 사용")
            return True  # 오류 시 연결 허용

    def _create_subscribe_message(self, symbol=None):
        """
        한국투자증권 WebSocket 실시간 체결 구독 요청 메시지 생성
        symbol : 구독할 종목 코드 (예: 'AAPL')
        """
        approval_key = self.token_manager.get_websocket_approval_key()
        if not approval_key:
            logger.error("❌ WebSocket 승인키 없음")
            return None

        tr_key = symbol or self.default_symbol  # ✅ 종목 코드 직접 사용

        subscribe_message = {
            "header": {
                "approval_key": approval_key,
                "custtype": self.custtype,  # 개인: 'P'
                "trtype": self.tr_type,     # 구독 요청: '1'
                "content-type": "utf-8"
            },
            "body": {
                "input": {
                    "tr_id": "H0STCNI0",          # 해외주식 체결통보 TR
                    "tr_key": tr_key,             # 서버가 요구하는 필수값
                    "pdno": symbol or self.default_symbol  # 구독할 종목코드
                }
            }
        }

        logger.info(f"📡 구독 메시지 생성 완료 (tr_key={tr_key}, symbol={symbol or self.default_symbol})")
        return json.dumps(subscribe_message)

    def _refresh_approval_key_if_needed(self):
        """
        승인키 갱신 - 오류 발생 시에만 사용
    
        ⚠️ 주의: 승인키를 갱신하면 기존 WebSocket 세션이 끊깁니다.
        따라서 정상 동작 중에는 갱신하지 않고, 오류 발생 시에만 수동으로 호출합니다.
        """
        try:
            logging.info("🔑 승인키 강제 갱신 시도 (오류 복구용)")
        
            # ✅ force_refresh=True로 새 승인키 발급
            new_key = self.token_manager.get_websocket_approval_key(force_refresh=True)
        
            if new_key:
                logger.info("🔑 승인키 강제 갱신 완료 (WebSocket 재연결 필요)")
                self.last_approval_key_refresh = time.time()
            else:
                logger.error("❌ 승인키 갱신 실패")
        except Exception as e:
            logger.error(f"❌ 승인키 갱신 중 오류: {e}")

    def subscribe(self, symbol=None):
        """
        구독 요청 전송 메서드
        """
        if not self.ws or not self.connected:
            logger.error("❌ WS 연결이 없거나 연결 상태가 아닙니다. 구독 전송 불가")
            return

        msg = self._create_subscribe_message(symbol)
        if msg:
            try:
                self.ws.send(msg)
                logger.info(f"▶ WebSocket 구독 메시지 전송 (raw): {msg}")
            except Exception as e:
                logger.error(f"❌ 구독 메시지 전송 오류: {e}", exc_info=True)
        else:
            logger.error("❌ 구독 메시지 생성 실패")

    def on_open(self, ws):
        logger.info("🚀 WebSocket 연결 성공")
        self.connected = True
        self.reconnect_count = 0
        
        # 자동 구독
        self.subscribe(self.default_symbol)
        
        # ✅ 개선사항 1: 구독 확인 후 재시도 로직
        def check_subscription():
            time.sleep(5)  # 5초 대기 후 구독 상태 확인
            if not self.subscribed and self.connected:
                logger.warning("⚠️ 구독 미확인 상태, 재시도 중...")
                self.subscribe(self.default_symbol)
                
                # 추가 재시도 (10초 후 한 번 더)
                time.sleep(10)
                if not self.subscribed and self.connected:
                    logger.warning("⚠️ 구독 재시도 2차 시도")
                    self.subscribe(self.default_symbol)
        
        threading.Thread(target=check_subscription, daemon=True).start()

    def on_message(self, ws, message):
        try:
            # 구독 성공 확인 메시지
            if "SUBSCRIBE SUCCESS" in message or "SUBSCRIBED" in message:
                logger.info("✅ WebSocket 구독 성공")
                self.subscribed = True
                return

            # PING/PONG 처리
            if "PINGPONG" in message or "PONG" in message:
                logger.debug(f"▶ WebSocket PING/PONG: {message}")
                return

            # 승인키 관련 오류 감지 시 자동 갱신
            if "approval" in message.lower() and "error" in message.lower():
                logger.warning("⚠️ 승인키 관련 오류 감지, 자동 갱신 시도")
                self._refresh_approval_key_if_needed()
                return

            # 본문 처리
            logger.debug(f"📡 WebSocket 수신 원본 메시지: {message}")
            data = json.loads(message)
            logger.debug(f"📑 WebSocket 파싱 데이터: {data}")

            # 오류 응답 처리
            body = data.get("body", {})
            rt_cd = body.get("rt_cd", "")
            if rt_cd == "9":  # 오류 코드
                msg1 = body.get("msg1", "")
                logger.warning(f"⚠️ WebSocket 서버 오류: {msg1}")
                
                # tr_key 오류면 구독 재시도
                if "tr_key" in msg1.lower():
                    logger.info("🔄 tr_key 오류로 인한 구독 재시도")
                    time.sleep(2)
                    self.subscribe(self.default_symbol)
                return

            # 정상 데이터 처리
            header = data.get("header", {})
            tr_id = header.get("tr_id", "")
            if tr_id == "H0STCNI0":
                self._handle_execution_message(body)
                
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON 파싱 오류: {e}, 원본 메시지: {message}")
        except Exception as e:
            logger.error(f"❌ on_message 처리 중 예외: {e}", exc_info=True)

    def _handle_execution_message(self, body):
        try:
            output = body.get("output", {})
            order_type = output.get("sll_buy_dvsn_cd")
            if order_type != "02":  # 매수 주문만 처리
                return

            ticker = output.get("pdno", "").strip()
            qty_str = output.get("ccld_qty") or output.get("ord_qty") or "0"
            price_str = output.get("ccld_unpr") or output.get("ord_unpr") or "0"

            try:
                quantity = int(qty_str)
                price = float(price_str)
            except (ValueError, AttributeError):
                logger.warning(f"수량/단가 파싱 실패: qty={qty_str}, price={price_str}")
                return

            if quantity <= 0 or price <= 0:
                logger.warning(f"유효하지 않은 체결 정보: {ticker} qty={quantity}, price={price}")
                return

            execution_data = {
                'ticker': ticker,
                'quantity': quantity,
                'price': price,
                'ordertype': 'buy',
                'timestamp': datetime.now(),
                'source': 'websocket'
            }

            logger.info(f"📈 WebSocket 체결 감지: {ticker} {quantity}주 @ ${price:.2f}")
            if self.message_handler:
                self.message_handler(execution_data)
                
        except Exception as e:
            logger.error(f"❌ handle_execution_message 오류: {e}", exc_info=True)

    def on_error(self, ws, error):
        logger.error(f"❌ WebSocket 오류: {error}")
        self.connected = False
        self.subscribed = False

    def on_close(self, ws, close_status_code, close_msg):
        # ✅ 개선사항 2: 종료 사유 코드별 상세 로그
        reason_map = {
            1000: "정상 종료",
            1001: "서버 종료",
            1006: "비정상 종료 (네트워크 문제)",
            1008: "정책 위반으로 인한 종료",
            4000: "인증 실패 또는 승인키 오류",
            4001: "잘못된 요청 형식",
            4002: "구독 한도 초과"
        }
        
        reason = reason_map.get(close_status_code, f"알 수 없는 종료 사유 (코드: {close_status_code})")
        logger.warning(f"⚠️ WebSocket 연결 해제 ({close_status_code}) - {reason}")
        
        if close_msg:
            logger.warning(f"🔍 서버 메시지: {close_msg}")
            
        # 승인키 관련 오류 시 갱신 시도
        if close_status_code in [4000, 4001]:
            logger.info("🔑 인증 오류로 인한 승인키 갱신 시도")
            self._refresh_approval_key_if_needed()

        self.connected = False
        self.subscribed = False

    def start(self):
        if self.is_running:
            logger.warning("WebSocket 이미 실행 중")
            return

        # ✅ 개선사항 4: 프리마켓/정규장 구분 로직
        if not self._is_regular_market():
            logger.info("🌙 현재는 정규장이 아닙니다. WebSocket 대신 REST 폴링 모드 사용 권장.")
            return

        self.is_running = True
        self.reconnect_count = 0

        def run_loop():
            while self.is_running and self.reconnect_count < self.max_reconnects:
                try:
                    
                    approval_key = self.token_manager.get_websocket_approval_key()
                    if not approval_key:
                        logger.warning("❌ 승인키 없음, 10초 후 재시도")
                        time.sleep(10)
                        continue

                    logger.info(f"🔄 WebSocket 연결 시도 {self.reconnect_count+1}/{self.max_reconnects}")
                    self.ws = WebSocketApp(
                        self.ws_url,
                        on_open=self.on_open,
                        on_message=self.on_message,
                        on_error=self.on_error,
                        on_close=self.on_close
                    )
                    
                    self.ws.run_forever(
                        sslopt={"cert_reqs": ssl.CERT_NONE},
                        ping_interval=60,
                        ping_timeout=10,
                        ping_payload="PING"
                    )
                    
                except Exception as e:
                    logger.error(f"❌ WebSocket run_loop 예외: {e}", exc_info=True)
                finally:
                    self.connected = False
                    self.subscribed = False
                    self.reconnect_count += 1

                    if self.is_running and self.reconnect_count < self.max_reconnects:
                        # ✅ 시장 상태에 따른 적응형 재연결 지연
                        if not self._is_regular_market():
                           # 프리마켓/장 마감: 5분 대기 (AWS 비용 절감)
                            delay = 300  # 5분
                            logger.info(f"🌙 정규장 아님 - 재연결 대기 {delay}초 (5분)")
                        else:
                            # 정규장: 빠른 재연결 (5초, 10초, 15초...)
                            delay = min(5 * self.reconnect_count, 60)
                            logger.info(f"⏳ 재접속 대기 {delay}초")
    
                        time.sleep(delay)

        thread = threading.Thread(target=run_loop, daemon=True)
        thread.start()
        logger.info("🚀 한국투자증권 WebSocket 클라이언트 시작")

    def stop(self):
        if not self.is_running:
            return
            
        self.is_running = False
        if self.ws:
            try:
                self.ws.close()
            except Exception as e:
                logger.warning(f"WebSocket 종료 중 오류: {e}")
        logger.info("🛑 WebSocket 클라이언트 중지")

    def is_connected(self):
        """연결 및 구독 상태 확인"""
        return self.connected and self.subscribed

    def get_status(self):
        """상세 상태 정보 반환"""
        return {
            'connected': self.connected,
            'subscribed': self.subscribed,
            'running': self.is_running,
            'reconnect_count': self.reconnect_count,
            'url': self.ws_url,
            'symbol': self.default_symbol,
            'last_approval_refresh': datetime.fromtimestamp(self.last_approval_key_refresh).strftime('%Y-%m-%d %H:%M:%S'),
            'is_regular_market': self._is_regular_market()
        }

    def force_reconnect(self):
        """강제 재연결"""
        logger.info("🔄 강제 재연결 시도")
        if self.ws:
            try:
                self.ws.close()
            except:
                pass
        self.connected = False
        self.subscribed = False
        self.reconnect_count = 0
