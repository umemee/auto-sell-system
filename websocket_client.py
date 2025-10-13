# websocket_client.py - 수정된 전체 코드 (인증 헤더 및 구독 메시지 개선)

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
        self._connected = False
        
    def _get_headers(self):
        """WebSocket 연결용 헤더 생성 - 공식 답변 기준으로 수정"""
        try:
            token = self.token_manager.get_access_token()
            if not token:
                logger.error("유효한 토큰을 가져올 수 없습니다.")
                return []
                
            # 환경변수 값 직접 확인 및 로깅
            api_key = self.config.get('api_key')
            api_secret = self.config.get('api_secret')
            
            if not api_key or not api_secret:
                logger.error(f"API 키 누락: api_key={bool(api_key)}, api_secret={bool(api_secret)}")
                return []
            
            # 헤더 구성 - 공식 답변에 따른 정확한 형식
            headers = [
                f"Authorization: Bearer {token}",
                f"appkey: {api_key}",
                f"appsecret: {api_secret}",
                "tr_id: H0STCNI0",    # 체결통보 TR ID
                "custtype: P"         # 개인고객 구분
            ]
            
            logger.info(f"WebSocket 헤더 구성 완료: token=***{token[-4:]}, appkey=***{api_key[-4:]}")
            return headers
            
        except Exception as e:
            logger.error(f"헤더 생성 중 오류: {e}")
            return []

    def on_open(self, ws):
        """WebSocket 연결 열림 - 구독 메시지 전송 개선"""
        logger.info("✅ WebSocket connection opened")
        self._connected = True
        
        # 잠시 대기 후 구독 요청 전송 (서버 핸드셰이크 완료 대기)
        time.sleep(0.5)
        
        try:
            # 계좌번호 확인
            cano = self.config.get('cano')
            acnt_prdt_cd = self.config.get('acnt_prdt_cd')
            
            if not cano or not acnt_prdt_cd:
                logger.error(f"계좌 정보 누락: cano={cano}, acnt_prdt_cd={acnt_prdt_cd}")
                return
                
            tr_key = cano + acnt_prdt_cd
            
            # H0STCNI0 체결통보 구독 요청 - 공식 답변 형식 준수
            sub_msg = {
                "header": {
                    "tr_type": "1",      # 1: 구독, 2: 해제
                    "tr_id": "H0STCNI0"  # 해외주식 체결통보
                },
                "body": {
                    "input": {
                        "tr_key": tr_key,    # 계좌번호 (CANO + ACNT_PRDT_CD)
                        "tr_type": "1"       # 1: 등록, 2: 해제
                    }
                }
            }
            
            # JSON 메시지 전송
            message = json.dumps(sub_msg)
            ws.send(message)
            
            logger.info(f"🎯 H0STCNI0 구독 요청 전송 완료")
            logger.info(f"   계좌: {tr_key}")
            logger.info(f"   메시지: {message}")
            
        except Exception as e:
            logger.error(f"❌ 구독 메시지 전송 실패: {e}")
            logger.exception(e)

    def on_message(self, ws, message):
        """WebSocket 메시지 수신 처리"""
        try:
            logger.debug(f"Raw message received: {message}")
            
            # 서버 응답 메시지 확인
            if "RETURN CODE" in message and "SUBSCRIBE SUCCESS" in message:
                logger.info("🎉 체결통보 구독 성공!")
                return
                
            # JSON 파싱 시도
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                logger.debug(f"Non-JSON message: {message}")
                return
            
            # 체결 데이터 처리
            if 'body' in data and 'output' in data['body']:
                output = data['body']['output']
                
                # 매수 체결인 경우에만 처리 (해외주식)
                sll_buy_dvsn_cd = output.get('sll_buy_dvsn_cd')
                if sll_buy_dvsn_cd == '02':  # 02 = 매수
                    execution_data = {
                        'ticker': output.get('pdno'),           # 종목코드 
                        'quantity': int(output.get('ccld_qty', 0)),    # 체결수량
                        'price': float(output.get('ccld_unpr', 0))     # 체결단가
                    }
                    
                    # 유효한 데이터인지 확인
                    if (execution_data['ticker'] and 
                        execution_data['quantity'] > 0 and 
                        execution_data['price'] > 0):
                        
                        logger.info(f"🔥 매수 체결 감지: {execution_data}")
                        
                        # 메시지 핸들러 호출 (자동매도 트리거)
                        if self.message_handler:
                            self.message_handler(execution_data)
                        else:
                            logger.warning("message_handler가 설정되지 않았습니다.")
                    else:
                        logger.debug(f"불완전한 체결 데이터: {execution_data}")
                else:
                    logger.debug(f"매도 체결 또는 기타 데이터: sll_buy_dvsn_cd={sll_buy_dvsn_cd}")
            else:
                logger.debug("체결 데이터가 아닌 메시지")
                
        except Exception as e:
            logger.error(f"메시지 처리 중 오류: {e}")
            logger.exception(e)

    def on_error(self, ws, error):
        """WebSocket 오류 처리"""
        logger.error(f"❌ WebSocket error: {error}")
        self._connected = False
        if hasattr(error, '__traceback__'):
            logger.exception(error)

    def on_close(self, ws, close_status_code, close_msg):
        """WebSocket 연결 종료 처리"""
        logger.warning(f"🔌 WebSocket closed: {close_status_code} - {close_msg}")
        self._connected = False

    def connect(self):
        """WebSocket 연결 시작"""
        url = self.config['api']['websocket_url']
        headers = self._get_headers()
        
        if not headers:
            logger.error("❌ 헤더 생성 실패, 연결 중단")
            return
            
        logger.info(f"🔌 Connecting to WebSocket: {url}")
        logger.info(f"   Headers count: {len(headers)}")
        
        self.ws = WebSocketApp(
            url,
            header=headers,
            on_open=self.on_open,
            on_message=self.on_message, 
            on_error=self.on_error,
            on_close=self.on_close
        )
        
        # SSL 설정 (운영 환경)
        ssl_opts = {"cert_reqs": ssl.CERT_REQUIRED}
        
        # WebSocket 실행
        self.ws.run_forever(
            sslopt=ssl_opts,
            ping_interval=30,    # 30초마다 ping
            ping_timeout=10      # 10초 ping 타임아웃
        )

    def start(self):
        """백그라운드 스레드에서 WebSocket 시작"""
        thread = threading.Thread(target=self._run)
        thread.daemon = True
        thread.start()

    def _run(self):
        """WebSocket 연결 루프 (재연결 포함)"""
        while True:
            try:
                self.connect()
            except Exception as e:
                logger.error(f"❌ WebSocket connection failed, retrying in 5s: {e}")
                self._connected = False
                time.sleep(5)
            else:
                break

    def stop(self):
        """WebSocket 연결 중지"""
        self._connected = False
        if self.ws:
            self.ws.close()
            logger.info("WebSocket 연결이 중지되었습니다.")

    def is_connected(self):
        """WebSocket 연결 상태 반환"""
        try:
            return self._connected and self.ws and hasattr(self.ws, 'sock') and not self.ws.sock.closed
        except (AttributeError, TypeError):
            return self._connected
        except Exception:
            return False