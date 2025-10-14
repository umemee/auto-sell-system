# 1단계 수정: websocket_client.py (핵심 문제점만 수정)

import json
import logging
import ssl
import threading
import time
from datetime import datetime
from websocket import WebSocketApp

logger = logging.getLogger(__name__)

class WebSocketClient:
    """한국투자증권 실시간 체결통보 WebSocket 클라이언트 (핵심 수정 버전)"""
    
    def __init__(self, config, token_manager, message_handler):
        self.config = config
        self.token_manager = token_manager
        self.message_handler = message_handler
        self.ws = None
        self._connected = False
        self._subscribed = False
        self.reconnect_count = 0
        self.max_reconnects = 10
        self.is_running = False
        
        # 🔥 핵심 수정 1: WebSocket URL에 /websocket 경로 자동 추가
        self.ws_url = self._fix_websocket_url()
        self.custtype = "P"
        self.tr_type = "1"
        
        # 🔥 핵심 수정 2: 기본 감시 종목 설정
        self.default_symbol = config['trading'].get('default_symbol', 'AAPL')
        
        logger.info(f"✅ WebSocket 클라이언트 초기화: {self.ws_url}")

    def _fix_websocket_url(self):
        """WebSocket URL 수정 - /websocket 경로 자동 추가"""
        base_url = self.config['api'].get('websocket_url', '')
        
        # /websocket 경로가 없으면 자동 추가
        if base_url and not base_url.endswith('/websocket'):
            if base_url.endswith('/'):
                base_url = base_url.rstrip('/')
            base_url += '/websocket'
            
        logger.info(f"🔧 WebSocket URL 수정됨: {base_url}")
        return base_url

    def _create_subscribe_message(self):
        """실시간 체결통보 구독 메시지 생성"""
        # 🔥 핵심 수정 3: approval_key 검증 및 재시도
        approval_key = self.token_manager.get_websocket_approval_key()
        if not approval_key:
            logger.error("❌ WebSocket 승인키 발급 실패")
            return None
            
        subscribe_message = {
            "header": {
                "approval_key": approval_key,
                "custtype": self.custtype,
                "tr_type": self.tr_type,
                "content-type": "utf-8"
            },
            "body": {
                "input": {
                    "tr_id": "H0STCNI0",
                    # 🔥 핵심 수정 4: 기본 종목 지정 (공백 방지)
                    "tr_key": self.default_symbol
                }
            }
        }
        return json.dumps(subscribe_message)

    def on_open(self, ws):
        """WebSocket 연결 성공 시 호출"""
        logger.info("🔌 한국투자증권 WebSocket 연결 성공!")
        self._connected = True
        self.reconnect_count = 0
        
        try:
            subscribe_msg = self._create_subscribe_message()
            if subscribe_msg:
                ws.send(subscribe_msg)
                logger.info(f"📡 {self.default_symbol} 종목 체결통보 구독 요청 전송")
            else:
                logger.error("❌ 구독 메시지 생성 실패")
                
        except Exception as e:
            logger.error(f"❌ 구독 메시지 전송 실패: {e}")

    def on_message(self, ws, message):
        """WebSocket 메시지 수신 시 호출"""
        try:
            # 구독 확인 메시지
            if "SUBSCRIBE SUCCESS" in message or "구독" in message:
                logger.info("🎯 실시간 체결통보 구독 성공!")
                self._subscribed = True
                return
                
            # 핑퐁 메시지
            if "PINGPONG" in message or "PONG" in message:
                logger.debug("💗 핑퐁 응답 수신")
                return
            
            # JSON 파싱
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                logger.debug(f"비JSON 메시지: {message[:100]}...")
                return
            
            # 체결 메시지 처리
            if 'header' in data and 'body' in data:
                header = data['header']
                body = data['body']
                
                tr_id = header.get('tr_id', '')
                if tr_id == 'H0STCNI0':
                    self._handle_execution_message(body)
                    
        except Exception as e:
            logger.error(f"❌ 메시지 처리 오류: {e}")

    def _handle_execution_message(self, body):
        """체결 메시지 처리 (필드명 검증 포함)"""
        try:
            if 'output' not in body:
                return
                
            output = body['output']
            
            # 매수 체결 확인
            order_type = output.get('sll_buy_dvsn_cd', '')
            if order_type != '02':
                return
            
            # 🔥 핵심 수정 5: 필드명 우선순위 적용
            ticker = output.get('pdno', '').strip()
            
            # 수량: ord_qty 우선, 없으면 ccld_qty
            quantity_str = output.get('ord_qty') or output.get('ccld_qty', '0')
            
            # 가격: ord_unpr 우선, 없으면 ccld_unpr
            price_str = output.get('ord_unpr') or output.get('ccld_unpr', '0')
            
            # 데이터 검증
            if not ticker:
                logger.warning("⚠️ 종목코드 없음")
                return
                
            try:
                quantity = int(quantity_str) if str(quantity_str).isdigit() else 0
                price = float(price_str) if str(price_str).replace('.', '').isdigit() else 0.0
            except (ValueError, AttributeError):
                logger.warning(f"⚠️ 데이터 파싱 실패: qty={quantity_str}, price={price_str}")
                return
            
            if quantity <= 0 or price <= 0:
                logger.warning(f"⚠️ 잘못된 체결 데이터: {ticker}")
                return
            
            execution_data = {
                'ticker': ticker,
                'quantity': quantity,
                'price': price,
                'order_type': 'buy',
                'timestamp': datetime.now(),
                'source': 'websocket'
            }
            
            logger.info(f"🔥 매수 체결 감지: {ticker} {quantity:,}주 @ ${price:.2f}")
            
            if self.message_handler:
                self.message_handler(execution_data)
                
        except Exception as e:
            logger.error(f"❌ 체결 메시지 처리 오류: {e}")

    def on_error(self, ws, error):
        """WebSocket 오류 시 호출"""
        logger.error(f"❌ WebSocket 오류: {error}")
        self._connected = False
        self._subscribed = False

    def on_close(self, ws, close_status_code, close_msg):
        """WebSocket 연결 종료 시 호출"""
        logger.warning(f"🔌 WebSocket 연결 종료: {close_status_code} - {close_msg}")
        self._connected = False
        self._subscribed = False

    def start(self):
        """WebSocket 연결 시작"""
        if self.is_running:
            logger.warning("⚠️ WebSocket이 이미 실행 중")
            return
            
        self.is_running = True
        logger.info("🚀 한국투자증권 WebSocket 클라이언트 시작")
        
        def run_websocket():
            while self.is_running and self.reconnect_count < self.max_reconnects:
                try:
                    # 승인키 확인
                    approval_key = self.token_manager.get_websocket_approval_key()
                    if not approval_key:
                        logger.error("❌ WebSocket 승인키 없음")
                        time.sleep(10)
                        continue
                    
                    logger.info(f"🔌 WebSocket 연결 시도 ({self.reconnect_count + 1}/{self.max_reconnects})")
                    
                    self.ws = WebSocketApp(
                        self.ws_url,
                        on_open=self.on_open,
                        on_message=self.on_message,
                        on_error=self.on_error,
                        on_close=self.on_close
                    )
                    
                    # 연결 실행
                    self.ws.run_forever(
                        ping_interval=60,
                        ping_timeout=10,
                        ping_payload="ping"
                    )
                    
                except Exception as e:
                    logger.error(f"❌ WebSocket 연결 실패: {e}")
                
                finally:
                    self._connected = False
                    self._subscribed = False
                    
                    if self.is_running:
                        self.reconnect_count += 1
                        if self.reconnect_count < self.max_reconnects:
                            delay = min(5 * self.reconnect_count, 60)
                            logger.info(f"🔄 {delay}초 후 재연결...")
                            time.sleep(delay)
        
        # 연결 스레드 시작
        connection_thread = threading.Thread(target=run_websocket, daemon=True)
        connection_thread.start()

    def stop(self):
        """WebSocket 연결 중지"""
        if not self.is_running:
            return
            
        logger.info("🛑 WebSocket 연결 중지")
        self.is_running = False
        self._connected = False
        self._subscribed = False
        
        if self.ws:
            try:
                self.ws.close()
            except:
                pass

    def is_connected(self):
        """연결 상태 확인"""
        return self._connected and self._subscribed

    def get_status(self):
        """상세 상태 정보"""
        return {
            'connected': self._connected,
            'subscribed': self._subscribed,
            'running': self.is_running,
            'reconnect_count': self.reconnect_count,
            'url': self.ws_url,
            'symbol': self.default_symbol
        }