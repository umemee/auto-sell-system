# main.py - v2.0 기획서 통합 버전
#
# ✅ v2.0 변경 사항:
# 1. WebSocketClient 관련 코드 전체 제거 (v2.0 기획서: WebSocket 미사용)
# 2. TelegramOrderManager (시스템 B) 임포트 및 초기화
# 3. SmartOrderMonitor (시스템 A)와 TelegramOrderManager (시스템 B)가
#    하나의 DailyTradeCounter 인스턴스를 공유하도록 통합
# 4. 텔레그램 봇 시작 시 order_manager 주입
# 5. 시작 알림 메시지 v2.0으로 업데이트
# 6. 메인 루프에서 WebSocket 상태 로깅 제거
# 7. [개선] config.yaml에서 로그 파일 경로를 읽어오도록 수정
# 8. [보완] A->B 슬립 모드 연동 (주문 취소용)

import logging
import time
import signal
import sys
import argparse
import threading
import os
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

# v1.x 컴포넌트
from config import load_config
from auth import TokenManager
from smart_order_monitor import SmartOrderMonitor
from telegram_bot import TelegramBot
# from order import is_market_hours (v2.0: SmartOrderMonitor가 내부적으로 처리)

# 🆕 v2.0 신규 컴포넌트
from telegram_order_manager import TelegramOrderManager

# 전역 변수
shutdown_requested = False
telegram_bot = None
smart_monitor = None
telegram_order_manager = None # 🆕 v2.0

# 
# ↓↓↓ (수정 1) setup_logging: config 파라미터 추가 ↓↓↓
#
def setup_logging(debug=False, config=None):
    """로깅 설정 (v2.0 - config 연동)"""
    log_level = logging.DEBUG if debug else logging.INFO
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 🔴 [v2.0 수정] config.yaml에서 로그 파일 경로 읽기
    # 기획서 v2.0 (config.yaml 7. 로깅 설정)
    if config:
        log_file_path = config.get('logging', {}).get('file', {}).get('path', 'trading.log')
    else:
        log_file_path = 'trading.log' # config 로드 실패 시 기본값

    # 로그 디렉토리 자동 생성
    try:
        log_dir = os.path.dirname(log_file_path)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
            print(f"Log directory created: {log_dir}")
    except Exception as e:
        print(f"Warning: Failed to create log directory {log_dir}: {e}", file=sys.stderr)
            
    file_handler = RotatingFileHandler(
        log_file_path, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8'
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
#
# ↑↑↑ (수정 1) setup_logging 수정 완료 ↑↑↑
#

def emergency_stop(reason):
    """
    비상 정지 함수 (v2.0)
    """
    global shutdown_requested, telegram_bot, smart_monitor, telegram_order_manager
    
    logging.critical(f"🚨 긴급 시스템 종지 발동!")
    logging.critical(f"📋 종지 사유: {reason}")
    
    # 텔레그램 알림
    if telegram_bot and hasattr(telegram_bot, 'send_emergency_stop_notification'):
        try:
            telegram_bot.send_emergency_stop_notification(reason)
            logging.info("✅ 텔레그램 긴급 알림 전송 완료")
        except Exception as e:
            logging.error(f"❌ 텔레그램 알림 실패: {e}")
    
    # 시스템 안전 종료
    shutdown_requested = True
    
    try:
        if smart_monitor and hasattr(smart_monitor, 'stop'):
            smart_monitor.stop()
            logging.info("✅ 스마트 모니터 (시스템 A) 정리 완료")

        # 🆕 v2.0
        if telegram_order_manager and hasattr(telegram_order_manager, 'stop'):
            telegram_order_manager.stop()
            logging.info("✅ 텔레그램 주문 관리자 (시스템 B) 정리 완료")
            
        if telegram_bot and hasattr(telegram_bot, 'stop'):
            telegram_bot.stop()
            logging.info("✅ 텔레그램 봇 정리 완료")
            
    except Exception as e:
        logging.error(f"❌ 정리 중 오류: {e}")
    
    logging.critical("🛑 시스템 종료")
    sys.exit(1)

def signal_handler(signum, frame):
    """안전한 종료 처리"""
    global shutdown_requested, telegram_bot, smart_monitor, telegram_order_manager
    
    shutdown_requested = True
    logging.info(f"종료 신호 수신 (Signal: {signum}). 안전한 종료를 시작합니다...")
    
    try:
        if smart_monitor and hasattr(smart_monitor, 'stop'):
            smart_monitor.stop()
            logging.info("스마트 모니터(A)가 안전하게 종료되었습니다.")

        # 🆕 v2.0
        if telegram_order_manager and hasattr(telegram_order_manager, 'stop'):
            telegram_order_manager.stop()
            logging.info("텔레그램 주문 관리자(B)가 안전하게 종료되었습니다.")
            
        if telegram_bot and hasattr(telegram_bot, 'stop'):
            # 🆕 v2.0: 종료 시 통계 전달 시도
            trade_stats = None
            if smart_monitor and hasattr(smart_monitor, 'trade_counter'):
                if hasattr(smart_monitor.trade_counter, 'get_stats'):
                    trade_stats = smart_monitor.trade_counter.get_stats()
            
            telegram_bot.stop(trade_stats=trade_stats)
            logging.info("텔레그램 봇이 안전하게 종료되었습니다.")
            
        logging.info("시스템이 안전하게 종료되었습니다.")
        
    except Exception as e:
        logging.error(f"종료 정리 중 오류: {e}")
    
    sys.exit(0)

# ❌ [v2.0 제거] WebSocket 관련 함수 전체 제거

def start_telegram_bot(config):
    """✅ 텔레그램 봇 초기화 (v2.0)"""
    global telegram_bot
    try:
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        chat_id = os.getenv('TELEGRAM_CHAT_ID')
        
        if not bot_token or not chat_id:
            # config.yaml에서도 읽기 시도 (v1.1 호환성)
            telegram_config = config.get('telegram', {})
            bot_token = bot_token or telegram_config.get('bot_token')
            chat_id = chat_id or telegram_config.get('chat_id')

        if not bot_token or not chat_id:
            logging.warning("⚠️ 텔레그램 설정 누락 (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)")
            return None
        
        telegram_bot = TelegramBot(
            bot_token=bot_token,
            chat_id=chat_id,
            config=config
        )
        
        # ❌ [v2.0 수정] .start()는 order_manager 생성 후에 호출
        logging.info("✅ 텔레그램 봇이 초기화되었습니다. (시작 대기)")
        return telegram_bot
        
    except Exception as e:
        logging.warning(f"⚠️ 텔레그램 봇 초기화 실패 (선택사항): {e}")
        import traceback
        traceback.print_exc()
        return None

def adaptive_market_monitor(config, token_manager, telegram_bot):
    """
    적응형 시장 모니터 - 시장 상태 감시만 수행 (v1.2 유지)

    ✅ v2.0: SmartOrderMonitor가 모든 모드 전환을 전담하므로,
    이 함수는 시장 상태 로깅만 수행
    """
    global smart_monitor, shutdown_requested

    last_status = None
    
    # ℹ️ order.py에서 is_market_hours 임포트 필요
    try:
        from order import is_market_hours
    except ImportError:
        logging.error("❌ adaptive_market_monitor: order.py에서 is_market_hours를 찾을 수 없습니다.")
        return

    while not shutdown_requested:
        try:
            current_status = is_market_hours(config['trading']['timezone'])

            # 상태 변경 시 로그만 출력 (SmartOrderMonitor가 알아서 처리)
            if current_status != last_status:
                last_status = current_status
                logging.info(f"🕐 시장 상태 변경: {current_status} (SmartOrderMonitor가 자동 감지)")

            # 1분마다 상태 확인
            time.sleep(60)

        except Exception as e:
            logging.error(f"시장 모니터 오류: {e}")
            time.sleep(60)

def main():
    global shutdown_requested, telegram_bot, smart_monitor, telegram_order_manager
    
    parser = argparse.ArgumentParser(description='v2.0 통합 자동매매 시스템')
    parser.add_argument('--mode', choices=['development', 'production'],
                        default='production', help='실행 모드 (기본: production)')
    args = parser.parse_args()
    
    # 
    # ↓↓↓ (수정 2) 로깅 설정을 위해 config 로드 순서 변경 ↓↓↓
    #
    
    # 🔴 [v2.0 수정] 1. 로깅 설정을 위해 Config 먼저 로드
    config = None
    try:
        config = load_config(args.mode)
    except Exception as e:
        # 로깅 설정 전이므로 print 사용
        print(f"CRITICAL: 설정 파일 로드 실패: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # 🔴 [v2.0 수정] 2. 로깅 설정 (Config 전달)
    debug_mode = args.mode == 'development'
    setup_logging(debug=debug_mode, config=config) # config 전달
    
    #
    # ↑↑↑ (수정 2) 순서 변경 완료 ↑↑↑
    #

    # 3. 시그널 핸들러 등록
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # 4. 시스템 초기화
        logging.info(f"🚀 v2.0 통합 자동매매 시스템 시작 ({args.mode} 모드)")
        logging.info("✅ 기획서 v2.0 요구사항 준수")
        logging.info("✅ [A] 자동 감지 + [B] 텔레그램 주문 통합")
        logging.info("❌ WebSocket 미사용 (v2.0)")
        
        # ❌ [v2.0 제거] config = load_config(args.mode) - (위로 이동됨)
        
        # ℹ️ order.py에서 is_market_hours 임포트 필요
        try:
            from order import is_market_hours
            market_status = is_market_hours(config['trading']['timezone'])
        except ImportError:
            logging.error("❌ main: order.py에서 is_market_hours를 찾을 수 없습니다.")
            market_status = "unknown"
            
        logging.info(f"🕐 현재 시장 상태: {market_status}")
        
        # 1. 텔레그램 봇 초기화 (v2.0)
        telegram_bot = start_telegram_bot(config)
        
        # 2. 토큰 매니저 초기화
        token_manager = TokenManager(config, telegram_bot)
        
        # 3. 스마트 모니터 (시스템 A) 초기화
        # ℹ️ SmartOrderMonitor가 내부적으로 공유 DailyTradeCounter를 생성
        smart_monitor = SmartOrderMonitor(config, token_manager, telegram_bot)
        
        # 4. 🆕 v2.0 통합: 텔레그램 주문 관리자 (시스템 B) 초기화
        logging.info("🔧 v2.0 통합: 텔레그램 주문 관리자(B) 초기화...")
        
        # ℹ️ SmartOrderMonitor에서 생성된 '공유' 인스턴스를 가져옵니다.
        # (v2.0 기획서 6.1: 통합 제어 시스템)
        shared_trade_counter = smart_monitor.trade_counter
        
        telegram_order_manager = TelegramOrderManager(
            config=config,
            token_manager=token_manager,
            telegram_bot=telegram_bot,
            order_monitor=smart_monitor,       # 매수 후 [A]로 등록
            trade_counter=shared_trade_counter # [A]와 8회 제한 공유
        )
        logging.info("✅ [A] <-> [B] 통합 제어(TradeCounter) 연결 완료")
        
        # 5. 🆕 v2.0: 텔레그램 봇 시작 (OrderManager 주입)
        if telegram_bot:
            logging.info("🚀 텔레그램 봇 시작 (v2.0)...")
            telegram_bot.start(order_manager=telegram_order_manager)
        
        # 6. 🆕 v2.0: 텔레그램 주문 관리자 (시스템 B) 스레드 시작
        logging.info("🚀 텔레그램 주문 관리자 (시스템 B) 시작...")
        telegram_order_manager.start()
        
        # 7. 스마트 모니터 (시스템 A) 스레드 시작
        logging.info("🧠 스마트 오더 모니터 (시스템 A) 시작...")

        # 
        # ↓↓↓ [v2.0 수정] A-B 시스템 연동 코드 추가 ↓↓↓
        #
        # 기획서 7.4 (시나리오 4) 준수를 위해 시스템 A(smart_monitor)가
        # 시스템 B(telegram_order_manager)의 참조를 갖도록 연결합니다.
        # (수면 모드 시 주문 자동 취소용)
        if hasattr(smart_monitor, 'set_telegram_order_manager'):
            smart_monitor.set_telegram_order_manager(telegram_order_manager)
            logging.info("✅ [A] -> [B] 수면 모드 연동 완료")
        #
        # ↑↑↑ [v2.0 수정] 추가 완료 ↑↑↑
        #

        smart_monitor.start()
        
        logging.info("✅ SmartOrderMonitor가 모든 시장 상태(pre, regular, closed)를 전담합니다.")
        
        # 시작 알림 (v2.0)
        if telegram_bot:
            message = f"""
🚀 **v2.0 통합 자동매매 시스템 시작!**

• 모드: {args.mode}
• 상태: ✅ 실행중 (현재: {market_status})
• 수익률: **+{config.get('order_settings', {}).get('target_profit_rate', 6.0)}%** (v2.0)
• 제한: 일일 **{shared_trade_counter.MAX_TRADES}회** (통합)
• 폴링: 4초 (균일)

📋 **시스템 구성**
• [A] 자동 감지: 활성화 (KIS 앱 매수 감지)
• [B] 텔레그램: 활성화 (/buy 명령어)

❌ WebSocket 미사용 (v2.0)
"""
            if hasattr(telegram_bot, 'send_message'):
                telegram_bot.send_message(message.strip(), force=True)
        
        # 적응형 시장 모니터 스레드 시작 (단순 로깅용)
        market_monitor_thread = threading.Thread(
            target=adaptive_market_monitor,
            args=(config, token_manager, telegram_bot),
            daemon=True
        )
        market_monitor_thread.start()
        
        # 메인 상태 모니터링 루프
        logging.info("✅ v2.0 통합 시스템이 준비되었습니다.")
        
        status_count = 0
        last_stats_report = 0
        
        while not shutdown_requested:
            try:
                if status_count % 12 == 0:  # 1분마다 상태 출력
                    
                    # 스마트 모니터 통계
                    if smart_monitor and hasattr(smart_monitor, 'get_detailed_stats'):
                        stats = smart_monitor.get_detailed_stats()
                        
                        current_mode = stats.get('current_mode', 'N/A')
                        monitor_count = stats.get('monitoring_count', 0)
                        daily_calls = stats.get('daily_api_calls', 0)
                        hourly_calls = stats.get('hourly_api_calls', 0)
                        
                        # 🆕 v2.0: 텔레그램 주문 건수
                        tg_order_count = 0
                        if telegram_order_manager and hasattr(telegram_order_manager, 'get_pending_orders'):
                            tg_order_count = len(telegram_order_manager.get_pending_orders())

                        logging.info(
                            f"📊 상태: {current_mode} | [A]감시: {monitor_count}건 | [B]대기: {tg_order_count}건 | "
                            f"API(시간): {hourly_calls} | API(일일): {daily_calls}"
                        )
                        
                        # 10분마다 상세 통계 리포트
                        if status_count - last_stats_report >= 120:  # 10분
                            successful_detections = stats.get('successful_detections', 0)
                            rate_limit_errors = stats.get('rate_limit_violations', 0)
                            logging.info(
                                f"📈 상세통계 - 성공(REST): {successful_detections}회, "
                                f"Rate Limit: {rate_limit_errors}회"
                            )
                            last_stats_report = status_count
                    else:
                        logging.info(f"📊 상태: 로딩 중...")
                
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
        # 
        if telegram_bot:
            telegram_bot.send_emergency_stop_notification(f"시스템 초기화 실패: {e}")
        sys.exit(1)
        
    finally:
        # 정리 작업
        logging.info("🧹 v2.0 시스템 종료 정리 중...")
        try:
            if smart_monitor and hasattr(smart_monitor, 'get_detailed_stats'):
                final_stats = smart_monitor.get_detailed_stats()
                total_requests = final_stats.get('total_requests', 0)
                successful_detections = final_stats.get('successful_detections', 0)
                logging.info(f"📊 최종통계 - 총요청: {total_requests}, 성공감지: {successful_detections}")

            if smart_monitor and hasattr(smart_monitor, 'stop'):
                smart_monitor.stop()
                
            if telegram_order_manager and hasattr(telegram_order_manager, 'stop'):
                telegram_order_manager.stop()
                
            if telegram_bot and hasattr(telegram_bot, 'stop'):
                # 🆕 v2.0: 종료 시 통계 전달 시도
                trade_stats = None
                if 'shared_trade_counter' in locals() and hasattr(shared_trade_counter, 'get_stats'):
                    trade_stats = shared_trade_counter.get_stats() # Phase 4에서 구현될 함수
                elif smart_monitor and hasattr(smart_monitor, 'trade_counter') and hasattr(smart_monitor.trade_counter, 'get_stats'):
                    trade_stats = smart_monitor.trade_counter.get_stats()
                    
                telegram_bot.stop(trade_stats=trade_stats)
                
        except Exception as e:
            logging.error(f"종료 정리 중 오류: {e}")

if __name__ == "__main__":
    main()