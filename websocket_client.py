# websocket_client.py - 완전한 전체 코드 (구독 메시지 추가)

import json
import logging
import ssl
import threading
import time
from websocket import WebSocketApp
from auth import TokenManager

logger = logging.getLogger(__name__)

class WebSocketClient:
    def __init__(self, config, message_handler):
        self.config = config
        self.message_handler = message_handler
        self.ws = None
        self.token_manager = TokenManager(config)

    def _get_headers(self):
        token = self.token_manager.get_access_token()
        return [
            f"Authorization: Bearer {token}",
            f"appkey: {self.config['api']['app_key']}",
            f"appsecret: {self.config['api']['app_secret']}",
            "tr_id: H0STCNI0",  # 체결통보 TR ID
            "custtype: P"
        ]

    def on_open(self, ws):
        logger.info("WebSocket connection opened")
        # 체결통보 구독 요청 (H0STCNI0)
        sub_msg = {
            "header": {
                "tr_type": "1",   # 구독
                "tr_id": "H0STCNI0"
            },
            "body": {
                "input": {
                    "tr_key": self.config['api']['cano'] + self.config['api']['acnt_prdt_cd'],
                    "tr_type": "1"
                }
            }
        }
        try:
            ws.send(json.dumps(sub_msg))
            logger.info(f"Subscribed to H0STCNI0 for account {sub_msg['body']['input']['tr_key']}")
        except Exception as e:
            logger.error(f"Failed to send subscription message: {e}")

    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            self.message_handler(data)
        except json.JSONDecodeError:
            logger.error("Received non-JSON message: %s", message)
        except Exception as e:
            logger.error(f"Failed to process message: {e}")

    def on_error(self, ws, error):
        logger.error(f"WebSocket error: {error}")
        logger.exception(error)

    def on_close(self, ws, close_status_code, close_msg):
        logger.warning(f"WebSocket closed: {close_status_code} - {close_msg}")

    def _connect(self):
        url = self.config['api']['ws_url']
        headers = self._get_headers()
        logger.info(f"Connecting to WebSocket: {url}")
        self.ws = WebSocketApp(
            url,
            header=headers,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )
        # 운영 환경: CERT_REQUIRED
        # 개발/테스트: CERT_NONE
        ssl_opts = {"cert_reqs": ssl.CERT_REQUIRED}
        self.ws.run_forever(
            sslopt=ssl_opts,
            ping_interval=30,
            ping_timeout=10
        )

    def start(self):
        thread = threading.Thread(target=self._run)
        thread.daemon = True
        thread.start()

    def _run(self):
        while True:
            try:
                self._connect()
            except Exception as e:
                logger.error(f"WebSocket connection failed, retrying in 5s: {e}")
                time.sleep(5)
            else:
                break

    def stop(self):
        if self.ws:
            self.ws.close()
