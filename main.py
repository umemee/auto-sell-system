import logging
import time
import signal
import sys
import argparse
from logging.handlers import RotatingFileHandler
from config import load_config
from auth import TokenManager
from order import place_sell_order
from websocket_client import WebSocketClient

def setup_logging(debug=False):
    """로깅 설정"""
    log_level = logging.DEBUG if debug else logging.INFO
    
    # 로그 포맷 설정
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 파일 핸들러 (로테이션)
    file_handler = RotatingFileHandler(
        'trading.log', maxBytes=10*1024*1024, backupCount=5, encoding='utf-8'
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    
    # 콘솔 핸들러
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    
    # 루트 로거 설정
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

def signal_handler(signum, frame):
    """프로그램 종료 신호 처리"""
    logging.info("프로그램 종료 신호를 받았습니다. 정리 중...")
    sys.exit(0)

def main():
    parser = argparse.ArgumentParser(description='한국투자증권 자동 매도 시스템')
    parser.add_argument('--debug', action='store_true', help='디버그 모드 활성화')
    parser.add_argument('--mode', choices=['development', 'production'], 
                       default='development', help='실행 모드')
    args = parser.parse_args()
    
    # 로깅 설정
    setup_logging(args.debug)
    
    # 시그널 핸들러 등록
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    logging.info("=== 한국투자증권 자동 매도 시스템 시작 ===")
    logging.info(f"실행 모드: {args.mode}")
    
    try:
        # 설정 로드
        config = load_config()
        
        # 토큰 매니저 초기화
        token_manager = TokenManager(config)
        initial_token = token_manager.get_access_token()
        if not initial_token:
            logging.error("초기 토큰 발급에 실패했습니다. 프로그램을 종료합니다.")
            return
        
        # 주문 콜백 함수 정의
        def order_callback(ticker, quantity, sell_price):
            return place_sell_order(config, token_manager, ticker, quantity, sell_price)
        
        # WebSocket 클라이언트 초기화
        ws_client = WebSocketClient(config, token_manager, order_callback)
        
        # 재연결 로직
        max_reconnect_attempts = config['system']['max_reconnect_attempts']
        reconnect_attempts = 0
        
        while reconnect_attempts < max_reconnect_attempts:
            try:
                logging.info("WebSocket 연결을 시작합니다...")
                ws_client.connect()
                
            except KeyboardInterrupt:
                logging.info("사용자에 의해 프로그램이 중단되었습니다.")
                break
            except Exception as e:
                logging.error(f"WebSocket 연결 중 예외 발생: {e}")
            
            # 재연결 로직
            reconnect_attempts += 1
            if reconnect_attempts < max_reconnect_attempts:
                # 지수적 백오프 (최대 60초)
                delay = min(10 * (2 ** reconnect_attempts), 60)
                logging.info(f"재연결 시도 {reconnect_attempts}/{max_reconnect_attempts} - {delay}초 후 재시도")
                time.sleep(delay)
                
                # 토큰 갱신
                token_manager.get_access_token(force_refresh=True)
            else:
                logging.critical("최대 재연결 횟수를 초과하여 프로그램을 종료합니다.")
                break
        
        # 정리
        ws_client.close()
        
    except Exception as e:
        logging.critical(f"프로그램 실행 중 치명적 오류 발생: {e}")
        return 1
    
    logging.info("프로그램이 정상적으로 종료되었습니다.")
    return 0

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
