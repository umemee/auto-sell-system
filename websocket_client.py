import json
import logging
import ssl
import threading
import time
from datetime import datetime
from websocket import WebSocketApp

logger = logging.getLogger(__name__)

class WebSocketClient:
    """
    한국투자증권 WebSocket 클라이언트
    """

    def __init__(self, config, token_manager, message_handler):
        self.config = config
        self.token_manager = token_manager
        self.message_handler = message_handler

        self.ws = None
        self.connected = False
        self.subscribed = False
        self.reconnect_count = 0
        self.max_reconnects = 10
        self.is_running = False

        self.ws_url = self._fix_websocket_url(self.config['api'].get('websocket_url'))
        self.custtype = self.config.get('custtype', 'P')
        self.trtype = self.config.get('trtype', '1')
        self.default_symbol = self.config.get('trading', {}).get('default_symbol', 'AAPL')

        logger.info(f"🔧 WebSocket URL 설정: {self.ws_url}")
        logger.info(f"✅ WebSocket 클라이언트 초기화: {self.ws_url}")

    def _fix_websocket_url(self, base_url):
        logger.info(f"WebSocket URL: {base_url}")
        return base_url

    def create_subscribe_message(self):
        """
        approval key를 얻어 구독 메시지 생성
        """
        approval_key = self.token_manager.get_websocket_approval_key()
        if not approval_key:
            logger.error("❌ WebSocket 승인키 없음")
            return None

        subscribe_message = {
            "header": {
                "approval_key": approval_key,
                "custtype": self.custtype,
                "trtype": self.trtype,
                "content-type": "utf-8"
            },
            "body": {
                "input": {
                    "tr_id": "H0STCNI0"       # 해외주식 체결 정보
                }
            }
        }
        return json.dumps(subscribe_message)

            
    def subscribe(self, symbol=None):
        if not self.ws or not self.connected:
            logger.error("❌ WS 연결이 없거나 연결 상태가 아닙니다. 구독 전송 불가")
            return

        msg = self.create_subscribe_message()
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
        try:
            msg = self.create_subscribe_message()
            if msg:
                ws.send(msg)
                logger.info(f"▶ WebSocket 구독 메시지 전송 (on_open): {msg}")
            else:
                logger.error("❌ 구독 메시지 생성 실패")
        except Exception as e:
            logger.error(f"❌ on_open 처리 중 예외: {e}", exc_info=True)

    def on_message(self, ws, message):
        try:
            # 구독 성공 메시지 처리
            if "SUBSCRIBE SUCCESS" in message or "SUBSCRIBED" in message:
                logger.info("✅ WebSocket 구독 성공")
                self.subscribed = True
                return

            # PING/PONG 처리
            if "PINGPONG" in message or "PONG" in message:
                logger.debug(f"▶ WebSocket PING/PONG: {message}")
                return

            # 상세 메시지 핸들링
            raw = message
            logger.debug(f"📡 WebSocket 수신 원본 메시지: {raw}")

            data = json.loads(raw)
            logger.debug(f"📑 WebSocket 파싱 데이터: {data}")

            header = data.get("header", {})
            body = data.get("body", {})
            tr_id = header.get("tr_id", "")

            if tr_id == "H0STCNI0":
                self.handle_execution_message(body)
        except Exception as e:
            logger.error(f"❌ on_message 처리 중 예외: {e}", exc_info=True)

    def handle_execution_message(self, body):
        try:
            output = body.get("output", {})
            order_type = output.get("sll_buy_dvsn_cd")
            if order_type != "02":
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
        logger.warning(f"⚠️ WebSocket 연결 해제: {close_status_code} - {close_msg}")
        self.connected = False
        self.subscribed = False

    def start(self):
        if self.is_running:
            logger.warning("WebSocket 이미 실행 중")
            return

        self.is_running = True
        self.reconnect_count = 0

        def run_loop():
            while self.is_running and self.reconnect_count < self.max_reconnects:
                try:
                    # 승인키 갱신
                    approval_key = self.token_manager.get_websocket_approval_key()
                    if not approval_key:
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
                    delay = min(5 * self.reconnect_count, 60)
                    logger.info(f"⏳ 재접속 대기 {delay}s")
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
            except Exception:
                pass
        logger.info("🛑 WebSocket 클라이언트 중지")

    def is_connected(self):
        return self.connected and self.subscribed

    def get_status(self):
        return {
            'connected': self.connected,
            'subscribed': self.subscribed,
            'running': self.is_running,
            'reconnect_count': self.reconnect_count,
            'url': self.ws_url,
            'symbol': self.default_symbol
        }
