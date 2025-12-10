# main.py - v3.0 완전 자동매매 시스템 통합 버전
#
# ✅ v3.0 변경 사항:
# 1. RankingUpdater, AutoTrader, OrderExecutor 임포트 및 초기화
# 2. config.auto_trader.enabled 체크로 v3.0 모드 분기
# 3. v3.0 모드: 완전 자동매매 (50MA 터치 전략)
# 4. v1.x/v2.0 모드: 기존 자동매도 시스템 (호환성 유지)
# 5. DailyTradeCounter v3.0 (진입/청산 구분)
#
# ✅ v2.0 변경 사항 (유지):
# 1. WebSocketClient 관련 코드 전체 제거
# 2. TelegramOrderManager (시스템 B) 통합
# 3. SmartOrderMonitor (시스템 A)와 DailyTradeCounter 공유
# 4. 주말(토, 일) 실행 방지 로직

import logging
import time
import signal
import sys
import argparse
import threading
import os
from datetime import datetime
from pytz import timezone
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

# v1.x/v2.0 컴포넌트
from config import load_config
from auth import TokenManager
from smart_order_monitor import SmartOrderMonitor
from telegram_bot import TelegramBot

# v2.0 컴포넌트
from telegram_order_manager import TelegramOrderManager

# 🆕 v3.0 컴포넌트
try:
    from ranking_updater import RankingUpdater
    from auto_trader import AutoTrader
    from order import OrderExecutor
    V3_AVAILABLE = True
except ImportError as e:
    logging.warning(f"⚠️ v3.0 컴포넌트 임포트 실패: {e}")
    V3_AVAILABLE = False

# 전역 변수
shutdown_requested = False
telegram_bot = None
smart_monitor = None
telegram_order_manager = None

# 🆕 v3.0 전역 변수
ranking_updater = None
auto_trader = None
order_executor = None

def setup_logging(debug=False, config=None):
    """로깅 설정 (v3.0)"""
    log_level = logging.DEBUG if debug else logging.INFO
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # config.yaml에서 로그 파일 경로 읽기
    if config:
        log_file_path = config.get('logging', {}).get('file', {}).get('path', 'trading.log')
    else:
        log_file_path = 'trading.log'

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

def emergency_stop(reason):
    """
    비상 정지 함수 (v3.0)
    """
    global shutdown_requested, telegram_bot, smart_monitor, telegram_order_manager
    global ranking_updater, auto_trader, order_executor
    
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
        # 🆕 v3.0 컴포넌트 정리
        if auto_trader and hasattr(auto_trader, 'stop'):
            auto_trader.stop()
            logging.info("✅ 자동 트레이더 (v3.0) 정리 완료")
        
        if ranking_updater and hasattr(ranking_updater, 'stop'):
            ranking_updater.stop()
            logging.info("✅ 랭킹 업데이터 (v3.0) 정리 완료")
        
        # v1.x/v2.0 컴포넌트 정리
        if smart_monitor and hasattr(smart_monitor, 'stop'):
            smart_monitor.stop()
            logging.info("✅ 스마트 모니터 (시스템 A) 정리 완료")

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
    """안전한 종료 처리 (v3.0)"""
    global shutdown_requested, telegram_bot, smart_monitor, telegram_order_manager
    global ranking_updater, auto_trader, order_executor
    
    shutdown_requested = True
    logging.info(f"종료 신호 수신 (Signal: {signum}). 안전한 종료를 시작합니다...")
    
    try:
        # 🆕 v3.0 컴포넌트 종료
        if auto_trader and hasattr(auto_trader, 'stop'):
            auto_trader.stop()
            logging.info("자동 트레이더(v3.0)가 안전하게 종료되었습니다.")
        
        if ranking_updater and hasattr(ranking_updater, 'stop'):
            ranking_updater.stop()
            logging.info("랭킹 업데이터(v3.0)가 안전하게 종료되었습니다.")
        
        # v1.x/v2.0 컴포넌트 종료
        if smart_monitor and hasattr(smart_monitor, 'stop'):
            smart_monitor.stop()
            logging.info("스마트 모니터(A)가 안전하게 종료되었습니다.")

        if telegram_order_manager and hasattr(telegram_order_manager, 'stop'):
            telegram_order_manager.stop()
            logging.info("텔레그램 주문 관리자(B)가 안전하게 종료되었습니다.")
            
        if telegram_bot and hasattr(telegram_bot, 'stop'):
            # 종료 시 통계 전달 시도
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

def start_telegram_bot(config):
    """✅ 텔레그램 봇 초기화 (v3.0)"""
    global telegram_bot
    try:
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        chat_id = os.getenv('TELEGRAM_CHAT_ID')
        
        if not bot_token or not chat_id:
            # config.yaml에서도 읽기 시도
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
        
        logging.info("✅ 텔레그램 봇이 초기화되었습니다. (시작 대기)")
        return telegram_bot
        
    except Exception as e:
        logging.warning(f"⚠️ 텔레그램 봇 초기화 실패 (선택사항): {e}")
        import traceback
        traceback.print_exc()
        return None

def adaptive_market_monitor(config, token_manager, telegram_bot):
    """
    적응형 시장 모니터 - 시장 상태 감시만 수행 (v3.0 유지)
    """
    global smart_monitor, shutdown_requested

    last_status = None
    
    try:
        from order import is_market_hours
    except ImportError:
        logging.error("❌ adaptive_market_monitor: order.py에서 is_market_hours를 찾을 수 없습니다.")
        return

    while not shutdown_requested:
        try:
            current_status = is_market_hours(config['trading']['timezone'])

            # 상태 변경 시 로그만 출력
            if current_status != last_status:
                last_status = current_status
                logging.info(f"🕐 시장 상태 변경: {current_status}")
            
            time.sleep(60)
            
        except Exception as e:
            logging.error(f"❌ adaptive_market_monitor 오류: {e}")
            time.sleep(60)

def main():
    global shutdown_requested, telegram_bot, smart_monitor, telegram_order_manager
    global ranking_updater, auto_trader, order_executor
    
    # 1. 환경변수 로드
    load_dotenv()
    
    # 2. 명령행 인자 파싱
    parser = argparse.ArgumentParser(description='해외주식 자동매매 시스템 v3.0')
    parser.add_argument('--mode', choices=['production', 'test'], default='production',
                       help='실행 모드: production(실전) 또는 test(모의)')
    parser.add_argument('--debug', action='store_true', help='디버그 모드 활성화')
    args = parser.parse_args()
    
    # config 먼저 로드 (logging에 필요)
    config = load_config(args.mode)
    
    # logging 설정 (config 전달)
    setup_logging(args.debug, config)
    
    # 🔴 [v3.0] v3.0 모드 체크
    v3_enabled = config.get('auto_trader', {}).get('enabled', False)
    
    if v3_enabled and not V3_AVAILABLE:
        logging.error("❌ v3.0 모드가 활성화되었지만 필요한 컴포넌트를 임포트할 수 없습니다.")
        logging.error("ranking_updater.py, auto_trader.py가 존재하는지 확인하세요.")
        sys.exit(1)
    
    # 주말(토, 일) 실행 방지 로직
    try:
        tz_name = config.get('order_settings', {}).get('timezone', 'US/Eastern')
        tz = timezone(tz_name)
        now = datetime.now(tz)
        
        if now.weekday() >= 5:
            day_name = "토요일" if now.weekday() == 5 else "일요일"
            logging.info(f"🌴 오늘은 {day_name}({now.date()})입니다. 주말이므로 시스템을 시작하지 않습니다.")
            logging.info("🛑 주말 실행 방지 로직에 의해 종료합니다. (sys.exit(0))")
            sys.exit(0)
            
    except SystemExit:
        sys.exit(0)
    except Exception as e:
        logging.warning(f"⚠️ 주말 체크 중 오류 발생 (무시하고 진행): {e}")

    # 시그널 핸들러 등록
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # 시스템 초기화
        if v3_enabled:
            logging.info(f"🚀 v3.0 완전 자동매매 시스템 시작 ({args.mode} 모드)")
            logging.info("✅ 기획서 v3.0 요구사항 준수")
            logging.info("✅ 50MA 터치 전략 자동 매매")
        else:
            logging.info(f"🚀 v2.0 통합 자동매매 시스템 시작 ({args.mode} 모드)")
            logging.info("✅ 기획서 v2.0 요구사항 준수")
            logging.info("✅ [A] 자동 감지 + [B] 텔레그램 주문 통합")
        
        logging.info("❌ WebSocket 미사용")
        
        try:
            from order import is_market_hours
            market_status = is_market_hours(config['trading']['timezone'])
        except ImportError:
            logging.error("❌ main: order.py에서 is_market_hours를 찾을 수 없습니다.")
            market_status = "unknown"
            
        logging.info(f"🕐 현재 시장 상태: {market_status}")
        
        # 1. 텔레그램 봇 초기화
        telegram_bot = start_telegram_bot(config)
        
        # 2. 토큰 매니저 초기화
        token_manager = TokenManager(config, telegram_bot)
        
        # ✅ [수정] 시스템 시작 전 토큰 발급
        logging.info("🔑 Access Token 발급 중...")
        try:
            token_manager.issue_token()
            access_token = token_manager.get_access_token()
            
            if not access_token:
                logging.error("❌ 토큰 발급 실패, 시스템 종료")
                sys.exit(1)
            
            logging.info("✅ Access Token 발급 완료")
        except Exception as e:
            logging.error(f"❌ 토큰 발급 오류: {e}")
            sys.exit(1)

        # 3. 스마트 모니터 (시스템 A) 초기화
        smart_monitor = SmartOrderMonitor(config, token_manager, telegram_bot)
        shared_trade_counter = smart_monitor.trade_counter
        
        # 4. v2.0: 텔레그램 주문 관리자 (시스템 B) 초기화
        logging.info("🔧 텔레그램 주문 관리자(B) 초기화...")
        telegram_order_manager = TelegramOrderManager(
            config=config,
            token_manager=token_manager,
            telegram_bot=telegram_bot,
            order_monitor=smart_monitor,
            trade_counter=shared_trade_counter
        )
        logging.info("✅ [A] <-> [B] 통합 제어(TradeCounter) 연결 완료")
        
        # 🆕 5. v3.0 컴포넌트 초기화 (enabled=true인 경우만)
        if v3_enabled:
            logging.info("🔧 v3.0 완전 자동매매 컴포넌트 초기화...")
            
            # 5-1. OrderExecutor 초기화
            order_executor = OrderExecutor(
                config=config,
                token_manager=token_manager,
                telegram_bot=telegram_bot,
                auto_trader=None  # 나중에 주입
            )
            logging.info("✅ OrderExecutor 초기화 완료")
            
            # 5-2. RankingUpdater 초기화
            ranking_updater = RankingUpdater(
                config=config,
                token_manager=token_manager,
            )
            logging.info("✅ RankingUpdater 초기화 완료")
            
            # 5-3. AutoTrader 초기화
            auto_trader = AutoTrader(
                config=config,
                ranking_updater=ranking_updater,
                order_executor=order_executor,
                order_monitor=smart_monitor,
                trade_counter=shared_trade_counter,
                telegram_bot=telegram_bot
            )
            logging.info("✅ AutoTrader 초기화 완료")
            
            # 5-4. OrderExecutor에 AutoTrader 참조 주입 (콜백용)
            order_executor.auto_trader = auto_trader
            logging.info("✅ OrderExecutor <-> AutoTrader 연결 완료")
        
        # 6. 텔레그램 봇 시작 (OrderManager 주입)
        if telegram_bot:
            logging.info("🚀 텔레그램 봇 시작...")
            telegram_bot.start(order_manager=telegram_order_manager)
        
        # 7. 텔레그램 주문 관리자 (시스템 B) 스레드 시작
        logging.info("🚀 텔레그램 주문 관리자 (시스템 B) 시작...")
        telegram_order_manager.start()
        
        # 8. 스마트 모니터 (시스템 A) 스레드 시작
        logging.info("🧠 스마트 오더 모니터 (시스템 A) 시작...")
        
        # A-B 시스템 연동
        if hasattr(smart_monitor, 'set_telegram_order_manager'):
            smart_monitor.set_telegram_order_manager(telegram_order_manager)
            logging.info("✅ [A] -> [B] 수면 모드 연동 완료")

        smart_monitor.start()
        logging.info("✅ SmartOrderMonitor가 모든 시장 상태를 전담합니다.")
        
        # 🆕 9. v3.0: AutoTrader 시작 (enabled=true인 경우만)
        if v3_enabled and auto_trader:
            logging.info("🤖 AutoTrader (v3.0) 시작...")
            auto_trader.start()
            logging.info("✅ 완전 자동매매 활성화 (50MA 터치 전략)")
        
        # 시작 알림
        if telegram_bot:
            if v3_enabled:
                # v3.0 시작 알림
                message = f"""
🚀 **v3.0 완전 자동매매 시스템 시작!**

- 모드: {args.mode}
- 상태: ✅ 실행중 (현재: {market_status})
- 전략: **50MA 터치 자동매매**
- 손절: **{config['auto_trader']['stop_loss']}%**
- 익절: **+{config['auto_trader']['take_profit']}%**
- 일일 한도: 진입 **{shared_trade_counter.MAX_ENTRIES}회** / 청산 **{shared_trade_counter.MAX_EXITS}회**

📋 **시스템 구성**
- [v3.0] 완전 자동: 활성화 (TOP 3 감시 중)
- [A] 자동 감지: 활성화 (v1.x/v2.0 호환)
- [B] 텔레그램: 활성화 (/buy 명령어)

⚙️ **설정**
- 랭킹 업데이트: 1시간마다
- 감시 간격: 4초
- 최대 감시: 8개 종목

❌ WebSocket 미사용
"""
            else:
                # v2.0 시작 알림
                message = f"""
🚀 **v2.0 통합 자동매매 시스템 시작!**

- 모드: {args.mode}
- 상태: ✅ 실행중 (현재: {market_status})
- 수익률: **+{config.get('order_settings', {}).get('target_profit_rate', 6.0)}%**
- 제한: 일일 **{shared_trade_counter.MAX_TRADES}회** (통합)
- 폴링: 4초 (균일)

📋 **시스템 구성**
- [A] 자동 감지: 활성화 (KIS 앱 매수 감지)
- [B] 텔레그램: 활성화 (/buy 명령어)

❌ WebSocket 미사용
"""
            
            if hasattr(telegram_bot, 'send_message'):
                telegram_bot.send_message(message.strip(), force=True)
        
        # 적응형 시장 모니터 스레드 시작
        market_monitor_thread = threading.Thread(
            target=adaptive_market_monitor,
            args=(config, token_manager, telegram_bot),
            daemon=True
        )
        market_monitor_thread.start()
        
        # 메인 상태 모니터링 루프
        if v3_enabled:
            logging.info("✅ v3.0 완전 자동매매 시스템이 준비되었습니다.")
        else:
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
                        
                        # 텔레그램 주문 건수
                        tg_order_count = 0
                        if telegram_order_manager and hasattr(telegram_order_manager, 'get_pending_orders'):
                            tg_order_count = len(telegram_order_manager.get_pending_orders())
                        
                        # 🆕 v3.0: AutoTrader 상태
                        if v3_enabled and auto_trader:
                            watch_count = len(auto_trader.watch_list) if hasattr(auto_trader, 'watch_list') else 0
                            excluded_count = len(auto_trader.permanently_excluded) if hasattr(auto_trader, 'permanently_excluded') else 0
                            
                            logging.info(
                                f"📊 [v3.0] {current_mode} | "
                                f"감시: {watch_count}개 | 제외: {excluded_count}개 | "
                                f"[A]감시: {monitor_count}건 | [B]대기: {tg_order_count}건 | "
                                f"API(시간): {hourly_calls} | API(일일): {daily_calls}"
                            )
                        else:
                            logging.info(
                                f"📊 상태: {current_mode} | [A]감시: {monitor_count}건 | [B]대기: {tg_order_count}건 | "
                                f"API(시간): {hourly_calls} | API(일일): {daily_calls}"
                            )
                        
                        # 10분마다 상세 통계 리포트
                        if status_count - last_stats_report >= 120:
                            successful_detections = stats.get('successful_detections', 0)
                            rate_limit_errors = stats.get('rate_limit_violations', 0)
                            
                            # 진입/청산 통계
                            trade_stats = shared_trade_counter.get_stats()
                            
                            logging.info(
                                f"📈 상세통계 - 성공(REST): {successful_detections}회, "
                                f"Rate Limit: {rate_limit_errors}회, "
                                f"진입: {trade_stats['entry']}/{trade_stats['max_entries']}회, "
                                f"청산: {trade_stats['exit']}/{trade_stats['max_exits']}회"
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
        
        if telegram_bot:
            telegram_bot.send_emergency_stop_notification(f"시스템 초기화 실패: {e}")
        sys.exit(1)
        
    finally:
        # 정리 작업
        if v3_enabled:
            logging.info("🧹 v3.0 시스템 종료 정리 중...")
        else:
            logging.info("🧹 v2.0 시스템 종료 정리 중...")
        
        try:
            # 🆕 v3.0 컴포넌트 정리
            if auto_trader and hasattr(auto_trader, 'stop'):
                auto_trader.stop()
            
            if ranking_updater and hasattr(ranking_updater, 'stop'):
                ranking_updater.stop()
            
            # v1.x/v2.0 컴포넌트 정리
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
                # 종료 시 통계 전달
                trade_stats = None
                if 'shared_trade_counter' in locals() and hasattr(shared_trade_counter, 'get_stats'):
                    trade_stats = shared_trade_counter.get_stats()
                elif smart_monitor and hasattr(smart_monitor, 'trade_counter') and hasattr(smart_monitor.trade_counter, 'get_stats'):
                    trade_stats = smart_monitor.trade_counter.get_stats()
                    
                telegram_bot.stop(trade_stats=trade_stats)
                
        except Exception as e:
            logging.error(f"종료 정리 중 오류: {e}")

if __name__ == "__main__":
    main()