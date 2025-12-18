"""
auto_trader.py

완전 자동매매 시스템 - 50MA 터치 전략

작성일: 2025-12-06
버전: 1.0
기획서: v3.0 섹션 6.3
"""

import time
import logging
import json
from datetime import datetime, timedelta
from typing import List, Set, Dict, Any, Optional
from pytz import timezone

logger = logging.getLogger(__name__)


class AutoTrader:
    """
    50MA 터치 전략 기반 완전 자동매매
    
    Attributes:
        watch_list (List[str]): 감시 중인 종목 목록
        touched_but_skipped (Set[str]): 터치했지만 못 산 종목
        permanently_excluded (Set[str]): 손절/익절 완료 종목
        ranking_history (Dict[str, List[int]]): 순위 이력
    """
    
    def __init__(
        self,
        config: dict,
        ranking_updater,
        order_executor,
        order_monitor,
        trade_counter,
        telegram_bot
    ):
        """
        초기화
        
        Args:
            config: 전체 설정
            ranking_updater: RankingUpdater 인스턴스
            order_executor: OrderExecutor 인스턴스
            order_monitor: OrderMonitor 인스턴스
            trade_counter: DailyTradeCounter 인스턴스
            telegram_bot: TelegramBot 인스턴스
        """
        self.config = config
        self.ranking_updater = ranking_updater
        self.order_executor = order_executor
        self.order_monitor = order_monitor
        self.trade_counter = trade_counter
        self.telegram_bot = telegram_bot
        
        # 설정 로드
        auto_config = config['auto_trader']
        self.MAX_WATCH_LIST = auto_config['max_watch_list']
        self.RANK_OUT_THRESHOLD = auto_config['rank_out_threshold']
        self.RANKING_INTERVAL = auto_config['ranking_update_interval']
        self.MONITORING_INTERVAL = auto_config['monitoring_interval']
        self.MA_PERIOD = auto_config['ma_period']
        self.TOUCH_THRESHOLD = auto_config['touch_threshold']
        self.VOLUME_MULTIPLIER = auto_config['volume_multiplier']
        self.STOP_LOSS = auto_config['stop_loss']
        self.TAKE_PROFIT = auto_config['take_profit']
        
        # 상태 변수
        self.watch_list: List[str] = []
        self.touched_but_skipped: Set[str] = set()
        self.permanently_excluded: Set[str] = set()
        self.ranking_history: Dict[str, List[int]] = {}
        
        # 타이밍
        self.last_ranking_update: Optional[datetime] = None
        self.is_running = False
        
        # 상태 파일 경로
        self.state_file = '/tmp/auto_trader_state.json'
        
        logger.info("🤖 AutoTrader 초기화 완료")
    
    def start(self):
        """자동매매 시작"""
        logger.info("🚀 완전 자동매매 시작")
        self.is_running = True
        
        # ✅ 거래일 변경 감지 → touched_but_skipped 초기화
        try:
            with open(self.state_file, 'r') as f:
                saved_state = json.load(f)
                saved_date = saved_state.get('date')
        except:
            saved_date = None
    
        today_date = datetime.now().strftime('%Y-%m-%d')
    
        if saved_date != today_date:
            logger.info(f"📅 새 거래일 시작 ({saved_date} → {today_date})")
            logger.info("🌅 touched_but_skipped 초기화")
            self.touched_but_skipped.clear()
        else:
            logger.info("📂 같은 거래일 - 상태 복구")
            self._load_state_minimal()

        # 2. 초기 랭킹 조회 (감시 목록 새로 구성)
        self.update_ranking()
        
        # 3. 텔레그램 알림
        self.telegram_bot.send_message(
            f"🚀 완전 자동매매 시작\n\n"
            f"📊 감시 종목: {', '.join(self.watch_list) if self.watch_list else '없음'}\n"
            f"📈 전략: 50MA 터치\n"
            f"🛡️ 손절: {self.STOP_LOSS}% / 익절: +{self.TAKE_PROFIT}%\n"
            f"🎯 일일 한도: {self.trade_counter.MAX_ENTRIES}회"
        )
        
        # 4. 감시 루프 시작
        try:
            self.monitor_loop()
        except KeyboardInterrupt:
            logger.info("⌨️ 사용자 중지")
            self.stop()
        except Exception as e:
            logger.error(f"❌ 치명적 오류: {e}")
            import traceback
            traceback.print_exc()
            self.stop()
    
    def stop(self):
        """시스템 중지"""
        logger.info("⏸️ 시스템 중지")
        self.is_running = False
        
        # 상태 저장
        self._save_state()
        
        # 통계
        stats = self.trade_counter.get_stats()
        
        # 텔레그램 알림
        self.telegram_bot.send_message(
            f"⏸️ 시스템 중지\n\n"
            f"📊 일일 통계:\n"
            f"진입: {stats['entry_count']}/{stats['max_entries']}회\n"
            f"청산: {stats['exit_count']}/{stats['max_exits']}회\n\n"
            f"📋 감시 종목: {len(self.watch_list)}개\n"
            f"🚫 제외 종목: {len(self.permanently_excluded)}개"
        )
    
    def update_ranking(self):
        """
        1시간마다 랭킹 업데이트 및 감시 목록 관리
        
        동작 순서:
        1. 상승률 TOP 3 조회
        2. 순위 이력 기록
        3. 신규 종목 추가 (터치 이력 체크)
        4. 2시간 연속 이탈 종목 제거
        5. 최대 8개 초과 시 제거
        6. 텔레그램 알림
        """
        logger.info("📊 랭킹 업데이트 시작")
        
        try:
            # 1. TOP 3 조회
            current_top3 = self.ranking_updater.get_top3_gainers()
            
            if not current_top3:
                logger.warning("⚠️ 조회 결과 없음, 기존 목록 유지")
                return
            
            # 2. 순위 이력 기록
            for item in current_top3:
                ticker = item['ticker']
                if ticker not in self.ranking_history:
                    self.ranking_history[ticker] = []
                self.ranking_history[ticker].append(item['rank'])
            
            # 3. 신규 종목 추가
            for item in current_top3:
                self._add_if_new(item['ticker'])
            
            # 4. 순위 이탈 종목 제거
            logger.info("🔧 [DEBUG] _remove_rank_out_tickers 시작")
            self._remove_rank_out_tickers(current_top3)
            logger.info("🔧 [DEBUG] _remove_rank_out_tickers 완료")

            # 5. 최대 개수 초과 시 제거
            logger.info("🔧 [DEBUG] _limit_watch_list 시작")
            self._limit_watch_list(current_top3)
            logger.info("🔧 [DEBUG] _limit_watch_list 완료")

            # 6. 텔레그램 알림
            logger.info("🔧 [DEBUG] _send_watch_list_update 시작")
            self._send_watch_list_update(current_top3)
            logger.info("🔧 [DEBUG] _send_watch_list_update 완료")

            # 업데이트 시각 기록
            logger.info("🔧 [DEBUG] 시각 기록 시작")
            self.last_ranking_update = datetime.now()
            logger.info("🔧 [DEBUG] 시각 기록 완료")

            # 상태 저장
            logger.info("🔧 [DEBUG] _save_state 시작")
            self._save_state()
            logger.info("🔧 [DEBUG] _save_state 완료")
            
        except Exception as e:
            logger.error(f"❌ 랭킹 업데이트 오류: {e}")
    
    def _add_if_new(self, ticker: str):
        """
        신규 종목 추가 (이력 체크 포함)
        
        Args:
            ticker: 종목코드
        """
        # 이미 제외된 종목
        if ticker in self.permanently_excluded:
            logger.debug(f"{ticker} 이미 영구 제외됨")
            return
        
        if ticker in self.touched_but_skipped:
            logger.debug(f"{ticker} 이미 터치 후 제외됨")
            return
        
        # 이미 감시 중
        if ticker in self.watch_list:
            logger.debug(f"{ticker} 이미 감시 중")
            return
        
        # 최근 1시간 터치 확인
        if self._check_recent_touch(ticker):
            logger.warning(f"⚠️ {ticker} 이미 50선 터치 (최근 1시간)")
            self.touched_but_skipped.add(ticker)
            return
        
        # 추가
        self.watch_list.append(ticker)
        logger.info(f"✅ {ticker} 감시 추가 (총 {len(self.watch_list)}개)")
    
    def _check_recent_touch(self, ticker: str) -> bool:
        """
        최근 1시간 내 50선 터치 여부
        
        Args:
            ticker: 종목코드
        
        Returns:
            bool: 터치했으면 True
        """
        try:
            # 1분봉 60개 조회
            candles = self._get_1min_candles(ticker, 60)
            
            if len(candles) < 50:
                return False
            
            # 50MA 계산
            closes = [c['close'] for c in candles[-50:]]
            ma50 = sum(closes) / 50
            
            # 최근 60개 중 터치 확인
            for candle in candles[-60:]:
                diff_pct = abs(candle['close'] - ma50) / ma50
                if diff_pct < self.TOUCH_THRESHOLD:
                    logger.debug(f"{ticker} 터치 발견: {candle['time']}")
                    return True
            
            return False
        
        except Exception as e:
            logger.error(f"{ticker} 이력 체크 오류: {e}")
            return False
    
    def _remove_rank_out_tickers(self, current_top3: List[Dict]):
        """
        2시간 연속 TOP 3 이탈 종목 제거
        
        Args:
            current_top3: 현재 TOP 3 목록
        """
        top3_tickers = [item['ticker'] for item in current_top3]
        
        for ticker in list(self.watch_list):
            if ticker not in self.ranking_history:
                continue
            
            recent_ranks = self.ranking_history[ticker][-self.RANK_OUT_THRESHOLD:]
            
            # 2시간 연속 TOP 3 밖
            if len(recent_ranks) >= self.RANK_OUT_THRESHOLD:
                if all(r > 3 for r in recent_ranks):
                    self.watch_list.remove(ticker)
                    logger.info(
                        f"❌ {ticker} 순위 이탈 제거 "
                        f"(순위: {recent_ranks})"
                    )
    
    def _limit_watch_list(self, current_top3: List[Dict]):
        """
        최대 8개 초과 시 제거
        
        Args:
            current_top3: 현재 TOP 3 목록
        """
        if len(self.watch_list) <= self.MAX_WATCH_LIST:
            return
        
        # TOP 3에 없는 종목 중 가장 오래된 것 제거
        top3_tickers = [item['ticker'] for item in current_top3]
        candidates = [t for t in self.watch_list if t not in top3_tickers]
        
        if candidates:
            removed = candidates[0]
            self.watch_list.remove(removed)
            logger.info(
                f"❌ {removed} 한도 초과 제거 "
                f"({len(self.watch_list)}/{self.MAX_WATCH_LIST})"
            )
    
    def _send_watch_list_update(self, current_top3: List[Dict]):
        """텔레그램 감시 목록 업데이트 알림"""
        message = "📊 감시 목록 업데이트\n\n"
        
        # 현재 TOP 3
        message += "🏆 상승률 TOP 3:\n"
        for item in current_top3:
            message += f"{item['rank']}. {item['ticker']} +{item['rate']}%\n"
        
        message += f"\n👀 감시 중: {len(self.watch_list)}개\n"
        if self.watch_list:
            message += f"{', '.join(self.watch_list)}\n"
        
        excluded_count = len(self.touched_but_skipped) + len(self.permanently_excluded)
        message += f"\n🚫 제외: {excluded_count}개"
        
        self.telegram_bot.send_message(message)
    
    def monitor_loop(self):
        """
        50MA 감시 루프 (4초마다)
        
        동작:
        1. 1시간마다 랭킹 업데이트 체크
        2. 현재 포지션 확인
        3. 각 감시 종목 50MA 터치 체크
        4. 조건 충족 시 매수 (선입선출)
        """
        logger.info("👁️ 50MA 감시 시작")
        
        while self.is_running:
            try:
                from datetime import datetime
                from pytz import timezone
            
                now_et = datetime.now(timezone('US/Eastern'))
                
                # ✅ 날짜 기반 운영 시간 계산
                today_start = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
                today_end = now_et.replace(hour=12, minute=0, second=0, microsecond=0)
                
                # ET 04:00 ~ 12:00 외에는 슬립 모드
                if not (today_start <= now_et < today_end):
                    if now_et >= today_end:
                        logger.info(f"⏰ ET 12:00 이후 (현재 {now_et.strftime('%H:%M')}), 시스템 중지")
                    else:
                        logger.info(f"⏰ ET 04:00 이전 (현재 {now_et.strftime('%H:%M')}), 슬립 모드")
                    
                    # ⚡ 수정: 슬립 중 타이머 리셋
                    self.last_ranking_update = None

                    # 즉시 루프 탈출 (랭킹 업데이트 하지 않음)
                    time.sleep(60)
                    continue  # 다음 반복으로 바로 이동

                # 1. 랭킹 업데이트 체크
                if self._should_update_ranking():
                    self.update_ranking()
                
                # 2. 포지션 확인
                current_holding = self.order_monitor.monitoring_orders
                has_position = len(current_holding) > 0
                
                # 3. 각 감시 종목 체크
                for ticker in self.watch_list:
                    
                    # 제외 대상 스킵
                    if ticker in self.permanently_excluded:
                        continue
                    if ticker in self.touched_but_skipped:
                        continue
                    
                    # 50MA 터치 체크
                    if self._is_touching_50ma(ticker):
                        
                        if has_position:
                            # 이미 보유 중 → 영구 제외
                            self.touched_but_skipped.add(ticker)
                            logger.info(f"🔒 {ticker} 터치했지만 보유 중")
                            
                            self.telegram_bot.send_message(
                                f"🎯 {ticker} 50선 터치\n"
                                f"하지만 포지션 보유 중으로 매수 불가"
                            )
                        else:
                            # 매수 시도 (선입선출 - 첫 번째만)
                            self._execute_buy(ticker)
                            break  # 1개만 매수
                
                # 4초 대기
                time.sleep(self.MONITORING_INTERVAL)
            
            except Exception as e:
                logger.error(f"❌ 감시 루프 오류: {e}")
                time.sleep(10)
    
    def _should_update_ranking(self) -> bool:
        """랭킹 업데이트 필요 여부"""
        if self.last_ranking_update is None:
            return True
        
        elapsed = (datetime.now() - self.last_ranking_update).total_seconds()
        return elapsed >= self.RANKING_INTERVAL
    
    def _is_touching_50ma(self, ticker: str) -> bool:
        """
        50MA 터치 조건 체크 (5가지)
        
        Args:
            ticker: 종목코드
        
        Returns:
            bool: 5가지 조건 모두 충족 시 True
        """
        try:
            # 1. 1분봉 조회 (51개 = 50MA + 현재)
            candles = self._get_1min_candles(ticker, 51)
            
            if len(candles) < 51:
                return False
            
            # 2. 50MA 계산 (최근 50개)
            closes = [c['close'] for c in candles[-51:-1]]
            ma50 = sum(closes) / 50
            
            # 3. 현재가 조회
            current_price = self._get_current_price(ticker)
            
            if not current_price:
                return False
            
            # 조건 1: 이전 캔들 > 50MA × 1.005
            prev_candle = candles[-2]
            if prev_candle['close'] <= ma50 * 1.005:
                return False
            
            # 조건 2: 현재 캔들 50MA ± 0.5% 이내
            current_candle = candles[-1]
            diff_pct = abs(current_candle['close'] - ma50) / ma50
            if diff_pct > self.TOUCH_THRESHOLD:
                return False
            
            # 조건 3: 현재가 > 현재 캔들 (반등) - ⚠️ 비활성화
            # 터치 시 즉시 매수, 하락 시 -2% 손절로 관리
            # if current_price <= current_candle['close']:
            #     return False
            
            # 조건 4: 거래량 > 평균 × 1.2
            recent_volumes = [c['volume'] for c in candles[-20:]]
            avg_volume = sum(recent_volumes) / len(recent_volumes)
            if current_candle['volume'] < avg_volume * self.VOLUME_MULTIPLIER:
                return False
            
            # ⚡ 조건 4-2: 절대 거래량 필터 (ATMCU 차단)
            MIN_ABSOLUTE_VOLUME = 50
            
            if current_candle['volume'] < MIN_ABSOLUTE_VOLUME:
                logger.warning(
                    f"⚠️ {ticker} 절대 거래량 부족: "
                    f"{current_candle['volume']}주 < {MIN_ABSOLUTE_VOLUME}주 "
                    f"→ 영구 제외"
                )
                self.permanently_excluded.add(ticker)
                self.touched_but_skipped.add(ticker)
                return False
            
            # ⚡ 조건 4-3: 시간당 평균 거래량 필터
            if len(candles) >= 60:
                hourly_volumes = [c['volume'] for c in candles[-60:]]
                avg_hourly_volume = sum(hourly_volumes) / 60
                MIN_HOURLY_VOLUME = 10
                
                if avg_hourly_volume < MIN_HOURLY_VOLUME:
                    logger.warning(
                        f"⚠️ {ticker} 시간당 거래량 부족: "
                        f"{avg_hourly_volume:.1f}주/분 < {MIN_HOURLY_VOLUME}주/분 "
                        f"→ 영구 제외"
                    )
                    self.permanently_excluded.add(ticker)
                    return False
                            
            # 조건 5: RSI < 70 (선택 - 생략)
            # rsi = self._calculate_rsi(candles)
            # if rsi > 70:
            #     return False
            
            logger.info(
                f"✅ {ticker} 50MA 터치 조건 충족\n"
                f"  이전: ${prev_candle['close']:.2f}\n"
                f"  현재: ${current_candle['close']:.2f}\n"
                f"  50MA: ${ma50:.2f}\n"
                f"  실시간: ${current_price:.2f}"
            )
            
            return True
        
        except Exception as e:
            logger.error(f"{ticker} 50MA 체크 오류: {e}")
            return False
    
    def _execute_buy(self, ticker: str):
        """
        100% 전액 매수
        
        Args:
            ticker: 종목코드
        """
        logger.info(f"💰 {ticker} 매수 시작")
        
        try:
            # 1. 일일 진입 한도 확인
            if not self.trade_counter.can_enter():
                logger.critical("🚫 일일 진입 한도 도달")
                
                self.telegram_bot.send_message(
                    f"🚫 일일 진입 한도 도달\n\n"
                    f"진입: {self.trade_counter.entry_count}회\n"
                    f"시스템을 중지합니다."
                )
                
                self.stop()
                return
            
            # 2. 전액 매수
            result = self.order_executor.place_fullsize_buy(ticker)
            
            if result['success']:
                # 진입 카운트 증가
                self.trade_counter.increment_entry()
                
                order_info = {
                    'ticker': ticker,
                    'order_no': result['order_no'],
                    'quantity': result['quantity'],
                    'buy_price': result['price'],
                    'source': 'v3_auto',  # v3.0 자동매매 표시
                    'created_at': datetime.now().isoformat()
                }
    
                if hasattr(self, 'order_monitor') and self.order_monitor:
                    self.order_monitor.register_order(
                        result['order_no'], 
                        order_info
                    )
                    logger.info(f"✅ {ticker} OrderMonitor 등록 완료 (손절/익절 감시 시작)")
    
                logger.info(
                    f"✅ {ticker} 매수 성공\n"
                    f"  수량: {result['quantity']}주\n"
                    f"  가격: ${result['price']:.2f}\n"
                    f"  진입: {self.trade_counter.entry_count}/{self.trade_counter.MAX_ENTRIES}"
                )
                
                # 상태 저장
                self._save_state()
            else:
                logger.error(f"❌ {ticker} 매수 실패: {result.get('reason')}")
        
        except Exception as e:
            logger.error(f"{ticker} 매수 실행 오류: {e}")
    
    def on_exit_complete(self, ticker: str, reason: str):
        """
        청산 완료 시 콜백 (OrderExecutor에서 호출)
        
        Args:
            ticker: 종목코드
            reason: 'stop_loss' 또는 'take_profit'
        """
        logger.info(f"🏁 {ticker} {reason} 완료")
        
        # 1. 영구 제외
        self.permanently_excluded.add(ticker)
        
        # 2. 감시 목록에서 제거
        if ticker in self.watch_list:
            self.watch_list.remove(ticker)
        
        # 3. 청산 카운트 증가
        self.trade_counter.increment_exit()
        
        stats = self.trade_counter.get_stats()
        
        logger.info(
            f"📊 일일 통계:\n"
            f"  진입: {stats['entry_count']}회\n"
            f"  청산: {stats['exit_count']}회"
        )
        
        # 4. 일일 한도 체크
        if stats['entry_count'] >= self.trade_counter.MAX_ENTRIES:
            logger.critical("🚫 일일 진입 한도 도달, 시스템 중지")
            self.stop()
        
        # 5. 상태 저장
        self._save_state()
    
    def _get_1min_candles(self, ticker: str, count: int) -> List[Dict]:
        """
        1분봉 조회
        
        Args:
            ticker: 종목코드
            count: 조회 개수
        
        Returns:
            list: [{'time': '093000', 'close': 250.50, 'volume': 125000}, ...]
        """
        # order_executor의 메서드 사용
        return self.order_executor.get_1min_candles(ticker, count)
    
    def _get_current_price(self, ticker: str) -> Optional[float]:
        """
        실시간 현재가 조회
        
        Args:
            ticker: 종목코드
        
        Returns:
            float: 현재가 (실패 시 None)
        """
        return self.order_executor.get_current_price(ticker)
    
    def _save_state(self):
        """상태 파일 저장"""
        try:
            state = {
                'date': datetime.now().strftime('%Y-%m-%d'),
                'watch_list': self.watch_list,
                'touched_but_skipped': list(self.touched_but_skipped),
                'permanently_excluded': list(self.permanently_excluded),
                'ranking_history': self.ranking_history,
                'entry_count': self.trade_counter.entry_count,
                'exit_count': self.trade_counter.exit_count,
                'last_ranking_update': self.last_ranking_update.isoformat() if self.last_ranking_update else None
            }
            
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
            
            logger.debug("💾 상태 저장 완료")
        
        except Exception as e:
            logger.error(f"❌ 상태 저장 오류: {e}")
    
    def _load_state(self):
        """상태 파일 로드"""
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
            
            # 날짜 확인 (당일만 복구)
            state_date = state.get('date')
            today = datetime.now().strftime('%Y-%m-%d')
            
            if state_date != today:
                logger.info("📅 새로운 거래일, 상태 초기화")
                return
            
            # 상태 복구
            self.watch_list = state.get('watch_list', [])
            self.touched_but_skipped = set(state.get('touched_but_skipped', []))
            self.permanently_excluded = set(state.get('permanently_excluded', []))
            self.ranking_history = state.get('ranking_history', {})
            
            # 마지막 업데이트 시각 복구
            last_update_str = state.get('last_ranking_update')
            if last_update_str:
                self.last_ranking_update = datetime.fromisoformat(last_update_str)
            
            logger.info(
                f"✅ 상태 복구 완료\n"
                f"  감시: {len(self.watch_list)}개\n"
                f"  제외: {len(self.touched_but_skipped) + len(self.permanently_excluded)}개"
            )
        
        except FileNotFoundError:
            logger.info("📝 상태 파일 없음, 새로 시작")
        
        except Exception as e:
            logger.error(f"❌ 상태 로드 오류: {e}")

    def _load_state_minimal(self):
        """
        최소 상태 복구 (재진입 제외 정보만)
        
        - permanently_excluded: 손절/익절 완료 종목 (재진입 금지)
        - touched_but_skipped: 터치했지만 못 산 종목 (재진입 금지)
        
        감시 목록(watch_list)은 복구하지 않음 → 항상 최신 TOP 3으로 시작
        """
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
            
            # 날짜 확인 (당일만 복구)
            state_date = state.get('date')
            today = datetime.now().strftime('%Y-%m-%d')
            
            if state_date != today:
                logger.info("📅 새로운 거래일, 상태 초기화")
                return
            
            # 재진입 제외 정보만 복구
            self.touched_but_skipped = set(state.get('touched_but_skipped', []))
            self.permanently_excluded = set(state.get('permanently_excluded', []))
            
            excluded_count = len(self.touched_but_skipped) + len(self.permanently_excluded)
            
            if excluded_count > 0:
                logger.info(
                    f"✅ 재진입 제외 정보 복구 완료\n"
                    f"  터치 후 제외: {len(self.touched_but_skipped)}개\n"
                    f"  손절/익절 완료: {len(self.permanently_excluded)}개\n"
                    f"  📋 감시 목록은 최신 TOP 3으로 새로 구성합니다"
                )
            else:
                logger.info("📝 제외 정보 없음, 완전히 새로 시작")
        
        except FileNotFoundError:
            logger.info("📝 상태 파일 없음, 새로 시작")
        
        except Exception as e:
            logger.error(f"❌ 상태 로드 오류: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 코드
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == '__main__':
    print("⚠️ auto_trader.py는 main.py를 통해 실행됩니다.")
    print("테스트: python3 main.py")