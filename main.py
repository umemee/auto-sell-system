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
from telegram_bot import TelegramBot
import threading

def setup_logging(debug=False):
    """로깅 설정"""
    log_level = logging.DEBUG if debug else logging.INFO
    
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    file_handler = RotatingFileHandler(
        'trading.log', maxBytes=10*1024*1024, backupCount=5, encoding='utf-8'
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    
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
    
    setup_logging(args.debug)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    logging.info("=== 한국투자증권 자동 매도 시스템 시작 ===")
    logging.info(f"실행 모드: {args.mode}")
    
    try:
        config = load_config(args.mode)
        
        # Telegram 봇 초기화
        telegram_bot = None
        if config.get('telegram', {}).get('bot_token') and config.get('telegram', {}).get('chat_id'):
            telegram_bot = TelegramBot(
                config['telegram']['bot_token'],
                config['telegram']['chat_id']
            )
            # 시작 알림 전송
            telegram_bot.send_startup_notification()
            
            # 별도 스레드에서 봇 폴링 시작
            bot_thread = threading.Thread(target=telegram_bot.start_polling, daemon=True)
            bot_thread.start()
            logging.info("Telegram 봇이 시작되었습니다.")
        else:
            logging.warning("Telegram 설정이 없어 봇 기능이 비활성화됩니다.")
        
        token_manager = TokenManager(config)
        initial_token = token_manager.get_access_token()
        if not initial_token:
            error_msg = "초기 토큰 발급에 실패했습니다."
            logging.error(error_msg)
            if telegram_bot:
                telegram_bot.send_error_notification(error_msg)
            return
        
        def order_callback(ticker, quantity, sell_price):
            # 매수 감지 알림
            if telegram_bot:
                telegram_bot.send_buy_notification(ticker, quantity, sell_price / 1.03)
            
            # 매도 주문 실행
            success = place_sell_order(config, token_manager, ticker, quantity, sell_price)
            
            # 매도 결과 알림
            if telegram_bot:
                telegram_bot.send_sell_notification(ticker, quantity, sell_price, success)
            
            return success
        
        ws_client = WebSocketClient(config, token_manager, order_callback)
        
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
                error_msg = f"WebSocket 연결 중 예외 발생: {e}"
                logging.error(error_msg)
                if telegram_bot:
                    telegram_bot.send_error_notification(error_msg)
            
            reconnect_attempts += 1
            if reconnect_attempts < max_reconnect_attempts:
                delay = min(10 * (2 ** reconnect_attempts), 60)
                logging.info(f"재연결 시도 {reconnect_attempts}/{max_reconnect_attempts} - {delay}초 후 재시도")
                time.sleep(delay)
                
                token_manager.get_access_token()
            else:
                critical_msg = "최대 재연결 횟수를 초과하여 프로그램을 종료합니다."
                logging.critical(critical_msg)
                if telegram_bot:
                    telegram_bot.send_error_notification(critical_msg)
                break
        
        ws_client.close()
        
    except Exception as e:
        critical_msg = f"프로그램 실행 중 치명적 오류 발생: {e}"
        logging.critical(critical_msg)
        if telegram_bot:
            telegram_bot.send_error_notification(critical_msg)
        return 1
    
    logging.info("프로그램이 정상적으로 종료되었습니다.")
    return 0

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
