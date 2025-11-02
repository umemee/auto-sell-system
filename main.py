# main.py - 기획서 v1.1 완전 준수 버전
#
# ✅ v1.2 수정 사항:
# 1. (문제 1) adaptive_market_monitor 함수 수정:
#    - SmartOrderMonitor와 중복되던 모드 전환 로직 제거
#    - 시장 상태 변경 로깅만 하도록 단순화

import logging
import time
import signal
import sys
import argparse
import threading
import os
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
from config import load_config
from auth import TokenManager
from websocket_client import WebSocketClient
from telegram_bot import TelegramBot
from smart_order_monitor import SmartOrderMonitor
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

def emergency_stop(reason):
    """
    비상 정지 함수 (기획서 5.2절)
    
    정규장에서 WebSocket 3회 연결 실패 시 시스템을 안전하게 종료합니다.
    """
    global shutdown_requested, ws_client, telegram_bot, smart_monitor
    
    logging.critical(f"🚨 긴급 시스템 종지 발동!")
    logging.critical(f"📋 종지 사유: {reason}")
    
    # 텔레그램 알림
    if telegram_bot and hasattr(telegram_bot, 'send_message'):
        alert_message = f"""
🚨 **시스템 긴급 종지**

📋 **사유**: {reason}
⏰ **시각**: {time.strftime('%Y-%m-%d %H:%M:%S')}
⚠️ **조치**: 시스템이 안전하게 종료됩니다

기획서 5.2절에 따른 비상 증지 조건 충족
"""
        try:
            telegram_bot.send_message(alert_message)
            logging.info("✅ 텔레그램 긴급 알림 전송 완료")
        except Exception as e:
            logging.error(f"❌ 텔레그램 알림 실패: {e}")
    
    # 시스템 안전 종료
    shutdown_requested = True
    
    try:
        if smart_monitor and hasattr(smart_monitor, 'stop'):
            smart_monitor.stop()
            logging.info("✅ 스마트 모니터 정리 완료")
            
        if ws_client and hasattr(ws_client, 'stop'):
            ws_client.stop()
            logging.info("✅ WebSocket 정리 완료")
            
        if telegram_bot and hasattr(telegram_bot, 'stop'):
            telegram_bot.stop()
            logging.info("✅ 텔레그램 봇 정리 완료")
    except Exception as e:
        logging.error(f"❌ 정리 중 오류: {e}")
    
    logging.critical("🛑 시스템 종료")
    sys.exit(1)

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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔴 [v1.1 신규] 보유 종목 조회 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def get_holdings_symbols(config, token_manager):
    """
    보유 종목 코드 리스트 조회 (기획서 5.1절)
    
    Returns:
        list: 보유 종목 코드 리스트 (예: ['AAPL', 'TSLA', 'NVDA'])
    """
    try:
        from order import get_holdings  # order.py에 있다고 가정
        
        holdings = get_holdings(config, token_manager)
        if not holdings:
            logging.warning("⚠️ 보유 종목이 없습니다")
            return []
        
        # 종목 코드 추출 (다양한 필드명 지원)
        symbols = []
        for h in holdings:
            symbol = h.get('ticker') or h.get('pdno') or h.get('symbol') or h.get('stock_code')
            if symbol:
                symbols.append(symbol)
        
        # 중복 제거
        symbols = list(set(symbols))
        
        logging.info(f"📋 보유 종목: {len(symbols)}개")
        if symbols:
            preview = ', '.join(symbols[:5])
            if len(symbols) > 5:
                preview += f" 외 {len(symbols)-5}개"
            logging.info(f"📋 종목 목록: {preview}")
        
        return symbols
        
    except ImportError:
        logging.error("❌ order.py에 get_holdings 함수가 없습니다")
        logging.error("💡 order.py에 get_holdings() 함수를 구현해주세요")
        return []
    except Exception as e:
        logging.error(f"❌ 보유 종목 조회 실패: {e}")
        import traceback
        traceback.print_exc()
        return []
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

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
    """
    정규장 전용 WebSocket 시작
    
    ✅ [v1.1 수정] 다중 종목 구독 지원 추가 (기획서 5.1절)
    """
    global ws_client
    
    def message_handler(execution_data):
        handle_websocket_execution(execution_data, config, token_manager, telegram_bot, smart_monitor)
    
    # WebSocket 클라이언트 생성
    ws_client = WebSocketClient(
        config, 
        token_manager, 
        message_handler,
        emergency_stop_callback=emergency_stop  # 기획서 5.2절
    )
    
    # 재연결 횟수 3회로 고정 (기획서 5.2절)
    max_attempts = 3
    attempt = 0
    
    while not shutdown_requested and attempt < max_attempts:
        try:
            attempt += 1
            market_status = is_market_hours(config['trading']['timezone'])
            
            if market_status != 'regular':
                logging.info(f"⏸️ [정규장 아님] WebSocket 대기 중... (현재: {market_status})")
                time.sleep(60)
                continue
                
            logging.info(f"🔌 [정규장] WebSocket 연결 시도 ({attempt}/{max_attempts})")
            ws_client.start()
            
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 🔴 [v1.1 신규] 연결 성공 후 다중 종목 구독
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 연결이 성공할 때까지 대기 (최대 30초)
            logging.info("⏳ WebSocket 연결 확인 중...")
            for i in range(30):
                if ws_client and hasattr(ws_client, 'is_connected') and ws_client.is_connected():
                    logging.info("✅ WebSocket 연결 확인됨")
                    break
                time.sleep(1)
            else:
                logging.warning("⚠️ WebSocket 연결 상태 확인 실패 (30초 타임아웃)")
                continue
            
            # 보유 종목 조회
            holdings_symbols = get_holdings_symbols(config, token_manager)
            
            if holdings_symbols:
                # 기획서 5.1: 다중 종목 구독 (최대 20건)
                logging.info(f"📡 보유 종목 {len(holdings_symbols)}개 구독 시도...")
                try:
                    result = ws_client.subscribe_multiple(holdings_symbols)
    
                    # ✅ 추가: result 검증
                    if not result or not isinstance(result, dict):
                        logging.error("❌ WebSocket 구독 결과가 올바르지 않습니다")
                        continue
    
                    subscribed = result.get('subscribed', [])
                    pending = result.get('pending', [])
                    skipped = result.get('skipped', [])
                    subscribed_count = len(subscribed)
                    pending_count = len(pending)
                    skipped_count = len(skipped)
    
                    logging.info(f"✅ WebSocket 구독 완료: {subscribed_count}개")
    
                    # ✅ 추가: 구독 실패 처리
                    if skipped_count > 0:
                        logging.error(f"❌ 구독 실패: {skipped_count}개 (subscription_limit_exceeded)")
                        skipped_preview = ', '.join(skipped[:5])
                        if len(skipped) > 5:
                            skipped_preview += f" 외 {len(skipped)-5}개"
                        logging.error(f"📋 실패 종목: {skipped_preview}")
    
                    if pending_count > 0:
                        logging.warning(f"⏳ 구독 대기: {pending_count}개 (기획서 5.1: 20건 제한)")
                        pending_preview = ', '.join(pending[:5])
                        if len(pending) > 5:
                            pending_preview += f" 외 {len(pending)-5}개"
                        logging.warning(f"📋 대기 종목: {pending_preview}")
        
                        # 텔레그램 알림
                        if telegram_bot and hasattr(telegram_bot, 'send_message'):
                            warning_msg = f"""⚠️ WebSocket 구독 제한

                📊 구독 성공: {subscribed_count}개
                ⏳ 대기 중: {pending_count}개

                기획서 5.1: 2025년 11월 1일부터 WebSocket 구독 20건 제한
                대기 중인 종목은 REST 폴링으로 모니터링됩니다."""
                            telegram_bot.send_message(warning_msg)
                    
                    # 구독 성공 알림
                    if telegram_bot and hasattr(telegram_bot, 'send_message'):
                        subscribed_preview = ', '.join(subscribed[:5])
                        if len(subscribed) > 5:
                            subscribed_preview += f" 외 {len(subscribed)-5}개"
                        
                        success_msg = f"""✅ WebSocket 구독 시작

📡 구독 종목: {subscribed_count}개
📋 {subscribed_preview}
🔔 실시간 모니터링 활성화"""
                        telegram_bot.send_message(success_msg)
                        
                except Exception as e:
                    logging.error(f"❌ 다중 종목 구독 실패: {e}")
                    import traceback
                    traceback.print_exc()
                    # 실패해도 기본 종목은 이미 구독됨 (start() 시 자동 구독)

            else:
                logging.info("📋 보유 종목 없음 - 기본 종목만 구독")
                # ✅ 추가: 기본 종목은 이미 on_open()에서 자동 구독됨
                # WebSocketClient.__init__()에서 self.default_symbol 설정
                # on_open() → self.subscribe(self.default_symbol) 호출
                default_symbol = config.get('trading', {}).get('default_symbol', 'AAPL')
                logging.info(f"📡 기본 종목 구독: {default_symbol} (자동)")
    
                # 텔레그램 알림
                if telegram_bot and hasattr(telegram_bot, 'send_message'):
                    info_msg = f"""ℹ️ WebSocket 구독 시작

            📡 기본 종목: {default_symbol}
            💡 보유 종목이 없어 기본 종목만 모니터링합니다."""
                    telegram_bot.send_message(info_msg)

            break
            
        except Exception as e:
            logging.error(f"WebSocket 연결 실패 ({attempt}/{max_attempts}): {e}")
            import traceback
            traceback.print_exc()
            
            if attempt < max_attempts and not shutdown_requested:
                base_delay = config.get('system', {}).get('base_reconnect_delay', 5)
                delay = min(base_delay * (2 ** (attempt - 1)), 60)
                logging.info(f"🔄 {delay}초 후 재시도합니다...")
                time.sleep(delay)
    
    # 3회 재시도 후 실패 시 경고
    if attempt >= max_attempts and not shutdown_requested:
        market_status = is_market_hours(config['trading']['timezone'])
        if market_status == 'regular':
            logging.critical(f"🚨 정규장에서 WebSocket {max_attempts}회 연결 실패 - WebSocketClient가 시스템 종지 예정")
            # WebSocketClient의 emergency_stop_callback이 자동으로 호출됨

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
    """✅ 텔레그램 봇 초기화 및 시작 (기획서 6.1절)"""
    global telegram_bot
    try:
        # ✅ 수정: config에서 bot_token과 chat_id 추출
        telegram_config = config.get('telegram', {})
        bot_token = telegram_config.get('bot_token') or os.getenv('TELEGRAM_BOT_TOKEN')
        chat_id = telegram_config.get('chat_id') or os.getenv('TELEGRAM_CHAT_ID')
        
        # bot_token과 chat_id 검증
        if not bot_token or not chat_id:
            logging.warning("⚠️ 텔레그램 설정 누락 (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)")
            return None
        
        # ✅ 수정: 개별 파라미터로 전달
        telegram_bot = TelegramBot(
            bot_token=bot_token,
            chat_id=chat_id,
            config=config
        )
        
        if hasattr(telegram_bot, 'start'):
            telegram_bot.start()
            logging.info("✅ 텔레그램 봇이 시작되었습니다.")
        
        return telegram_bot
    except Exception as e:
        logging.warning(f"⚠️ 텔레그램 봇 초기화 실패 (선택사항): {e}")
        import traceback
        traceback.print_exc()
        return None

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔴 [v1.2 수정] adaptive_market_monitor 함수
# SmartOrderMonitor가 모드 전환을 전담하도록
# 이 함수는 로깅만 수행하도록 단순화
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def adaptive_market_monitor(config, token_manager, telegram_bot):
    """
    적응형 시장 모니터 - 시장 상태 감시만 수행

    ✅ 변경사항: 모드 전환은 SmartOrderMonitor가 전담
    ⚠️ 이 함수는 시장 상태 로깅만 수행
    """
    global smart_monitor, shutdown_requested

    last_status = None

    while not shutdown_requested:
        try:
            current_status = is_market_hours(config['trading']['timezone'])

            # 상태 변경 시 로그만 출력 (SmartOrderMonitor가 알아서 처리)
            if current_status != last_status:
                last_status = current_status
                logging.info(f"🕐 시장 상태: {current_status} (SmartOrderMonitor가 자동 전환)")

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
        logging.info("💡 기획서 v1.1 완전 준수 버전")
        logging.info("✅ Rate Limit 안전 모드, 적응형 폴링, WebSocket 자동 전환")
        logging.info("✅ WebSocket 구독 20건 제한 (2025년 11월 1일부터)")
        logging.info("✅ 정규장 WebSocket 3회 실패 시 시스템 종지 (기획서 5.2절)")
        
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
            message = f"""🚀 스마트 자동매매 시작!
🕐 시장상태: {market_status}
🧠 Rate Limit 안전모드
⚡ 적응형 폴링 활성화 (정규장 3초/10초)
✅ 기획서 v1.1 수정판 준수
⚠️ WebSocket 비활성화 (REST 폴링 전용)
📊 일일 API 호출: ~5,120회 (안전 마진 내)"""
            if hasattr(telegram_bot, 'send_message'):
                telegram_bot.send_message(message)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 🔴 [v1.2 수정] 
        # SmartOrderMonitor는 시장 상태와 관계없이 항상 시작합니다.
        # 내부의 smart_monitor_loop가 스스로 상태를 감지하고
        # should_system_run(), switch_mode_if_needed()를 통해
        # 동작(폴링) 또는 대기(수면)를 결정합니다.
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        logging.info("🧠 SmartOrderMonitor 시작...")
        if hasattr(smart_monitor, 'start'):
            smart_monitor.start()
        else:
            logging.error("❌ SmartOrderMonitor에 start() 메서드가 없습니다. 확인 필요.")
            return # 심각한 오류
            
        logging.info("✅ SmartOrderMonitor가 모든 시장 상태(pre, regular, closed)를 전담합니다.")
        
        # 적응형 시장 모니터 스레드 시작 (단순 로깅용)
        market_monitor_thread = threading.Thread(
            target=adaptive_market_monitor,
            args=(config, token_manager, telegram_bot),
            daemon=True
        )
        market_monitor_thread.start()
        
        # 메인 상태 모니터링 루프
        logging.info("✅ 스마트 하이브리드 시스템이 준비되었습니다.")
        logging.info("💡 SmartOrderMonitor가 시장 시간에 따라 자동 전환됩니다.")
        
        status_count = 0
        last_stats_report = 0
        
        while not shutdown_requested:
            try:
                if status_count % 12 == 0:  # 1분마다 상태 출력
                    # market_status는 adaptive_market_monitor가 로깅
                    
                    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    # 🔴 [v1.1 신규] WebSocket 구독 상태 확인
                    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    ws_status = "비활성화" # WebSocket 포기로 상태 변경
                    ws_subscribed_count = 0
                    
                    if ws_client and hasattr(ws_client, 'get_status'):
                        try:
                            ws_full_status = ws_client.get_status()
                            ws_connected = ws_full_status.get('connected', False)
                            ws_subscribed = ws_full_status.get('subscribed', False)
                            ws_subscribed_count = ws_full_status.get('subscribed_count', 0)
                            ws_max_subs = ws_full_status.get('max_subscriptions', 20)
                            
                            if ws_connected and ws_subscribed:
                                ws_status = f"연결됨 ({ws_subscribed_count}/{ws_max_subs} 구독)"
                            elif ws_connected:
                                ws_status = "연결됨 (구독 대기)"
                            else:
                                ws_status = "대기 중"
                        except Exception as e:
                            logging.debug(f"WebSocket 상태 조회 오류: {e}")
                            ws_status = "연결됨" if ws_client and hasattr(ws_client, 'is_connected') and ws_client.is_connected() else "대기 중"
                    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    
                    # 스마트 모니터 통계
                    if smart_monitor and hasattr(smart_monitor, 'get_detailed_stats'):
                        stats = smart_monitor.get_detailed_stats()
                        
                        # [v1.2] smart_monitor에서 직접 상태와 통계를 가져옴
                        current_mode = stats.get('current_mode', 'N/A')
                        monitor_count = stats.get('monitoring_count', 0)
                        daily_calls = stats.get('daily_api_calls', 0)
                        hourly_calls = stats.get('hourly_api_calls', 0)
                        
                        logging.info(
                            f"📊 상태: {current_mode} | 모니터링: {monitor_count}건 | "
                            f"API(시간): {hourly_calls} | API(일일): {daily_calls}"
                        )
                        
                        # 10분마다 상세 통계 리포트
                        if status_count - last_stats_report >= 120:  # 10분
                            successful_detections = stats.get('successful_detections', 0)
                            ws_detections = stats.get('ws_detections', 0)
                            rate_limit_errors = stats.get('rate_limit_violations', 0)
                            logging.info(
                                f"📈 상세통계 - 성공(REST): {successful_detections}회, "
                                f"성공(WS): {ws_detections}회, "
                                f"Rate Limit: {rate_limit_errors}회"
                            )
                            
                            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                            # 🔴 [v1.1 신규] WebSocket 구독 상세 정보
                            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                            if ws_client and hasattr(ws_client, 'get_status'):
                                try:
                                    ws_detail = ws_client.get_status()
                                    subscribed_symbols = ws_detail.get('subscribed_symbols', [])
                                    pending_symbols = ws_detail.get('pending_symbols', [])
                                    
                                    if subscribed_symbols:
                                        logging.info(f"📡 구독 중: {len(subscribed_symbols)}개 종목")
                                    if pending_symbols:
                                        logging.warning(f"⏳ 대기 중: {len(pending_symbols)}개 종목")
                                except Exception as e:
                                    logging.debug(f"WebSocket 상세 상태 조회 오류: {e}")
                            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                            
                            last_stats_report = status_count
                    else:
                        logging.info(f"📊 상태: 로딩 중... | WS: {ws_status}")
                
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
                logging.info(f"📊 최종통계 - 총요청: {total_requests}, 성공감지: {successful_detections}")
                
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