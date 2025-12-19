# telegram_bot.py - v2.0 기획서 준수 (대화형 명령어 추가) + Phase 2 수정 (수동 감시)

import requests
import logging
import json
import time
import os
import signal
import threading
import re  # ✨ [Phase 2] 정규표현식 모듈 (티커 감지용)
from datetime import datetime, timedelta
from collections import defaultdict

class TelegramBot:
    """
    텔레그램 봇 (기획서 v2.0 대화형 명령어 포함)
    
    주요 기능:
    - v1.1의 모든 알림 기능
    - [v2.0 신규] /buy : 대화형 목표가 매수 주문
    - [v2.0 신규] /orders : 대기 중인 텔레그램 주문 조회
    - [v2.0 신규] /cancel : 대기 중인 텔레그램 주문 취소
    - [Phase 2] /watch 또는 티커 입력 : 수동 감시 종목 추가
    """
    
    # (수정 1) __init__ 에 order_manager 와 conversation_states 추가
    def __init__(self, bot_token, chat_id, config=None, order_manager=None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.logger = logging.getLogger(__name__)
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.is_running = False
        self.polling_thread = None
        
        # (신규) 텔레그램 주문 관리자 (Phase 1, 2에서 구현)
        self.order_manager = order_manager

        # ✨ [Phase 2] AutoTrader 참조 (수동 티커 추가용)
        self.auto_trader = None
        
        # (신규) 대화형 명령어 상태 관리
        # 예: { 1234567: {'type': 'buy', 'step': 'awaiting_ticker', 'data': {}} }
        self.conversation_states = {}

        # 설정
        if config and 'telegram' in config:
            self.polling_interval = config['telegram'].get('polling_interval', 10)
            self.error_polling_interval = config['telegram'].get('error_polling_interval', 30)
            self.timeout = config['telegram'].get('timeout', 10)
        else:
            # 기본값 - AWS 비용 절약
            self.polling_interval = 10
            self.error_polling_interval = 30
            self.timeout = 10
        
        # ✅ 추가: 알림 중복 방지 (기획서 6.1절)
        self.last_alert_time = {}  # 알림 타입별 마지막 전송 시간
        self.alert_cooldown = 300  # 5분 내 같은 알림 중복 방지

        # ✅ 추가: 슬립 모드 중복 방지 플래그
        self.sleep_mode_notified = False  # 슬립 모드 알림 이미 발송 여부
        
        # ✅ 추가: 통계 추적
        self.stats = {
            'total_buys': 0,
            'total_sells': 0,
            'successful_sells': 0,
            'failed_sells': 0,
            'total_profit': 0.0,
            'errors': 0,
            'start_time': datetime.now()
        }

    def _get_kst_time(self):
        """
        ✅ [수정] KST 시간 반환 헬퍼 메서드
        
        모든 알림 메시지에서 정확한 한국 시간을 표시하기 위해 사용
        """
        from pytz import timezone
        return datetime.now(timezone('Asia/Seoul'))

    def _should_send_alert(self, alert_type):
        """
        ✅ 추가: 알림 중복 방지 체크 (기획서 6.1절)
        
        같은 타입의 알림을 5분 이내에 다시 보내지 않음
        """
        current_time = time.time()
        last_time = self.last_alert_time.get(alert_type, 0)
        
        if current_time - last_time < self.alert_cooldown:
            self.logger.debug(f"알림 중복 방지: {alert_type} (마지막 전송: {current_time - last_time:.0f}초 전)")
            return False
        
        self.last_alert_time[alert_type] = current_time
        return True

    # (수정 2) send_message의 chat_id를 동적으로 설정 가능하도록 변경
    def send_message(self, message, parse_mode='HTML', force=False, alert_type=None, chat_id=None):
        """
        메시지 전송 (알림 중복 방지 포함)
        
        Parameters:
            message: 전송할 메시지
            parse_mode: HTML 또는 Markdown
            force: True시 중복 방지 무시
            alert_type: 알림 타입 (중복 방지용)
            chat_id: (신규) 지정된 chat_id로 전송, None이면 self.chat_id 사용
        """
        
        # (신규) chat_id가 지정되지 않으면 기본 chat_id 사용
        target_chat_id = chat_id if chat_id else self.chat_id
        
        if not target_chat_id:
            self.logger.error("메시지 전송 실패: chat_id가 설정되지 않았습니다.")
            return False
            
        try:
            # 알림 중복 방지 체크 (기본 chat_id에 대해서만)
            if not force and alert_type and target_chat_id == self.chat_id:
                if not self._should_send_alert(alert_type):
                    return False
            
            url = f"{self.base_url}/sendMessage"
            data = {
                "chat_id": target_chat_id,
                "text": message,
                "parse_mode": parse_mode
            }

            response = requests.post(url, data=data, timeout=self.timeout)
            if response.status_code == 200:
                self.logger.debug(f"Telegram 메시지 전송 성공 (ChatID: {target_chat_id})")
                return True
            else:
                self.logger.error(f"Telegram 메시지 전송 실패: {response.text}")
                return False
        except Exception as e:
            self.logger.error(f"Telegram 메시지 전송 중 오류: {e}")
            return False

    def send_startup_notification(self):
        """✅ [수정 1/15] 시스템 시작 알림 (기획서 6.1절)"""
        now_kst = self._get_kst_time()
        
        message = f"""
🚀 <b>자동 매도 시스템 시작</b> (v2.0)

• 상태: ✅ 실행중
• 시작 시간: {now_kst.strftime('%Y-%m-%d %H:%M:%S')}
• 운영 시간: ET 05:00 - 12:00
• 수익률 목표: +6.0% (v2.0)
• 폴링 간격: 4초 (프리마켓)

📋 v2.0 기획서 준수
✅ **신규**: /buy 텔레그램 주문
✅ 일일 8회 매매 제한 (통합)
✅ 주말 자동 슬립
❌ WebSocket 미사용 (v2.0)

시스템이 정상적으로 작동하고 있습니다.
"""
        return self.send_message(message.strip(), force=True)

    def send_buy_detection_notification(self, ticker, quantity, price, source='auto'):
        """
        ✅ [수정 2/15] 매수 감지 알림 (v2.0 - source 구분)
        
        Parameters:
            source: 'auto' (자동감지) / 'telegram' (텔레그램 주문)
        """
        self.stats['total_buys'] += 1
        now_kst = self._get_kst_time()
        
        if source == 'telegram':
            source_emoji = "🤖"
            source_text = "텔레그램 주문 (목표가 도달)"
        else: # auto
            source_emoji = "🔍"
            source_text = "KIS 앱 (자동 감지)"
        
        message = f"""
{source_emoji} <b>매수 체결 감지</b> ({source_text})

• 종목: <b>{ticker}</b>
• 수량: {quantity:,}주
• 매수가: ${price:.2f}
• 시각: {now_kst.strftime('%H:%M:%S')}

🎯 목표 수익률 +6.0% 도달 시 자동 매도 예정
💰 목표가: ${price * 1.06:.2f}
"""
        # 텔레그램 주문은 중복 방지 없이, 자동 감지만 중복 방지
        alert_type = f"buy_{ticker}" if source == 'auto' else None
        force_send = True if source == 'telegram' else False
        
        return self.send_message(message.strip(), alert_type=alert_type, force=force_send)

    def send_sell_order_notification(self, ticker, quantity, buy_price, sell_price, profit_rate, source='auto'):
        """✅ [수정 3/15] 매도 주문 알림 (v2.0 - source 구분)"""
        self.stats['total_sells'] += 1
        self.stats['successful_sells'] += 1
        profit_amount = (sell_price - buy_price) * quantity
        self.stats['total_profit'] += profit_amount
        now_kst = self._get_kst_time()
        
        source_text = " (TG 주문)" if source == 'telegram' else ""
        
        message = f"""
📈 <b>자동 매도 주문 실행{source_text}</b>

• 종목: <b>{ticker}</b>
• 수량: {quantity:,}주
• 매수가: ${buy_price:.2f}
• 매도가: ${sell_price:.2f}
• 수익률: <b>+{profit_rate:.1f}%</b>
• 수익금: <b>${profit_amount:.2f}</b>
• 시각: {now_kst.strftime('%H:%M:%S')}

✅ 매도 주문이 성공적으로 실행되었습니다.
"""
        return self.send_message(message.strip(), force=True)

    def send_sell_failure_notification(self, ticker, quantity, reason):
        """✅ [수정 4/15] 매도 실패 알림 (기획서 6.1절)"""
        self.stats['failed_sells'] += 1
        now_kst = self._get_kst_time()
        
        message = f"""
⚠️ <b>매도 주문 실패</b>

• 종목: <b>{ticker}</b>
• 수량: {quantity:,}주
• 실패 사유: {reason}
• 시각: {now_kst.strftime('%H:%M:%S')}

⚠️ 수동 확인이 필요합니다.
"""
        return self.send_message(message.strip(), alert_type=f"sell_fail_{ticker}")

    def send_rate_limit_warning(self, current_usage, limit, utilization_pct):
        """
        ✅ [수정 5/15] Rate Limit 경고 알림 (기획서 5.2절, 6.1절)
        
        90% 도달 시 경고
        """
        if utilization_pct < 90:
            return False
        
        now_kst = self._get_kst_time()
        
        message = f"""
⚠️ <b>Rate Limit 경고</b>

• 현재 사용량: {current_usage:,}회
• 일일 한도: {limit:,}회
• 사용률: <b>{utilization_pct:.1f}%</b>
• 시각: {now_kst.strftime('%H:%M:%S')}

<b>기획서 5.2절: Rate Limit 90% 도달</b>

⚠️ API 호출을 자제하고 있습니다.
남은 한도를 효율적으로 사용합니다.
"""
        return self.send_message(message.strip(), alert_type="rate_limit_warning")

    def send_consecutive_errors_alert(self, error_count, error_type):
        """
        ✅ [수정 6/15] 연속 오류 알림 (기획서 5.2절, 6.1절)
        
        연속 10회 API 오류 시 알림
        """
        if error_count < 10:
            return False
        
        self.stats['errors'] += 1
        now_kst = self._get_kst_time()
        
        message = f"""
🚨 <b>연속 API 오류 감지</b>

• 오류 타입: {error_type}
• 연속 횟수: {error_count}회
• 시각: {now_kst.strftime('%H:%M:%S')}

<b>기획서 5.2절: 연속 10회 API 오류</b>

⚠️ 시스템 상태를 점검하고 있습니다.
"""
        return self.send_message(message.strip(), alert_type=f"consecutive_errors_{error_type}")

    def send_websocket_failure_alert(self, attempt, max_attempts, is_regular_market):
        """
        ✅ [수정 7-8/15] WebSocket 실패 알림 (기획서 5.2절, 6.1절)
        
        정규장에서 3회 실패 시 시스템 종지 경고
        (v2.0에서는 거의 사용되지 않음)
        """
        now_kst = self._get_kst_time()
        
        if is_regular_market and attempt >= max_attempts:
            message = f"""
🚨 <b>긴급: WebSocket 연결 실패</b>

• 시도 횟수: {attempt}/{max_attempts}
• 시장 상태: 정규장 (ET 09:30-12:00)
• 시각: {now_kst.strftime('%H:%M:%S')}

<b>기획서 5.2절: WebSocket 3회 실패</b>

🛑 정규장에서 WebSocket 연결 불가
⚠️ 시스템이 곧 자동 종지됩니다
"""
            return self.send_message(message.strip(), force=True)

        else:
            message = f"""
⚠️ <b>WebSocket 재연결 시도</b>

• 시도: {attempt}/{max_attempts}
• 시각: {now_kst.strftime('%Y-%m-%d %H:%M:%S')}

연결을 복구하려고 시도하고 있습니다.
"""
            return self.send_message(message.strip(), alert_type="websocket_reconnect")

    # --- 신규 함수 추가 ---
    def send_websocket_subscription_limit_alert(self, subscribed_count, pending_count, total_count):
        """
        ✅ [수정 9/15] WebSocket 구독 제한 알림 (기획서 v1.1, 5.1절)
        (v2.0에서는 거의 사용되지 않음)
        """
        # 대기 중인 종목이 없으면 알림하지 않음
        if pending_count == 0:
            return False
        
        now_kst = self._get_kst_time()
        
        message = f"""
⚠️ <b>WebSocket 구독 제한</b>

📊 구독 현황:
• 구독 성공: {subscribed_count}개
• 구독 대기: {pending_count}개
• 전체 보유: {total_count}개

📋 기획서 v1.1 (5.1절):
2025년 11월 1일부터 WebSocket 구독 20건 제한

✅ 대응 방안:
• 우선순위 높은 {subscribed_count}개 실시간 감시
• 나머지 {pending_count}개는 REST 폴링 (선택적)

ℹ️ 시스템은 정상 작동하고 있습니다.
시간: {now_kst.strftime('%H:%M:%S')}
"""
        return self.send_message(message.strip(), alert_type="ws_subscription_limit")
    # --- 신규 함수 추가 완료 ---

    def send_account_query_failure_alert(self, error_message):
        """✅ [수정 10/15] 계좌 정보 조회 실패 알림 (기획서 5.2절, 6.1절)"""
        now_kst = self._get_kst_time()
        
        message = f"""
⚠️ <b>계좌 정보 조회 실패</b>

• 오류 내용: {error_message}
• 시각: {now_kst.strftime('%H:%M:%S')}

<b>기획서 5.2절: 계좌 정보 조회 실패</b>

⚠️ 실시간 잔고 정보를 확인할 수 없습니다.
수동으로 계좌를 확인해주세요.
"""
        return self.send_message(message.strip(), alert_type="account_query_fail")

    def send_error_notification(self, error_message, level="warning"):
        """
        ✅ [수정 11/15] 오류 알림 (레벨 추가)
        
        Parameters:
            error_message: 오류 메시지
            level: info/warning/critical
        """
        emoji_map = {
            'info': 'ℹ️',
            'warning': '⚠️',
            'critical': '🚨'
        }
        
        emoji = emoji_map.get(level, '⚠️')
        now_kst = self._get_kst_time()
        
        message = f"""
{emoji} <b>시스템 {level.upper()}</b>

오류 내용: {error_message}
발생 시각: {now_kst.strftime('%Y-%m-%d %H:%M:%S')}

시스템 상태를 확인해 주세요.
"""
        return self.send_message(message.strip(), alert_type=f"error_{level}")

    def send_info_notification(self, message):
            """
            ℹ️ 정보성 알림 전송
        
            Parameters:
                message: 전송할 메시지
            """
            return self.send_error_notification(message, level="info")

    def send_daily_summary(self, trade_stats=None):
        """
        ✅ 수정: 일일 통계 요약 (v2.0 - trade_stats 연동)
        
        trade_stats: DailyTradeCounter에서 전달받은 통계 딕셔너리
        """
        runtime = datetime.now() - self.stats['start_time']
        runtime_hours = runtime.total_seconds() / 3600
        
        # (신규) v2.0 통계
        auto_trades = 0
        telegram_trades = 0
        total_trades = 0
        
        if trade_stats:
            auto_trades = trade_stats.get('auto', 0)
            telegram_trades = trade_stats.get('telegram', 0)
            total_trades = trade_stats.get('total', 0)

        # 기존 통계 (매도 시도 횟수)
        total_sells = self.stats['total_sells']
        successful_sells = self.stats['successful_sells']
        failed_sells = self.stats['failed_sells']
        
        success_rate = 0
        if total_sells > 0:
            success_rate = (successful_sells / total_sells) * 100
        
        message = f"""
📊 <b>일일 통계 요약</b> (v2.0)

<b>📈 거래 현황 (v1.x 기준)</b>
• 매수 감지: {self.stats['total_buys']}건
• 매도 시도: {total_sells}건
• 매도 성공: {successful_sells}건
• 매도 실패: {failed_sells}건
• 성공률: {success_rate:.1f}%

<b>💰 수익 현황</b>
• 총 수익: ${self.stats['total_profit']:.2f}

<b>⚙️ 시스템 (v2.0)</b>
• 총 매매 (8회 한도): {total_trades}회
    - 자동 감지: {auto_trades}회
    - 텔레그램: {telegram_trades}회
• 가동 시간: {runtime_hours:.1f}시간
• 오류 횟수: {self.stats['errors']}회

<b>기획서 v2.0 준수 시스템</b>
✅ 일일 통계 요약 (기획서 6.1절)
"""
        return self.send_message(message.strip(), force=True)

    def send_sleep_mode_notification(self, reason="normal", trade_stats=None):
        """
        ✅ [수정 12/15] 슬립모드 진입 알림 (v2.0)
        
        Parameters:
            reason: "normal", "trade_limit", "weekend"
            trade_stats: DailyTradeCounter 통계
        """
        from pytz import timezone
       
        if reason == "normal" and self.sleep_mode_notified:
            # 이미 슬립 모드 알림을 보냈으면 무시
            self.logger.debug("⏭️ 슬립 모드 알림 이미 발송됨 (중복 방지)")
            return None

        if reason == "trade_limit":
            emoji = "🚫"
            title = "매매 한도 도달 - 슬립 모드"
            reason_text = f"오늘 설정된 매매 횟수({trade_stats.get('total', 0)}/8회)에 도달했습니다."
        elif reason == "weekend":
            emoji = "😴"
            title = "주말 슬립 모드 진입"
            reason_text = "미국장 주말(토/일)입니다."
        else: # normal
            emoji = "😴"
            title = "슬립 모드 진입"
            now_et = datetime.now(timezone('US/Eastern'))
            reason_text = f"정규장 종료 시각 (ET {now_et.strftime('%H:%M')})에 도달했습니다."
        
        next_start = "월요일 ET 05:00" if reason == "weekend" else "오늘 17:00 (ET 05:00)"
        
        now_kst = self._get_kst_time()
        message = f"""
{emoji} <b>{title}</b>

• 종료 시각: {now_kst.strftime('%Y-%m-%d %H:%M:%S')}
• 사유: {reason_text}
• 다음 시작: {next_start}

시스템이 슬립 모드로 전환됩니다.
"""
        result = self.send_message(message.strip(), force=True)

        # ⚡ [수정] 슬립 모드 알림 발송 플래그 설정
        if reason == "normal":
            self.sleep_mode_notified = True
    
        return result
    
    def reset_sleep_mode_flag(self):
        """
        ✅ [신규] 슬립 모드 플래그 리셋
        
        매일 ET 00:00에 DailyTradeCounter에서 호출됨
        """
        if self.sleep_mode_notified:
            self.logger.info("🔄 슬립 모드 플래그 리셋 (새로운 거래일)")
            self.sleep_mode_notified = False

    def send_shutdown_notification(self, trade_stats=None):
        """✅ [수정 13/15] 시스템 종료 알림 (v2.0 - 통계 연동)"""
        # 종료 전 일일 통계 전송
        self.send_daily_summary(trade_stats)
        
        time.sleep(1)  # 통계 메시지 전송 후 1초 대기
        
        now_kst = self._get_kst_time()
        
        message = f"""
🛑 <b>자동 매도 시스템 종료</b>

• 종료 시각: {now_kst.strftime('%Y-%m-%d %H:%M:%S')}
• 상태: 정상 종료

시스템이 안전하게 종료되었습니다.
"""
        return self.send_message(message.strip(), force=True)

    def send_emergency_stop_notification(self, reason):
        """
        ✅ [수정 14/15] 긴급 종지 알림 (기획서 5.2절, 6.1절)
        
        비상 정지 시 전송
        """
        now_kst = self._get_kst_time()
        
        message = f"""
🚨 <b>긴급 시스템 종지</b>

• 종지 사유: <b>{reason}</b>
• 시각: {now_kst.strftime('%H:%M:%S')}

<b>기획서 5.2절: 비상 증지 조건 충족</b>

⚠️ 시스템이 안전하게 종료됩니다.
수동 점검이 필요합니다.
"""
        return self.send_message(message.strip(), force=True)

    def get_updates(self, offset=None):
        """업데이트 가져오기"""
        try:
            url = f"{self.base_url}/getUpdates"
            params = {}
            if offset:
                params['offset'] = offset

            response = requests.get(url, params=params, timeout=self.timeout)
            if response.status_code == 200:
                data = response.json()
                if data['ok']:
                    return data['result']
            return []
        except Exception as e:
            self.logger.error(f"업데이트 가져오기 오류: {e}")
            return []

    def clear_message_queue(self, start_time):
        """
        메시지 큐 정리 - 시작 시각 이전 메시지만 무시
        
        Args:
            start_time: 시스템 시작 시각 (timestamp)
        """
        try:
            updates = self.get_updates()
            if not updates:
                return True
            
            old_messages = []
            latest_offset = None
            
            for update in updates:
                message = update.get('message', {})
                message_date = message.get('date', 0)
                
                # 시작 시각 이전 메시지는 무시
                if message_date < start_time:
                    old_messages.append(update['update_id'])
                    latest_offset = update['update_id']
            
            # 오래된 메시지만 읽음 처리
            if latest_offset:
                self.get_updates(offset=latest_offset + 1)
                self.logger.info(f"텔레그램 오래된 메시지 정리: {len(old_messages)}개 무시됨")
            else:
                self.logger.info("텔레그램 큐에 오래된 메시지 없음")
            
            return True
        except Exception as e:
            self.logger.error(f"메시지 큐 정리 오류: {e}")
            return False

    # ✨ [Phase 2 신규] /watch 명령어 처리 핸들러 (v1.1)
    def handle_watch_command(self, message_text: str, chat_id: int):
        """
        수동 감시 추가 처리
        사용법: '/watch TSLA' 또는 'TSLA'
        """
        # 1. 티커 추출
        text = message_text.strip()
        if text.startswith('/watch'):
            text = text[6:].strip()
        
        # 정규식으로 대문자 티커 추출
        tickers = re.findall(r'[A-Z]{1,5}', text.upper())
        
        if not tickers:
            self.send_message(
                "❌ 티커를 입력하세요\n예: <code>/watch TSLA</code> 또는 <code>TSLA</code>", 
                chat_id=chat_id
            )
            return
        
        # 2. AutoTrader 연결 확인
        if not self.auto_trader:
            self.send_message("❌ AutoTrader가 연결되지 않았습니다.", chat_id=chat_id)
            return

        # 3. 각 티커 추가 요청
        results = []
        for ticker in tickers:
            # AutoTrader의 메서드 호출
            result = self.auto_trader.add_manual_ticker(ticker)
            results.append(result)

        # 4. 결과 메시지 생성
        response = ""
        for res in results:
            response += f"{res['message']}\n\n"
        
        # 5. 현재 감시 현황 요약
        watch_list = self.auto_trader.watch_list
        manual_cnt = len(self.auto_trader.manual_watch_list)
        auto_cnt = len(watch_list) - manual_cnt
        
        response += f"📊 <b>현재 감시 목록</b> ({len(watch_list)}/{self.auto_trader.MAX_WATCH_LIST}개)\n"
        response += f"• 🤖 자동 감지: {auto_cnt}개\n"
        response += f"• 👤 수동 추가: {manual_cnt}개"
        
        self.send_message(response.strip(), chat_id=chat_id)

    # 
    # ↓↓↓ (수정 3) handle_command: v2.0 신규 명령어 추가 ↓↓↓
    #
    def handle_command(self, command, chat_id, message_date):
        """명령어 처리 - v2.0 명령어 추가"""
        try:
            # 메시지가 5분 이상 된 것은 무시 (시스템 시작 전 메시지)
            current_time = datetime.now().timestamp()
            if current_time - message_date > 300:  # 5분 = 300초
                self.logger.info(f"오래된 명령어 무시: {command} (나이: {current_time - message_date:.0f}초)")
                return

            # (신규) /buy, /orders, /cancel 은 응답 메시지를 동적으로 생성하므로
            # 이 메서드 상단에서 처리하고 즉시 반환합니다.
            
            # --- [v2.0] /buy 명령어 (대화 시작) ---
            if command == '/buy':
                if not self.order_manager:
                    self.send_message("오류: 텔레그램 주문 관리자가 연결되지 않았습니다.", chat_id=chat_id)
                    return

                if chat_id in self.conversation_states:
                    self.send_message("이미 다른 대화가 진행 중입니다. '취소'를 입력하여 종료 후 다시 시도하세요.", chat_id=chat_id)
                    return
                
                # 대화 상태 시작
                initial_state = {
                    'type': 'buy',
                    'step': 'awaiting_ticker',
                    'data': {}
                }
                self.conversation_states[chat_id] = initial_state
                
                # OrderManager에게 첫 번째 질문을 받아옴
                response_message = self.order_manager.handle_buy_conversation(chat_id, None, initial_state)
                self.send_message(response_message, chat_id=chat_id, parse_mode='HTML')
                return # 이 메서드 종료

            # --- [v2.0] /cancel 명령어 (대화 시작) ---
            if command == '/cancel':
                if not self.order_manager:
                    self.send_message("오류: 텔레그램 주문 관리자가 연결되지 않았습니다.", chat_id=chat_id)
                    return

                if chat_id in self.conversation_states:
                    self.send_message("이미 다른 대화가 진행 중입니다. '취소'를 입력하여 종료 후 다시 시도하세요.", chat_id=chat_id)
                    return
                
                # 대화 상태 시작
                initial_state = {
                    'type': 'cancel',
                    'step': 'awaiting_cancel_number',
                    'data': {}
                }
                self.conversation_states[chat_id] = initial_state
                
                # OrderManager에게 취소 목록을 받아옴
                response_message = self.order_manager.handle_cancel_conversation(chat_id, None, initial_state)
                
                # 만약 취소할 주문이 없으면 OrderManager가 "취소할 주문 없음" 메시지 반환
                # 이 경우 대화 상태를 즉시 종료해야 함
                if initial_state.get('step') == 'done':
                    del self.conversation_states[chat_id]

                self.send_message(response_message, chat_id=chat_id, parse_mode='HTML')
                return # 이 메서드 종료

            # --- [v2.0] /orders 명령어 (즉시 응답) ---
            if command == '/orders':
                if not self.order_manager:
                    self.send_message("오류: 텔레그램 주문 관리자가 연결되지 않았습니다.", chat_id=chat_id)
                    return
                
                response_message = self.order_manager.get_pending_orders_message()
                self.send_message(response_message, chat_id=chat_id, parse_mode='HTML')
                return # 이 메서드 종료

            # --- 기존 명령어들 ---
            
            message = None # 보낼 메시지
            
            if command == '/start':
                message = """
🤖 <b>자동 매도 시스템 봇 (v2.0)</b>

v2.0 기획서에 따라 업그레이드되었습니다.

<b>사용 가능한 명령어:</b>
• /buy - 목표가 매수 주문 시작
• /orders - 대기 중인 텔레그램 주문
• /cancel - 텔레그램 주문 취소
• /watch - 수동 감시 종목 추가
• /status - 시스템 상태 확인
• /stats - 실시간 통계
• /stop - 시스템 종료
• /help - 도움말 보기
"""
            elif command == '/status':
                runtime = datetime.now() - self.stats['start_time']
                runtime_hours = runtime.total_seconds() / 3600
                now_kst = self._get_kst_time()
                
                # (신규) OrderManager에서 통계 가져오기 시도
                tg_order_count = 0
                if self.order_manager:
                    tg_order_count = self.order_manager.get_pending_order_count()

                message = f"""
📊 <b>시스템 상태 (v2.0)</b>

• 상태: ✅ 실행중
• 가동 시간: {runtime_hours:.1f}시간
• 확인 시각: {now_kst.strftime('%Y-%m-%d %H:%M:%S')}
• 폴링 간격: {self.polling_interval}초
• 텔레그램 주문 대기: {tg_order_count}건
"""
            elif command == '/stats':
                success_rate = 0
                if self.stats['total_sells'] > 0:
                    success_rate = (self.stats['successful_sells'] / self.stats['total_sells']) * 100
                
                # (신규) v2.0 통계
                trade_stats = {'auto': 0, 'telegram': 0, 'total': 0, 'remaining': 8}
                if self.order_manager and hasattr(self.order_manager, 'trade_counter'):
                    trade_stats = self.order_manager.trade_counter.get_stats()

                message = f"""
📈 <b>실시간 통계 (v2.0)</b>

<b>매매 (일일 8회 한도)</b>
• 총 매매: {trade_stats['total']} / 8 회
• 남은 횟수: {trade_stats['remaining']} 회
• 자동 감지: {trade_stats['auto']} 회
• 텔레그램: {trade_stats['telegram']} 회

<b>수익</b>
• 총 수익: ${self.stats['total_profit']:.2f}

<b>시스템</b>
• 매수 감지: {self.stats['total_buys']}건
• 매도 성공: {self.stats['successful_sells']}건
• 성공률: {success_rate:.1f}%
• 오류: {self.stats['errors']}회
"""
            elif command == '/stop':
                message = """
🛑 <b>시스템 종료 요청</b>

시스템 종료를 시작합니다...
잠시 후 시스템이 안전하게 종료됩니다.
"""
                # 메시지 전송 후 시스템 종료
                self.send_message(message.strip(), chat_id=chat_id)
                # 안전한 시스템 종료
                self.logger.info("텔레그램에서 시스템 종료 요청을 받았습니다.")
                os.kill(os.getpid(), signal.SIGTERM)
                return

            elif command == '/help':
                message = f"""
📚 도움말 (v2.0)

명령어 목록:
• /buy - 대화형 목표가 매수 주문
• /orders - 대기 중인 주문 목록
• /cancel - 대기 중인 주문 취소
• /watch - 수동 감시 종목 추가
• /status - 현재 시스템 상태 확인
• /stats - 실시간 거래 통계
• /stop - 시스템 안전 종료
• /help - 이 도움말 보기

기능:
• KIS 앱 수동 매수 감시
• 텔레그램 목표가 매수
• 자동 +6.0% 매도 주문 실행
• 일일 8회 통합 매매 제한
• 주말 자동 슬립

📋 기획서 v2.0 준수:
• 수익률 목표: +6.0%
• 운영 시간: ET 05:00 - 12:00
• 폴링 주기: 4초 (균일)
• WebSocket 미사용
"""
            else:
                message = f"""
❓ <b>알 수 없는 명령어: {command}</b>

/help 명령어로 사용 가능한 명령어를 확인하세요.
"""

            # 응답 전송 (신규 명령어 외)
            if message:
                requests.post(f"{self.base_url}/sendMessage", data={
                    "chat_id": chat_id,
                    "text": message.strip(),
                    "parse_mode": "HTML"
                }, timeout=self.timeout)

        except Exception as e:
            self.logger.error(f"명령어 처리 오류: {e}")

    # 
    # ↑↑↑ (수정 3) handle_command 수정 완료 ↑↑↑
    #

    # 
    # ↓↓↓ (신규 4) handle_conversation: 대화형 명령어 처리기 ↓↓↓
    #
    def handle_conversation(self, chat_id, text, message_date):
        """
        /buy, /cancel 등 대화형 명령어의 후속 응답 처리
        """
        try:
            state = self.conversation_states.get(chat_id)
            if not state:
                return # 상태가 없으면 무시

            if not self.order_manager:
                self.send_message("오류: 텔레그램 주문 관리자가 연결되지 않았습니다.", chat_id=chat_id)
                del self.conversation_states[chat_id]
                return

            # '취소' 입력 시 대화 종료
            if text.lower() == '취소':
                del self.conversation_states[chat_id]
                self.send_message("대화가 취소되었습니다.", chat_id=chat_id)
                return

            # 대화 유형에 따라 담당 핸들러 호출
            conv_type = state.get('type')
            
            if conv_type == 'buy':
                response_message = self.order_manager.handle_buy_conversation(chat_id, text, state)
            elif conv_type == 'cancel':
                response_message = self.order_manager.handle_cancel_conversation(chat_id, text, state)
            else:
                response_message = "알 수 없는 대화 상태입니다. 대화를 종료합니다."
                del self.conversation_states[chat_id]

            # 응답 메시지 전송
            self.send_message(response_message, chat_id=chat_id, parse_mode='HTML')

            # 대화가 완료되었는지 확인 (OrderManager가 state를 변경)
            if state.get('step') == 'done' or state.get('step') == 'cancelled':
                del self.conversation_states[chat_id]

        except Exception as e:
            self.logger.error(f"대화 처리 중 오류: {e}")
            self.send_message("대화 처리 중 오류가 발생했습니다. 처음부터 다시 시도해주세요.", chat_id=chat_id)
            if chat_id in self.conversation_states:
                del self.conversation_states[chat_id]
    # 
    # ↑↑↑ (신규 4) handle_conversation 추가 완료 ↑↑↑
    #

    # 
    # ↓↓↓ (수정 5) start_polling: 대화 상태 우선 처리 ↓↓↓
    #
    def start_polling(self):
        """텔레그램 봇 폴링 시작 (대화 상태 관리 추가)"""
        self.logger.info(f"텔레그램 봇 폴링을 시작합니다... (간격: {self.polling_interval}초)")
        
        # 시작 시각을 먼저 기록
        start_time = datetime.now().timestamp()
        
        # 시작 시각을 전달하여 메시지 큐 정리
        self.clear_message_queue(start_time)
        self.is_running = True
        
        offset = None
        consecutive_errors = 0
        max_consecutive_errors = 5

        while self.is_running:
            try:
                updates = self.get_updates(offset)
                
                # 연속 오류 카운터 리셋
                consecutive_errors = 0

                for update in updates:
                    try:
                        if 'message' in update:
                            message = update['message']
                            chat_id = message['chat']['id']
                            message_date = message['date']

                            # 권한 확인
                            if str(chat_id) != str(self.chat_id):
                                self.logger.warning(f"권한이 없는 사용자의 메시지 무시: {chat_id}")
                                continue

                            # 시스템 시작 이전 메시지는 무시
                            if message_date < start_time:
                                self.logger.debug(f"시스템 시작 이전 메시지 무시: {message.get('text', '')}")
                                continue

                            if 'text' in message:
                                text = message['text'].strip()
                                self.logger.info(f"텔레그램 메시지 수신 (from {chat_id}): {text}")

                                # 1순위: 대화 상태 확인
                                if chat_id in self.conversation_states:
                                    self.handle_conversation(chat_id, text, message_date)
                                
                                # 2순위: 명령어 처리
                                elif text.startswith('/'):
                                    # ✨ [Phase 2] /watch 명령어는 인자 파싱이 필요하므로 여기서 직접 처리
                                    if text.startswith('/watch'):
                                        self.handle_watch_command(text, chat_id)
                                    else:
                                        # 명령어만 추출 (예: /start param -> /start)
                                        command = text.split()[0]
                                        self.handle_command(command, chat_id, message_date)
                                
                                # ✨ [Phase 2] 3순위: 단순 티커 입력 감지 (예: "TSLA")
                                elif re.match(r'^[A-Z]{1,5}$', text.upper()):
                                    self.handle_watch_command(text, chat_id)
                                
                                # 4순위: 그 외 (무시)
                                else:
                                    self.logger.debug(f"명령어 또는 대화가 아닌 메시지 무시: {text}")

                        offset = update['update_id'] + 1

                    except Exception as e:
                        self.logger.error(f"업데이트 처리 오류: {e}")
                        continue

                # 폴링 간격
                if self.is_running:
                    time.sleep(self.polling_interval)

            except Exception as e:
                consecutive_errors += 1
                self.logger.error(f"폴링 오류 ({consecutive_errors}/{max_consecutive_errors}): {e}")
                
                # 연속 오류가 너무 많으면 더 긴 대기
                if consecutive_errors >= max_consecutive_errors:
                    self.logger.warning("연속 오류 한계 도달. 긴 대기 시간 적용.")
                    wait_time = self.error_polling_interval * 2
                else:
                    wait_time = self.error_polling_interval

                if self.is_running:
                    time.sleep(wait_time)

        self.logger.info("텔레그램 봇 폴링이 종료되었습니다.")
    # 
    # ↑↑↑ (수정 5) start_polling 수정 완료 ↑↑↑
    #

    # (수정 6) start: order_manager 주입
    def start(self, order_manager=None):
        """봇 시작 (order_manager 주입)"""
        if self.is_running:
            self.logger.warning("텔레그램 봇이 이미 실행 중입니다.")
            return
            
        self.logger.info("텔레그램 봇을 시작합니다...")
        
        # (신규) order_manager 연결
        if order_manager:
            self.order_manager = order_manager
            self.logger.info("TelegramOrderManager가 성공적으로 연결되었습니다.")
        else:
            self.logger.warning("TelegramOrderManager가 연결되지 않았습니다. /buy 등 v2.0 명령어 사용 불가.")
            
        self.polling_thread = threading.Thread(target=self.start_polling, daemon=True)
        self.polling_thread.start()
        
        # 시작 알림 전송
        self.send_startup_notification()

    def stop(self, trade_stats=None):
        """봇 중지 (v2.0 - 통계 연동)"""
        if not self.is_running:
            return
            
        self.logger.info("텔레그램 봇을 중지합니다...")
        self.is_running = False
        
        # 종료 알림 전송 (일일 통계 포함)
        self.send_shutdown_notification(trade_stats)
        
        # 폴링 스레드 종료 대기
        if self.polling_thread and self.polling_thread.is_alive():
            self.polling_thread.join(timeout=5)

    def stop_polling(self):
        """폴링 중지 (호환성을 위해 유지)"""
        self.logger.info("텔레그램 봇 폴링을 중지합니다...")
        self.is_running = False