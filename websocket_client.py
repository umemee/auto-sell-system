import websocket
import json
import logging
import threading
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
        self.is_connected = False
        self.logger = logging.getLogger(__name__)
        
    def cleanup_processed_executions(self):
        """오래된 처리 키 정리 (메모리 관리)"""
        try:
            if datetime.now() - self.last_cleanup > timedelta(hours=6):
                old_size = len(self.processed_executions)
                
                # 실제 운영에서는 타임스탬프 기반으로 더 정교한 정리 필요
                # 여기서는 간단히 전체 클리어 (6시간마다)
                if old_size > 1000:  # 메모리 사용량이 많을 때만 정리
                    self.processed_executions.clear()
                    self.logger.info(f"처리된 체결 키 정리 완료 ({old_size}개 → 0개)")
                
                self.last_cleanup = datetime.now()
        except Exception as e:
            self.logger.error(f"메모리 정리 중 오류: {e}")
    
    def validate_message_format(self, message):
        """메시지 형식 검증"""
        try:
            if not message or not isinstance(message, str):
                return False, None, None
                
            if '|' not in message:
                return False, None, None
            
            data = message.split('|')
            if len(data) < 4:
                return False, None, None
            
            # 해외주식 체결통보가 아닌 경우
            if data[1] != "H0STCNI0":
                return False, None, None
            
            if '^' not in data[3]:
                return False, None, None
                
            msg_body = data[3].split('^')
            if len(msg_body) < 10:  # 필요한 최소 필드 수
                return False, None, None
            
            return True, data, msg_body
            
        except Exception as e:
            self.logger.warning(f"메시지 형식 검증 중 오류: {e}")
            return False, None, None
    
    def parse_execution_data(self, msg_body):
        """체결 데이터 파싱"""
        try:
            execution_data = {
                'ord_no': msg_body[4] if len(msg_body) > 4 else "",
                'exec_no': msg_body[5] if len(msg_body) > 5 else "",
                'ord_dvsn': msg_body[6] if len(msg_body) > 6 else "",
                'ticker': msg_body[2] if len(msg_body) > 2 else "",
                'quantity': 0,
                'price': 0.0
            }
            
            # 수량 파싱
            if len(msg_body) > 7 and msg_body[7]:
                try:
                    execution_data['quantity'] = int(float(msg_body[7]))
                except (ValueError, TypeError):
                    self.logger.warning(f"수량 파싱 실패: {msg_body[7]}")
                    
            # 가격 파싱
            if len(msg_body) > 8 and msg_body[8]:
                try:
                    execution_data['price'] = float(msg_body[8])
                except (ValueError, TypeError):
                    self.logger.warning(f"가격 파싱 실패: {msg_body[8]}")
            
            return execution_data
            
        except Exception as e:
            self.logger.error(f"체결 데이터 파싱 중 오류: {e}")
            return None
    
    def on_message(self, ws, message):
        """WebSocket 메시지 수신 처리"""
        try:
            # 메시지 형식 검증
            is_valid, data, msg_body = self.validate_message_format(message)
            if not is_valid:
                return
            
            # 체결 데이터 파싱
            execution_data = self.parse_execution_data(msg_body)
            if not execution_data:
                return
            
            # 중복 체결 방지
            exec_key = f"{execution_data['ord_no']}-{execution_data['exec_no']}"
            if exec_key in self.processed_executions:
                self.logger.debug(f"중복 체결 메시지 무시: {exec_key}")
                return
            
            # 매수 체결인 경우만 처리 (ord_dvsn == '02')
            if (execution_data['ord_dvsn'] == '02' and 
                execution_data['ticker'] and 
                execution_data['quantity'] > 0 and 
                execution_data['price'] > 0):
                
                self.logger.info(
                    f"🚨 신규 매수 체결 감지! "
                    f"[{execution_data['ticker']}] "
                    f"수량: {execution_data['quantity']}, "
                    f"가격: ${execution_data['price']:.2f}"
                )
                
                # +3% 매도 주문 실행
                sell_price = execution_data['price'] * (1 + self.config['trading']['profit_margin'])
                success = self.order_callback(execution_data['ticker'], execution_data['quantity'], sell_price)
                
                if success:
                    self.processed_executions.add(exec_key)
                    self.cleanup_processed_executions()
                else:
                    self.logger.error(f"[{execution_data['ticker']}] 자동 매도 주문 실행 실패")
            
        except Exception as e:
            self.logger.error(f"메시지 처리 중 예상치 못한 오류: {e}")
            self.logger.debug(f"문제가 된 메시지: {message[:200]}...")  # 처음 200자만 로그
    
    def on_error(self, ws, error):
        """WebSocket 오류 처리"""
        self.logger.error(f"WebSocket 오류: {error}")
        self.is_connected = False
    
    def on_close(self, ws, close_status_code, close_msg):
        """WebSocket 연결 종료 처리"""
        self.is_connected = False
        self.logger.warning(f"WebSocket 연결 종료 (코드: {close_status_code}, 메시지: {close_msg})")
    
    def on_open(self, ws):
        """WebSocket 연결 성공 처리"""
        self.is_connected = True
        self.logger.info("WebSocket 연결 성공! 실시간 체결 통보 구독 중...")
        
        try:
            token = self.token_manager.get_access_token()
            if not token:
                self.logger.error("유효한 토큰이 없어 구독할 수 없습니다.")
                return
            
            subscription_request = {
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
            
            ws.send(json.dumps(subscription_request))
            self.logger.info("실시간 체결 통보 구독 요청 전송 완료")
            
        except Exception as e:
            self.logger.error(f"구독 요청 중 오류: {e}")
    
    def connect(self):
        """WebSocket 연결 시작"""
        try:
            websocket_url = self.config['api']['websocket_url']
            self.logger.info(f"WebSocket 연결 시도: {websocket_url}")
            
            self.ws = websocket.WebSocketApp(
                websocket_url,
                on_open=self.on_open,
                on_message=self.on_message,
                on_error=self.on_error,
                on_close=self.on_close
            )
            
            # Ping/Pong으로 연결 유지 (30초 간격, 10초 타임아웃)
            self.ws.run_forever(
                ping_interval=30, 
                ping_timeout=10,
                ping_payload="ping"
            )
            
        except Exception as e:
            self.logger.error(f"WebSocket 연결 중 오류: {e}")
            raise
    
    def close(self):
        """WebSocket 연결 종료"""
        try:
            if self.ws:
                self.is_connected = False
                self.ws.close()
                self.logger.info("WebSocket 연결이 정상적으로 종료되었습니다.")
        except Exception as e:
            self.logger.error(f"WebSocket 종료 중 오류: {e}")
