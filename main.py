import logging
import time
import signal
import sys
import argparse
import threading
from logging.handlers import RotatingFileHandler
from config import load_config
from auth import TokenManager
from order import place_sell_order
from websocket_client import WebSocketClient
from telegram_bot import TelegramBot

# 전역 변수로 정상 종료 플래그 추가
shutdown_requested = False
ws_client = None
telegram_bot = None

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
    """시그널 핸들러 - 안전한 종료를 위해 개선"""
    global shutdown_requested, ws_client, telegram_bot
    
    logger = logging.getLogger(__name__)
    logger.info(f"종료 신호 수신 (Signal: {signum}). 안전한 종료를 시작합니다...")
    
    shutdown_requested = True
    
    # 텔레그램 종료 알림
    if telegram_bot:
        try:
            telegram_bot.send_shutdown_notification()
        except Exception as e:
            logger.error(f"종료 알림 전송 실패: {e}")
    
    # WebSocket 연결 정리
    if ws_client and ws_client.ws:
        try:
            ws_client.ws.close()
        except Exception as e:
            logger.error(f"WebSocket 종료 오류: {e}")
    
    logger.info("시스템이 안전하게 종료되었습니다.")
    sys.exit(0)

def main():
    global ws_client, telegram_bot, shutdown_requested
    
    parser = argparse.ArgumentParser(description='자동 매도 시스템')
    parser.add_argument('--mode', default='development', choices=['development', 'production'],
                        help='실행 모드 (development/production)')
    parser.add_argument('--debug', action='store_true', help='디버그 모드 활성화')
    
    args = parser.parse_args()
    
    setup_logging(args.debug)
    logger = logging.getLogger(__name__)
    
    # 신호 처리 등록
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # 설정 로드
        config = load_config(args.mode)
        logger.info(f"🚀 자동 매도 시스템 시작 - 모드: {args.mode}")
        
        # TokenManager 초기화
        token_manager = TokenManager(config)
        
        # TelegramBot 초기화
        if config.get('telegram_bot_token') and config.get('telegram_chat_id'):
            telegram_bot = TelegramBot(
                config['telegram_bot_token'], 
                config['telegram_chat_id']
            )
            
            # 시작 알림 전송
            telegram_bot.send_startup_notification()
            
            # 텔레그램 봇 폴링을 별도 스레드에서 실행
            def start_telegram_polling():
                try:
                    telegram_bot.start_polling()
                except Exception as e:
                    logger.error(f"텔레그램 봇 폴링 오류: {e}")
            
            telegram_thread = threading.Thread(target=start_telegram_polling, daemon=True)
            telegram_thread.start()
            logger.info("📱 텔레그램 봇이 시작되었습니다.")
        else:
            logger.warning("⚠️ 텔레그램 설정이 없어 알림 기능이 비활성화됩니다.")
        
        # 주문 콜백 함수
        def order_callback(execution_data):
            try:
                logger.info(f"📈 매수 체결 감지: {execution_data}")
                result = place_sell_order(config, token_manager, execution_data, telegram_bot)
                if result:
                    logger.info("✅ 매도 주문 성공")
                else:
                    logger.error("❌ 매도 주문 실패")
            except Exception as e:
                logger.error(f"주문 처리 중 오류: {e}")
        
        # WebSocket 클라이언트 초기화 및 실행
        ws_client = WebSocketClient(config, token_manager, order_callback)
        
        # 개선된 재연결 로직
        max_reconnect_attempts = config['system']['max_reconnect_attempts']
        reconnect_delay = config['system']['reconnect_delay']
        reconnect_attempts = 0
        
        while not shutdown_requested and reconnect_attempts < max_reconnect_attempts:
            try:
                logger.info(f"🔌 WebSocket 연결 시도 ({reconnect_attempts + 1}/{max_reconnect_attempts})")
                ws_client.connect()
                
                # 연결 성공 시 재시도 카운터 리셋
                reconnect_attempts = 0
                
                # WebSocket 연결이 끊어질 때까지 대기
                while not shutdown_requested and ws_client.is_connected():
                    time.sleep(1)
                
                if shutdown_requested:
                    break
                    
                logger.warning("🔌 WebSocket 연결이 끊어졌습니다.")
                
            except Exception as e:
                logger.error(f"❌ WebSocket 연결 오류: {e}")
            
            if shutdown_requested:
                break
                
            reconnect_attempts += 1
            
            if reconnect_attempts < max_reconnect_attempts:
                logger.info(f"⏳ {reconnect_delay}초 후 재연결 시도...")
                time.sleep(reconnect_delay)
            else:
                # 최대 재연결 횟수 초과 시 안전한 종료
                error_msg = f"❌ 최대 재연결 횟수({max_reconnect_attempts})를 초과했습니다."
                logger.critical(error_msg)
                
                if telegram_bot:
                    try:
                        telegram_bot.send_error_notification(
                            f"시스템 재연결 실패\n\n{error_msg}\n\n시스템이 안전하게 종료됩니다."
                        )
                    except Exception as e:
                        logger.error(f"오류 알림 전송 실패: {e}")
                
                # 안전한 종료를 위해 일정 시간 대기
                graceful_timeout = config['system'].get('graceful_shutdown_timeout', 30)
                logger.info(f"⏳ {graceful_timeout}초 후 시스템을 종료합니다...")
                time.sleep(graceful_timeout)
                break
        
        logger.info("🛑 자동 매도 시스템이 종료되었습니다.")
        
    except KeyboardInterrupt:
        logger.info("👤 사용자에 의한 종료")
    except Exception as e:
        error_msg = f"시스템 오류: {e}"
        logger.critical(error_msg)
        if telegram_bot:
            try:
                telegram_bot.send_error_notification(error_msg)
            except:
                pass
        sys.exit(1)

if __name__ == "__main__":
    main()