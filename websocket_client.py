import websocket
import json
import logging
import time
from datetime import datetime, timedelta

class WebSocketClient:
    def __init__(self, config, token_manager, order_callback):
        self.config = config
        self.token_manager = token_manager
        self.order_callback = order_callback
        self.ws = None
        self.processed_executions = set()
        self.last_cleanup = datetime.now()
        self.logger = logging.getLogger(__name__)

    def cleanup_processed_executions(self):
        if datetime.now() - self.last_cleanup > timedelta(hours=self.config['system']['cleanup_interval_hours']):
            count = len(self.processed_executions)
            self.processed_executions.clear()
            self.last_cleanup = datetime.now()
            self.logger.info(f"🧹 처리된 체결 키 정리: {count}개 → 0개")

    def on_message(self, ws, message):
        # 메시지 포맷 검증
        try:
            if '|' not in message: return
            parts = message.split('|')
            if len(parts) < 4 or parts[1] != "H0STCNI0": return
            body = parts[3].split('^')
            if len(body) < 9: return

            ord_no, exec_no, ord_dvsn = body[4], body[5], body[6]
            ticker = body[2]
            quantity = int(float(body[7])) if body[7].isdigit() or body[7].replace('.','',1).isdigit() else 0
            price = float(body[8]) if body[8].replace('.','',1).isdigit() else 0.0

            key = f"{ord_no}-{exec_no}"
            if key in self.processed_executions: return

            if ord_dvsn == '02' and ticker and quantity > 0 and price > 0:
                self.logger.info(f"🔔 신규 매수 체결: {ticker} {quantity}주 @ ${price:.2f}")
                sell_price = price * (1 + self.config['trading']['profit_margin'])
                success = self.order_callback(ticker, quantity, sell_price)
                if success:
                    self.processed_executions.add(key)
                    self.cleanup_processed_executions()

        except Exception as e:
            self.logger.error(f"메시지 처리 오류: {e}")

    def on_error(self, ws, error):
        self.logger.error(f"WebSocket 오류: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        self.logger.warning(f"WebSocket 연결 종료: {close_status_code} {close_msg}")

    def on_open(self, ws):
        self.logger.info("WebSocket 연결 성공, 체결 통보 구독 요청 중...")
        token = self.token_manager.get_access_token()
        if not token:
            self.logger.error("토큰이 유효하지 않아 구독 중단")
            ws.close()
            return

        req = {
            "header": {
                "authorization": f"Bearer {token}",
                "appkey": self.config['api_key'],
                "appsecret": self.config['api_secret'],
                "tr_type": "1",
                "custtype": "P"
            },
            "body": {
                "input": {
                    "tr_id": "H0STCNI0",
                    "tr_key": f"{self.config['cano']}-{self.config['acnt_prdt_cd']}"
                }
            }
        }
        ws.send(json.dumps(req))
        self.logger.info("✅ 구독 요청 전송 완료")

    def connect(self):
        url = self.config['api']['websocket_url']
        self.ws = websocket.WebSocketApp(
            url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )
        # Ping/Pong 유지
        self.ws.run_forever(ping_interval=30, ping_timeout=10)

    def close(self):
        if self.ws:
            self.ws.close()
            self.logger.info("WebSocket 연결 종료 요청 완료")
