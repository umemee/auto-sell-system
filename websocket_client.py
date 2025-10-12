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
        self.is_running = False
        self.reconnect_attempts = 0
        
    def is_connected(self):
        """WebSocket 연결 상태 확인"""
        return self.ws is not None and self.is_running

    def cleanup_processed_executions(self):
        """처리된 체결 키 정리"""
        if datetime.now() - self.last_cleanup > timedelta(hours=self.config['system']['cleanup_interval_hours']):
            count = len(self.processed_executions)
            self.processed_executions.clear()
            self.last_cleanup = datetime.now()
            self.logger.info(f"🧹 처리된 체결 키 정리: {count}개 → 0개")

    def on_message(self, ws, message):
        """WebSocket 메시지 수신 처리"""
        try:
            # 메시지 포맷 검증
            if '|' not in message: 
                return
            
            parts = message.split('|')
            if len(parts) < 4 or parts[1] != "H0STCNI0": 
                return
            
            body = parts[3].split('^')
            if len(body) < 9: 
                return
            
            ord_no, exec_no, ord_dvsn = body[4], body[5], body[6]
            ticker, exec_qty, exec_price = body[7], body[8], body[10]
            
            # 매수 주문만 처리 (00: 지정가 매수, 01: 시장가 매수)
            if ord_dvsn not in ['00', '01']:
                return
            
            # 중복 처리 방지
            execution_key = f"{ord_no}_{exec_no}"
            if execution_key in self.processed_executions:
                return
            
            self.processed_executions.add(execution_key)
            
            execution_data = {
                'ticker': ticker,
                'quantity': int(exec_qty),
                'price': float(exec_price),
                'order_number': ord_no,
                'execution_number': exec_no,
                'timestamp': datetime.now().isoformat()
            }
            
            self.logger.info(f"📈 매수 체결: {ticker} {exec_qty}주 @${exec_price}")
            
            # 주문 콜백 호출
            if self.order_callback:
                self.order_callback(execution_data)
                
            # 메모리 정리
            self.cleanup_processed_executions()
            
        except Exception as e:
            self.logger.error(f"메시지 처리 오류: {e}")

    def on_error(self, ws, error):
        """WebSocket 오류 처리"""
        self.logger.error(f"🔌 WebSocket 오류: {error}")
        self.is_running = False

    def on_close(self, ws, close_status_code, close_msg):
        """WebSocket 연결 종료 처리"""
        self.logger.warning(f"🔌 WebSocket 연결 종료: {close_status_code} - {close_msg}")
        self.is_running = False

    def on_open(self, ws):
        """WebSocket 연결 성공 처리"""
        try:
            self.logger.info("✅ WebSocket 연결 성공")
            self.is_running = True
            self.reconnect_attempts = 0  # 연결 성공 시 재시도 카운터 리셋
            
            # 실시간 체결 데이터 구독 요청
            subscribe_message = json.dumps({
                "header": {
                    "approval_key": self.token_manager.get_websocket_approval_key(),
                    "custtype": "P",
                    "tr_type": "1",
                    "content-type": "utf-8"
                },
                "body": {
                    "input": {
                        "tr_id": "H0STCNI0",
                        "tr_key": self.config['account_no']
                    }
                }
            })
            
            ws.send(subscribe_message)
            self.logger.info("📡 체결 데이터 구독 요청 완료")
            
        except Exception as e:
            self.logger.error(f"WebSocket 초기화 오류: {e}")
            self.is_running = False

    def connect(self):
        """WebSocket 연결 시작"""
        try:
            self.logger.info("🔌 WebSocket 연결을 시작합니다...")
            
            # 기존 연결이 있으면 정리
            if self.ws:
                try:
                    self.ws.close()
                except:
                    pass
                self.ws = None
            
            # WebSocket 연결 설정
            websocket_url = self.config['api']['websocket_url']
            
            # WebSocket 클라이언트 생성 및 연결
            self.ws = websocket.WebSocketApp(
                websocket_url,
                on_message=self.on_message,
                on_error=self.on_error,
                on_close=self.on_close,
                on_open=self.on_open
            )
            
            # 연결 실행 (blocking call)
            self.ws.run_forever(
                ping_interval=30,  # 30초마다 ping
                ping_timeout=10,   # ping 타임아웃 10초
                reconnect=5        # 5초 간격으로 재연결 시도
            )
            
        except Exception as e:
            self.logger.error(f"WebSocket 연결 실패: {e}")
            self.is_running = False
            raise

    def disconnect(self):
        """WebSocket 연결 종료"""
        try:
            self.logger.info("🔌 WebSocket 연결을 종료합니다...")
            self.is_running = False
            
            if self.ws:
                self.ws.close()
                self.ws = None
                
        except Exception as e:
            self.logger.error(f"WebSocket 종료 오류: {e}")