# main.py - 수정된 전체 코드 (import 오류 수정)

import logging
import time
import signal
import sys
import argparse
import threading
from logging.handlers import RotatingFileHandler

from config import load_config
from auth import TokenManager

# ✅ 순환 import 해결을 위해 런타임 import로 변경
from websocket_client import WebSocketClient
from telegram_bot import TelegramBot
from smart_order_monitor import SmartOrderMonitor

# ✅ order.py에서 is_market_hours import (수정!)
from order import is_market_hours, place_sell_order

# 전역 변수
shutdown_requested = False
ws_client = None
telegram_bot = None
smart_monitor = None

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
    """안전한 종료 처리"""
    global shutdown_requested, ws_client, telegram_bot, smart_monitor
    
    shutdown_requested = True
    logging.info(f"종료 신호 수신 (Signal: {signum}). 안전한 종료를 시작합니다...")
    
    try:
        if smart_monitor and hasattr(smart_monitor, 'stop'):
            smart_monitor.stop()
            logging.info("스마트 모니터가 안전하게 종료되었습니다.")
            
        if ws_client and hasattr(ws_client, 'stop'):
            ws_client.stop()
            logging.info("WebSocket 연결이 안전하게 종료되었습니다.")
            
        if telegram_bot and hasattr(telegram_bot, 'stop'):
            telegram_bot.stop()
            logging.info("텔레그램 봇이 안전하게 종료되었습니다.")
            
        logging.info("시스템이 안전하게 종료되었습니다.")
        
    except Exception as e:
        logging.error(f"종료 정리 중 오류: {e}")
    
    sys.exit(0)

def handle_websocket_execution(execution_data, config, token_manager, telegram_bot, smart_monitor):
    """WebSocket 체결 데이터 처리 (정규장)"""
    try:
        logging.info(f"🔥 [정규장] WebSocket 체결 감지: {execution_data}")
        
        # 즉시 자동 매도 실행
        success = place_sell_order(config, token_manager, execution_data, telegram_bot)
        if success:
            logging.info(f"✅ [정규장] 즉시 자동 매도 성공: {execution_data['ticker']}")
        else:
            logging.error(f"❌ [정규장] 자동 매도 실패: {execution_data['ticker']}")
    except Exception as e:
        logging.error(f"WebSocket 체결 처리 중 오류: {e}")

def start_websocket_for_regular_hours(config, token_manager, telegram_bot, smart_monitor):
    """정규장 전용 WebSocket 시작"""
    global ws_client
    
    def message_handler(execution_data):
        handle_websocket_execution(execution_data, config, token_manager, telegram_bot, smart_monitor)
    
    ws_client = WebSocketClient(config, token_manager, message_handler)
    
    max_attempts = config['system']['max_reconnect_attempts']
    attempt = 0
    
    while not shutdown_requested and attempt < max_attempts:
        try:
            attempt += 1
            market_status = is_market_hours(config['trading']['timezone'])
            
            if market_status != 'regular':
                logging.info(f"⏸️ [정규장 아님] WebSocket 대기 중... (현재: {market_status})")
                time.sleep(60)
                continue
                
            logging.info(f"📌 [정규장] WebSocket 연결 시도 ({attempt}/{max_attempts})")
            ws_client.start()
            break
            
        except Exception as e:
            logging.error(f"WebSocket 연결 실패 ({attempt}/{max_attempts}): {e}")
            if attempt < max_attempts and not shutdown_requested:
                delay = min(config['system']['base_reconnect_delay'] * (2 ** (attempt - 1)), 300)
                logging.info(f"🔄 {delay}초 후 재시도합니다...")
                time.sleep(delay)

def start_smart_monitor(config, token_manager, telegram_bot):
    """스마트 모니터 시작"""
    global smart_monitor
    
    smart_monitor = SmartOrderMonitor(config, token_manager, telegram_bot)
    market_status = is_market_hours(config['trading']['timezone'])
    
    if market_status in ['premarket', 'aftermarket']:
        if hasattr(smart_monitor, 'start'):
            smart_monitor.start()
        logging.info(f"🧠 [장외] 스마트 폴링 시작 (현재: {market_status})")
    else:
        logging.info(f"⏸️ [정규장] 스마트 폴링 대기 중...")

def start_telegram_bot(config):
    """텔레그램 봇 시작"""
    global telegram_bot
    
    telegram_bot_token = config.get('telegram_bot_token')
    telegram_chat_id = config.get('telegram_chat_id')
    
    if telegram_bot_token and telegram_chat_id:
        telegram_bot = TelegramBot(telegram_bot_token, telegram_chat_id, config)
        
        # start 메서드가 있는지 확인
        if hasattr(telegram_bot, 'start'):
            telegram_bot.start()
        elif hasattr(telegram_bot, 'start_polling'):
            # start 메서드가 없다면 직접 폴링 시작
            polling_thread = threading.Thread(target=telegram_bot.start_polling, daemon=True)
            polling_thread.start()
        else:
            logging.warning("⚠️ 텔레그램 봇의 시작 메서드를 찾을 수 없습니다.")
            
        logging.info("📱 텔레그램 봇이 시작되었습니다.")
        return telegram_bot
    else:
        logging.warning("⚠️ 텔레그램 설정이 없어 알림 서비스를 시작하지 않습니다.")
        return None

def adaptive_market_monitor(config, token_manager, telegram_bot):
    """적응형 시장 모니터 - 시장 상태에 따른 서비스 자동 전환"""
    global ws_client, smart_monitor
    
    last_status = None
    websocket_thread = None
    websocket_running = False
    
    while not shutdown_requested:
        try:
            current_status = is_market_hours(config['trading']['timezone'])
            
            if current_status != last_status:
                logging.info(f"🕐 시장 상태 변경: {last_status} → {current_status}")
                
                if current_status == 'regular':
                    # 정규장 시작: WebSocket 활성화, 스마트 폴링 중지
                    logging.info("🔄 정규장 시작 - WebSocket 모드로 전환")
                    
                    if smart_monitor and hasattr(smart_monitor, 'is_running') and smart_monitor.is_running:
                        if hasattr(smart_monitor, 'stop'):
                            smart_monitor.stop()
                        logging.info("⏸️ 스마트 폴링 중지됨")

                    # ✅ WebSocket 중복 방지
                    if not websocket_running:
                        # 기존 WebSocket 정리
                        if ws_client:
                            try:
                                if hasattr(ws_client, 'stop'):
                                    ws_client.stop()
                                logging.info("🔄 기존 WebSocket 정리")
                            except Exception as e:
                                logging.warning(f"⚠️ 기존 WebSocket 정리 중 오류: {e}")
                        
                        # 새 WebSocket 시작
                        websocket_thread = threading.Thread(
                            target=start_websocket_for_regular_hours,
                            args=(config, token_manager, telegram_bot, smart_monitor),
                            daemon=True,
                            name="WebSocketThread"
                        )
                        websocket_thread.start()
                        websocket_running = True
                        logging.info("✅ WebSocket 스레드 시작됨")
                    else:
                        logging.info("ℹ️ WebSocket 이미 실행 중, 건너뜀")
                
                elif current_status in ['premarket', 'aftermarket']:
                    logging.info(f"🔄 {current_status} 시작 - 스마트 폴링 모드로 전환")
                    
                    # ✅ WebSocket 중지
                    if websocket_running:
                        if ws_client and hasattr(ws_client, 'stop'):
                            try:
                                ws_client.stop()
                                logging.info("🛑 WebSocket 중지됨")
                            except Exception as e:
                                logging.warning(f"⚠️ WebSocket 중지 중 오류: {e}")
                        
                        websocket_running = False
                        
                        # WebSocket 스레드 종료 대기
                        if websocket_thread and websocket_thread.is_alive():
                            websocket_thread.join(timeout=5)
                            if websocket_thread.is_alive():
                                logging.warning("⚠️ WebSocket 스레드가 5초 내에 종료되지 않음")
                    
                    # 스마트 모니터 시작
                    if smart_monitor and hasattr(smart_monitor, 'is_running') and not smart_monitor.is_running:
                        if hasattr(smart_monitor, 'start'):
                            smart_monitor.start()
                        logging.info("🧠 스마트 폴링 활성화됨")
                
                elif current_status == 'closed':
                    logging.info("🔄 장 마감 - 대기 모드")
                    
                    # WebSocket 중지
                    if websocket_running:
                        if ws_client and hasattr(ws_client, 'stop'):
                            ws_client.stop()
                        websocket_running = False
                    
                    # 스마트 모니터 중지
                    if smart_monitor and hasattr(smart_monitor, 'is_running') and smart_monitor.is_running:
                        if hasattr(smart_monitor, 'stop'):
                            smart_monitor.stop()
                        logging.info("⏸️ 스마트 폴링 중지됨")
                
                last_status = current_status
            
            # 1분마다 상태 확인
            time.sleep(60)
            
        except Exception as e:
            logging.error(f"시장 모니터 오류: {e}")
            time.sleep(60)

def main():
    global shutdown_requested, ws_client, telegram_bot, smart_monitor
    
    parser = argparse.ArgumentParser(description='스마트 하이브리드 자동매매 시스템')
    parser.add_argument('--mode', choices=['development', 'production'],
                        default='development', help='실행 모드')
    args = parser.parse_args()
    
    # 로깅 설정
    debug_mode = args.mode == 'development'
    setup_logging(debug=debug_mode)
    
    # 시그널 핸들러 등록
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # 시스템 초기화
        logging.info(f"🚀 스마트 하이브리드 자동매매 시스템 시작 ({args.mode} 모드)")
        logging.info("💡 Rate Limit 안전 모드, 적응형 폴링, WebSocket 자동 전환")
        
        config = load_config(args.mode)
        market_status = is_market_hours(config['trading']['timezone'])
        logging.info(f"🕐 현재 시장 상태: {market_status}")
        
        # 토큰 매니저 초기화
        telegram_bot = start_telegram_bot(config)
        token_manager = TokenManager(config, telegram_bot)
        
        # 스마트 모니터 초기화 (항상 준비)
        smart_monitor = SmartOrderMonitor(config, token_manager, telegram_bot)
        
        # 시작 알림
        if telegram_bot:
            message = f"🚀 스마트 자동매매 시작!\n🕐 시장상태: {market_status}\n🧠 Rate Limit 안전모드\n⚡ 적응형 폴링 활성화"
            if hasattr(telegram_bot, 'send_message'):
                telegram_bot.send_message(message)
        
        # 현재 시장 상태에 따른 초기 서비스 시작
        if market_status == 'regular':
            # 정규장: WebSocket 시작
            logging.info("📌 정규장 감지 - WebSocket 모드로 시작")
            ws_thread = threading.Thread(
                target=start_websocket_for_regular_hours,
                args=(config, token_manager, telegram_bot, smart_monitor),
                daemon=True
            )
            ws_thread.start()
            
        elif market_status in ['premarket', 'aftermarket']:
            # 장외: 스마트 폴링 시작
            logging.info(f"🧠 {market_status} 감지 - 스마트 폴링 모드로 시작")
            if hasattr(smart_monitor, 'start'):
                smart_monitor.start()
        else:
            logging.info("⏸️ 장 마감 시간 - 대기 모드로 시작")
        
        # 적응형 시장 모니터 스레드 시작
        market_monitor_thread = threading.Thread(
            target=adaptive_market_monitor,
            args=(config, token_manager, telegram_bot),
            daemon=True
        )
        market_monitor_thread.start()
        
        # 메인 상태 모니터링 루프
        logging.info("✅ 스마트 하이브리드 시스템이 준비되었습니다.")
        logging.info("💡 시장 시간에 따라 WebSocket/스마트 폴링 모드가 자동 전환됩니다.")
        
        status_count = 0
        last_stats_report = 0
        
        while not shutdown_requested:
            try:
                if status_count % 12 == 0:  # 1분마다 상태 출력
                    market_status = is_market_hours(config['trading']['timezone'])
                    ws_status = "연결됨" if ws_client and hasattr(ws_client, 'is_connected') and ws_client.is_connected() else "대기 중"
                    monitor_count = smart_monitor.get_monitoring_count() if smart_monitor and hasattr(smart_monitor, 'get_monitoring_count') else 0
                    
                    # 스마트 모니터 통계
                    if smart_monitor and hasattr(smart_monitor, 'get_detailed_stats'):
                        stats = smart_monitor.get_detailed_stats()
                        api_usage = stats.get('utilization_pct', 0)
                        total_requests = stats.get('total_requests', 0)
                        logging.info(f"📊 상태: {market_status} | WS: {ws_status} | 모니터링: {monitor_count}건 | API: {api_usage} | 이요청: {total_requests}")
                        
                        # 10분마다 상세 통계 리포트
                        if status_count - last_stats_report >= 120:  # 10분
                            successful_detections = stats.get('successful_detections', 0)
                            rate_limit_errors = stats.get('rate_limit_errors', 0)
                            logging.info(f"📈 상세통계 - 성공감지: {successful_detections}회, Rate Limit 오류: {rate_limit_errors}회")
                            last_stats_report = status_count
                    else:
                        logging.info(f"📊 상태: {market_status} | WS: {ws_status} | 모니터링: {monitor_count}건")
                
                status_count += 1
                time.sleep(5)
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                logging.error(f"메인 루프 오류: {e}")
                time.sleep(5)
                
    except Exception as e:
        logging.error(f"시스템 초기화 실패: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
        
    finally:
        # 정리 작업
        logging.info("🧹 스마트 시스템 종료 정리 중...")
        try:
            if smart_monitor and hasattr(smart_monitor, 'get_detailed_stats'):
                final_stats = smart_monitor.get_detailed_stats()
                total_requests = final_stats.get('total_requests', 0)
                successful_detections = final_stats.get('successful_detections', 0)
                logging.info(f"📊 최종통계 - 이요청: {total_requests}, 성공감지: {successful_detections}")
                
            if smart_monitor and hasattr(smart_monitor, 'stop'):
                smart_monitor.stop()
            if ws_client and hasattr(ws_client, 'stop'):
                ws_client.stop()
            if telegram_bot and hasattr(telegram_bot, 'stop'):
                telegram_bot.stop()
                
        except Exception as e:
            logging.error(f"종료 정리 중 오류: {e}")

if __name__ == "__main__":
    main()