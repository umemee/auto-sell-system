# websocket_client.py - 수정된 전체 코드

import json
import logging
import ssl
import threading
import time
from websocket import WebSocketApp
from auth import TokenManager

logger = logging.getLogger(__name__)

class WebSocketClient:
    def __init__(self, config, token_manager, message_handler):
        """
        config: 설정 dict
        token_manager: auth.TokenManager 인스턴스
        message_handler: 메시지 처리 callback(데이터 dict 인자)
        """
        self.config = config
        self.token_manager = token_manager
        self.message_handler = message_handler
        self.ws = None
        self._connected = False  # 연결 상태 추적 변수 추가

    def _get_headers(self):
        token = self.token_manager.get_access_token()
        return [
            f"Authorization: Bearer {token}",
            f"appkey: {self.config['api_key']}", # 수정됨: ['api']['app_key'] → ['api_key']
            f"appsecret: {self.config['api_secret']}", # 수정됨: ['api']['app_secret'] → ['api_secret']
            "tr_id: H0STCNI0", # 체결통보 TR ID
            "custtype: P"
        ]

    def on_open(self, ws):
        logger.info("WebSocket connection opened")
        self._connected = True  # 연결 상태 업데이트
        
        # 체결통보 구독 요청 (H0STCNI0)
        sub_msg = {
            "header": {
                "tr_type": "1", # 구독
                "tr_id": "H0STCNI0"
            },
            "body": {
                "input": {
                    "tr_key": self.config['cano'] + self.config['acnt_prdt_cd'], # 올바른 키 사용
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
            
            # 체결 데이터 파싱 로직 추가
            if 'body' in data and 'output' in data['body']:
                output = data['body']['output']
                
                # 매수 체결인 경우에만 처리
                if output.get('sll_buy_dvsn_cd') == '02':  # 02 = 매수
                    execution_data = {
                        'ticker': output.get('pdno'),  # 종목코드
                        'quantity': int(output.get('ccld_qty', 0)),  # 체결수량
                        'price': float(output.get('ccld_unpr', 0))  # 체결단가
                    }
                    
                    # 유효한 데이터인지 확인
                    if execution_data['ticker'] and execution_data['quantity'] > 0 and execution_data['price'] > 0:
                        logger.info(f"매수 체결 감지: {execution_data}")
                        self.message_handler(execution_data)
                    else:
                        logger.debug(f"불완전한 체결 데이터: {execution_data}")
                else:
                    logger.debug(f"매도 체결이거나 기타 데이터: {output.get('sll_buy_dvsn_cd')}")
            else:
                logger.debug("체결 데이터가 아닌 메시지 수신")
                
        except json.JSONDecodeError:
            logger.error("Received non-JSON message: %s", message)
        except Exception as e:
            logger.error(f"Failed to process message: {e}")

    def on_error(self, ws, error):
        logger.error(f"WebSocket error: {error}")
        self._connected = False  # 오류 시 연결 상태 업데이트
        logger.exception(error)

    def on_close(self, ws, close_status_code, close_msg):
        logger.warning(f"WebSocket closed: {close_status_code} - {close_msg}")
        self._connected = False  # 연결 종료 시 상태 업데이트

    def connect(self):
        url = self.config['api']['websocket_url']  # 수정됨: config.yaml 구조에 맞게
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
                self.connect()
            except Exception as e:
                logger.error(f"WebSocket connection failed, retrying in 5s: {e}")
                self._connected = False  # 연결 실패 시 상태 업데이트
                time.sleep(5)
            else:
                break

    def stop(self):
        self._connected = False  # 종료 시 상태 업데이트
        if self.ws:
            self.ws.close()

    def is_connected(self):
        """WebSocket 연결 상태 반환"""
        try:
            return self._connected and self.ws and not self.ws.sock.closed
        except AttributeError:
            # ws.sock가 없는 경우 (아직 연결되지 않음)
            return self._connected
        except Exception:
            return False