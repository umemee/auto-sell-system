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
    한국투자증권 WebSocket 클라이언트 - 기획서 v1.0 완전 준수 버전
    
    주요 변경사항:
    1. 정규장 시간: ET 09:30-12:00 (기획서 2.2절)
    2. 재연결 횟수: 최대 3회 (기획서 5.2절 비상 증지 조건)
    3. WebSocket 실패 시 시스템 종지 로직 추가 (기획서 5.2절)
    4. 비상 정지 콜백 메커니즘 추가
    """

    def __init__(self, config, token_manager, message_handler, emergency_stop_callback=None):
        self.config = config
        self.token_manager = token_manager
        self.message_handler = message_handler
        self.emergency_stop_callback = emergency_stop_callback  # ✅ 추가: 시스템 종지 콜백

        # WebSocket 연결 상태
        self.ws = None
        self.connected = False
        self.subscribed = False
        self.reconnect_count = 0
        self.max_reconnects = 3  # ✅ 변경: 10 → 3 (기획서 5.2절)
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
        미국 정규장 시간인지 확인 (ET 09:30-12:00)
        
        ✅ 수정: 기획서 2.2절에 따라 ET 16:00 → 12:00으로 변경
        
        Returns: bool - 정규장이면 True
        """
        try:
            et_tz = timezone('US/Eastern')
            et_now = datetime.now(et_tz).time()
            
            regular_start = dtime(9, 30)   # 09:30 ET
            regular_end = dtime(12, 0)     # ✅ 변경: 16:00 → 12:00 (기획서 2.2절)
            
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
        approval_key = self.token_manager.get_approval_key()
        if not approval_key:
            logger.error("❌ WebSocket 승인키 없음")
            return None
    
        ticker = symbol or self.default_symbol
        tr_key = f"DNAS{ticker}"  # 기본적으로 나스닥으로 설정

        subscribe_message = {
            "header": {
                "approval_key": approval_key,
                "custtype": self.custtype,
                "trtype": self.tr_type,
                "content-type": "utf-8"
            },
            "body": {
                "input": {
                    "tr_id": "HDFSCNT0",
                    "tr_key": tr_key,
                    "pdno": ticker  
                }
            }
        }

        logger.info("=" * 60)
        logger.info("📋 WebSocket 구독 메시지 생성")
        logger.info(f"  - TR_ID: HDFSCNT0")
        logger.info(f"  - TR_KEY: {tr_key}")
        logger.info(f"  - PDNO: {ticker}")
        logger.info(f"  - Approval Key: {approval_key[:20]}...")
        logger.info("=" * 60)
        logger.debug(f"전체 메시지:\n{json.dumps(subscribe_message, indent=2)}")
 
        return json.dumps(subscribe_message)

    def _refresh_approval_key_if_needed(self):
        """
        승인키 갱신 - 오류 발생 시에만 사용
    
        ⚠️ 주의: 승인키를 갱신하면 기존 WebSocket 세션이 끊깁니다.
        따라서 정상 동작 중에는 갱신하지 않고, 오류 발생 시에만 수동으로 호출합니다.
        """
        try:
            logging.info("🔑 승인키 강제 갱신 시도 (오류 복구용)")
        
            new_key = self.token_manager.get_approval_key(force_refresh=True)

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
        
        # 구독 확인 후 재시도 로직
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

    # ✅ --- 수정 1: on_message() 함수 전체 교체 ---
    def on_message(self, ws, message):
        print(f"🔥🔥🔥 RAW MESSAGE: {message}")
        logger.info(f"🔥🔥🔥 RAW MESSAGE: {message}")
        try:
            # 모든 메시지 로깅 (디버그용)
            logger.info(f"📥 WebSocket 수신 원본: {message[:500]}")
            # PING/PONG 처리
            if "PINGPONG" in message or "PONG" in message:
                logger.debug(f"▶ WebSocket PING/PONG: {message}")
                return

            # JSON 파싱 시도
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                # JSON이 아닌 메시지는 무시 (PING 등)
                logger.debug(f"Non-JSON 메시지: {message}")
                return

            # 헤더 확인
            header = data.get("header", {})
            tr_id = header.get("tr_id", "")

            # 바디 확인
            body = data.get("body", {})

            # 오류 응답 처리
            rt_cd = body.get("rt_cd", "")
            if rt_cd != "" and rt_cd != "0":  # 오류 코드
                msg1 = body.get("msg1", "알 수 없는 오류")
                logger.warning(f"⚠️ WebSocket 서버 오류 (rt_cd={rt_cd}): {msg1}")
                # 승인키 오류
                if "approval" in msg1.lower():
                    logger.warning("🔑 승인키 관련 오류, 갱신 시도")
                    self._refresh_approval_key_if_needed()
                    time.sleep(2)
                    self.subscribe(self.default_symbol)
                # tr_key 오류
                elif "tr_key" in msg1.lower() or "tr_id" in msg1.lower():
                    logger.info("🔄 tr_key/tr_id 오류로 인한 구독 재시도")
                    time.sleep(2)
                    self.subscribe(self.default_symbol)
                return

            # HDFSCNT0 실시간지연체결가 데이터 처리
            if tr_id == "HDFSCNT0":
                # 첫 데이터 수신 = 구독 성공
                if not self.subscribed:
                    logger.info("✅ WebSocket 구독 성공 (첫 데이터 수신)")
                    self.subscribed = True
                self._handle_realtime_price_message(body)

        except Exception as e:
            logger.error(f"❌ on_message 처리 중 예외: {e}", exc_info=True)
    # ✅ --- 수정 1 완료 ---

    # ✅ --- 수정 2: _handle_execution_message() 삭제 후 _handle_realtime_price_message() 추가 ---
    def _handle_realtime_price_message(self, body):
        """
        실시간지연체결가(HDFSCNT0) 데이터 처리

        응답 필드:
        - SYMB: 종목코드
        - LAST: 현재가
        - EVOL: 체결량
        - TVOL: 거래량
        등 25개 필드
        """
        try:
            output = body.get("output", {})

            # 종목코드
            ticker = output.get("SYMB", "").strip()
            if not ticker:
                logger.debug("종목코드 없음, 스킵")
                return

            # 현재가
            current_price_str = output.get("LAST", "0")
            try:
                current_price = float(current_price_str)
            except (ValueError, TypeError):
                logger.debug(f"현재가 파싱 실패: {current_price_str}")
                return

            # 체결량 (실시간 변동)
            exec_volume_str = output.get("EVOL", "0")
            try:
                exec_volume = int(exec_volume_str) if exec_volume_str else 0
            except (ValueError, TypeError):
                exec_volume = 0

            # 거래량 (누적)
            total_volume_str = output.get("TVOL", "0")
            try:
                total_volume = int(total_volume_str) if total_volume_str else 0
            except (ValueError, TypeError):
                total_volume = 0

            # 시세 데이터 로깅
            logger.info(f"📊 실시간시세: {ticker} ${current_price:.2f} | 체결량: {exec_volume} | 거래량: {total_volume:,}")

            # 메시지 핸들러로 전달 (자동매도 로직에서 사용)
            if self.message_handler:
                price_data = {
                    'ticker': ticker,
                    'price': current_price,
                    'exec_volume': exec_volume,
                    'total_volume': total_volume,
                    'timestamp': datetime.now(),
                    'source': 'websocket',
                    'raw_output': output  # 전체 데이터
                }
                self.message_handler(price_data)

        except Exception as e:
            logger.error(f"❌ _handle_realtime_price_message 오류: {e}", exc_info=True)
    # ✅ --- 수정 2 완료 ---

    def on_error(self, ws, error):
        logger.error(f"❌ WebSocket 오류: {error}")
        self.connected = False
        self.subscribed = False

    def on_close(self, ws, close_status_code, close_msg):
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
            logger.warning(f"📝 서버 메시지: {close_msg}")
            
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

        # ✅ 정규장 시간 체크 (기획서 2.3절)
        if not self._is_regular_market():
            logger.info("🌙 현재는 정규장이 아닙니다. WebSocket 대신 REST 폴링 모드 사용 권장.")
            return

        self.is_running = True
        self.reconnect_count = 0

        def run_loop():
            while self.is_running and self.reconnect_count < self.max_reconnects:
                try:
                    
                    approval_key = self.token_manager.get_approval_key()
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
                        # 시장 상태에 따른 적응형 재연결 지연
                        if not self._is_regular_market():
                            # 프리마켓/장 마감: 5분 대기 (AWS 비용 절감)
                            delay = 300  # 5분
                            logger.info(f"🌙 정규장 아님 - 재연결 대기 {delay}초 (5분)")
                        else:
                            # 정규장: 빠른 재연결 (5초, 10초, 15초...)
                            delay = min(5 * self.reconnect_count, 60)
                            logger.info(f"⏳ 재접속 대기 {delay}초")
    
                        time.sleep(delay)
            
            # ✅ 추가: 정규장에서 재연결 횟수 초과 시 시스템 종지 (기획서 5.2절)
            if self.reconnect_count >= self.max_reconnects:
                if self._is_regular_market():
                    logger.critical(f"🛑 정규장에서 WebSocket {self.max_reconnects}회 연결 실패 - 시스템 종지 (기획서 5.2절)")
                    if self.emergency_stop_callback:
                        self.emergency_stop_callback("WebSocket 연결 실패 (정규장)")
                else:
                    logger.warning(f"⚠️ 프리마켓/장외 시간에 WebSocket {self.max_reconnects}회 연결 실패 - REST 폴링으로 전환 권장")

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