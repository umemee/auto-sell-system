"""
auto_trader.py

완전 자동매매 시스템 - 50MA 터치 전략 (Aggressive Mode)

작성일: 2025-12-06
수정일: 2025-12-20 (Aggressive Mode 적용)
버전: 1.2
기획서: v3.0 섹션 6.3 + 명령어추가기획서 v1.1
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
    50MA 터치 전략 기반 완전 자동매매 (공격적 모드)
    
    Attributes:
        watch_list (List[str]): 감시 중인 종목 목록
        touched_but_skipped (Set[str]): 터치했지만 못 산 종목
        permanently_excluded (Set[str]): 손절/익절 완료 종목
        ranking_history (Dict[str, List[int]]): 순위 이력
        manual_watch_list (Set[str]): 수동으로 추가한 감시 종목
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
        self.TAKE_PROFIT_TIER1 = auto_config.get('take_profit_tier1', 3)
        self.TAKE_PROFIT_TIER2 = auto_config.get('take_profit_tier2', 6)
        self.TRAILING_STOP = auto_config.get('trailing_stop_distance', 2)

        # 추적 손절용 변수 추가
        self.position_peak_profit: Dict[str, float] = {}  # 종목별 최고 수익률
        self.position_partial_sold: Dict[str, bool] = {}  # 종목별 분할 익절 여부
        
        # 상태 변수
        self.watch_list: List[str] = []
        self.touched_but_skipped: Set[str] = set()
        self.permanently_excluded: Set[str] = set()
        self.ranking_history: Dict[str, List[int]] = {}
        
        # ✨ [v1.1 신규] 수동 추가 종목 추적
        self.manual_watch_list: Set[str] = set()
        
        # 타이밍
        self.last_ranking_update: Optional[datetime] = None
        self.is_running = False
        
        # 상태 파일 경로
        self.state_file = '/tmp/auto_trader_state.json'
        
        logger.info("🤖 AutoTrader (Aggressive Mode) 초기화 완료")
    
    def start(self):
        """자동매매 시작"""
        logger.info("🚀 완전 자동매매 시작 (Aggressive)")
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
        
        # 3. 텔레그램 알림 (공격적 모드 명시)
        self.telegram_bot.send_message(
            f"🚀 자동매매 시작 (Aggressive)\n"
            f"전략: 50MA 하향이탈(-4%) 허용\n\n"
            f"📊 감시 종목: {', '.join(self.watch_list) if self.watch_list else '없음'}\n"
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
                ticker = item['ticker']
                
                # ✨ [v1.1 수정] 수동 추가 종목은 업데이트 로직 스킵 (이미 감시 중이므로)
                if ticker in self.manual_watch_list:
                    continue
                    
                self._add_if_new(ticker)
            
            # 4. 순위 이탈 종목 제거
            self._remove_rank_out_tickers(current_top3)

            # 5. 최대 개수 초과 시 제거
            self._limit_watch_list(current_top3)

            # 6. 텔레그램 알림
            self._send_watch_list_update(current_top3)

            # 업데이트 시각 기록
            self.last_ranking_update = datetime.now()

            # 상태 저장
            self._save_state()
            logger.info("✅ 랭킹 업데이트 및 저장 완료")
            
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
            # ✨ [v1.1 수정] 수동 추가 종목은 순위 이탈해도 제거하지 않음
            if ticker in self.manual_watch_list:
                logger.debug(f"🔵 {ticker} 수동 추가 종목, 제거 안 함")
                continue

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
            # 수동 추가 종목은 제거 대상에서 제외
            candidates = [t for t in candidates if t not in self.manual_watch_list]

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
        logger.info("👁️ 50MA 감시 시작 (Aggressive)")
        
        # ✅ 추가됨: 루프 횟수 카운터 (생존 신고용)
        loop_count = 0
        
        while self.is_running:
            try:
                # ✅ 추가됨: 루프 시작 시점 시간 측정 (성능 감시용)
                loop_start_time = time.time()
                loop_count += 1
                
                from datetime import datetime
                from pytz import timezone
            
                now_et = datetime.now(timezone('US/Eastern'))
                
                # 날짜 기반 운영 시간 계산
                today_start = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
                today_end = now_et.replace(hour=12, minute=0, second=0, microsecond=0)
                
                # ET 04:00 ~ 12:00 외에는 슬립 모드
                if not (today_start <= now_et < today_end):
                    if now_et >= today_end:
                        # ✅ 수정됨: 로그 레벨을 info -> debug로 변경 (너무 시끄러움 방지)
                        if loop_count % 30 == 0: # 2분마다 한 번씩만 출력
                            logger.debug(f"💤 시스템 종료 대기 중 (ET 12:00 이후, 현재 {now_et.strftime('%H:%M')})")
                    else:
                        if loop_count % 30 == 0:
                            logger.debug(f"💤 개장 대기 중 (ET 04:00 이전, 현재 {now_et.strftime('%H:%M')})")
                    
                    self.last_ranking_update = None
                    time.sleep(60)
                    continue

                # ✅ 추가됨: 100회(약 6~7분)마다 생존 신고 및 상태 요약 로그 출력
                if loop_count % 100 == 0:
                    watch_str = ", ".join(self.watch_list) if self.watch_list else "없음"
                    logger.info(f"👁️ [감시 중] 루프 #{loop_count} | 감시 목록({len(self.watch_list)}): {watch_str}")

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
                    
                    # 50MA 터치 체크 (공격적 로직)
                    if self._is_touching_50ma(ticker):
                        
                        if has_position:
                            # 이미 보유 중 → 영구 제외
                            self.touched_but_skipped.add(ticker)
                            logger.info(f"🔒 {ticker} 터치했지만 보유 중 (추가 매수 금지)") # ✅ 메시지 명확화
                            
                            self.telegram_bot.send_message(
                                f"🎯 {ticker} 50선 터치 감지!\n"
                                f"✋ 하지만 현재 포지션 보유 중이라 매수하지 않습니다.\n"
                                f"🚫 해당 종목은 금일 재진입 금지 목록에 추가됩니다."
                            )
                        else:
                            # 매수 시도 (선입선출 - 첫 번째만)
                            logger.info(f"⚡ {ticker} 매수 조건 충족! 매수 시도합니다.") # ✅ 매수 의도 로그 추가
                            self._execute_buy(ticker)
                            break  # 1개만 매수
                
                # 4초 대기 (실제 처리 시간 고려하여 보정 가능하지만 여기선 단순 sleep)
                time.sleep(self.MONITORING_INTERVAL)
            
            except Exception as e:
                # ✅ 수정됨: 에러 발생 시 더 자세한 정보 출력
                logger.error(f"❌ 감시 루프 내부 오류 발생: {str(e)}")
                import traceback
                logger.error(traceback.format_exc()) # 스택 트레이스 출력
                time.sleep(10)
    
    def _should_update_ranking(self) -> bool:
        """랭킹 업데이트 필요 여부"""
        if self.last_ranking_update is None:
            return True
        
        elapsed = (datetime.now() - self.last_ranking_update).total_seconds()
        return elapsed >= self.RANKING_INTERVAL
    
    def _is_touching_50ma(self, ticker: str) -> bool:
        """
        🔥 50MA 공격적 터치 전략 (Aggressive Mode)
        
        조건:
        1. 추세: 50MA가 상승 중이어야 함 (5분 전 50MA와 비교)
        2. 가격 Zone: 50MA 기준 -4% ~ +2% (하락 돌파 허용)
        3. 거래량: 평소 거래량의 50% 이상 (조건 완화)
        
        Args:
            ticker: 종목코드
        
        Returns:
            bool: 조건 충족 시 True
        """
        try:
            # 1. 1분봉 조회 (60개 = 50MA 계산 + 5분 전 비교용)
            candles = self._get_1min_candles(ticker, 60)
            
            if len(candles) < 56:
                return False
            
            # 2. 현재 50MA 계산 (최근 50개)
            closes = [c['close'] for c in candles[-51:-1]]
            ma50 = sum(closes) / 50
            
            # 3. [유지] 추세 필터 (5분 전 50MA와 비교)
            # 5분 전 시점의 50MA 계산 (인덱스 -56 ~ -6)
            past_closes = [c['close'] for c in candles[-56:-6]]
            if past_closes:
                past_ma50 = sum(past_closes) / 50
                if ma50 < past_ma50:
                    # 추세 하락이면 패스
                    return False
            
            # 4. 현재가 조회 (캔들 데이터 사용)
            current_candle = candles[-1]
            price = current_candle['close']
            
            # 5. [수정] 공격적 가격 범위 설정 (Zone)
            # 하단: 50MA - 4% (0.96)
            # 상단: 50MA + 2% (1.02)
            lower_limit = ma50 * 0.96
            upper_limit = ma50 * 1.02
            
            if not (lower_limit <= price <= upper_limit):
                return False
            
            # 6. [수정] 거래량 조건 완화
            # 최근 20개 평균 거래량 계산
            recent_volumes = [c['volume'] for c in candles[-21:-1]]
            avg_volume = sum(recent_volumes) / len(recent_volumes) if recent_volumes else 0
            
            # 평소 거래량의 50%만 되면 OK
            if current_candle['volume'] < avg_volume * 0.5:
                return False
            
            # ⚡ [유지] 절대 거래량 필터 (ATMCU 등 극소형주 차단용 최소 안전장치)
            MIN_ABSOLUTE_VOLUME = 50
            if current_candle['volume'] < MIN_ABSOLUTE_VOLUME:
                logger.warning(f"⚠️ {ticker} 절대 거래량 부족 ({current_candle['volume']}) -> 영구 제외")
                self.permanently_excluded.add(ticker)
                return False
            
            logger.info(
                f"✅ {ticker} 공격적 매수 조건 충족!\n"
                f"  현재가: ${price:.2f}\n"
                f"  50MA: ${ma50:.2f} (상승 추세)\n"
                f"  거래량: {current_candle['volume']} (평균의 {(current_candle['volume']/avg_volume)*100:.0f}%)"
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
                    'source': 'v3_agg',  # 공격적 모드 표시
                    'created_at': datetime.now().isoformat()
                }
    
                # ✨ v3.0: OrderExecutor에 등록 (3단계 출구 전략 사용)
                if hasattr(self, 'order_executor') and self.order_executor:
                    self.order_executor.register_order(
                        result['order_no'],
                        order_info
                    )
                    logger.info(f"✅ {ticker} OrderExecutor 등록 완료 (3단계 출구 전략 시작)")

                # v1.x/v2.0: SmartOrderMonitor에도 등록 (백업 감시)
                if hasattr(self, 'order_monitor') and self.order_monitor:
                    self.order_monitor.add_order_to_monitor(
                        order_no=result['order_no'],
                        ticker=ticker,
                        quantity=result['quantity'],
                        buy_price=result['price'],
                        source='v3_agg'
                    )
                    logger.info(f"✅ {ticker} SmartOrderMonitor 등록 완료 (백업 감시)")
                
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
    
    # ✨ [v1.1 신규] 수동 티커 추가 메서드
    def add_manual_ticker(self, ticker: str) -> Dict[str, Any]:
        """수동 티커 추가 (텔레그램 명령어용)"""
        ticker = ticker.upper().strip()
        logger.info(f"👤 수동 티커 추가 요청: {ticker}")
        
        # 1. 입력 검증
        if not ticker or len(ticker) > 5:
            return {'success': False, 'message': '❌ 잘못된 티커 형식 (1-5글자)'}
        
        # 2. 중복 체크
        if ticker in self.watch_list:
            source = '수동' if ticker in self.manual_watch_list else '자동'
            return {'success': False, 'message': f'❌ 이미 감시 중입니다 ({source})'}
        
        # 3. MAX_WATCH_LIST 체크
        if len(self.watch_list) >= self.MAX_WATCH_LIST:
            return {'success': False, 'message': f'❌ 감시 목록 가득 참 ({self.MAX_WATCH_LIST}개)'}
        
        # 4. 제외 목록에서 강제 제거 (재진입 허용)
        if ticker in self.touched_but_skipped:
            self.touched_but_skipped.discard(ticker)
        if ticker in self.permanently_excluded:
            self.permanently_excluded.discard(ticker)
        
        # 5. 추가
        self.watch_list.append(ticker)
        self.manual_watch_list.add(ticker)
        
        logger.info(f"✅ {ticker} 수동 추가 완료")
        self._save_state()
        
        return {
            'success': True, 
            'message': f'✅ {ticker} 감시 시작\n전략: 50MA 터치\n목표: +{self.TAKE_PROFIT}%'
        }

    def remove_manual_ticker(self, ticker: str) -> Dict[str, Any]:
        """수동 티커 제거"""
        ticker = ticker.upper().strip()
        
        if ticker not in self.manual_watch_list:
            return {'success': False, 'message': '❌ 수동 추가된 종목이 아닙니다'}
            
        if ticker in self.watch_list:
            self.watch_list.remove(ticker)
            
        self.manual_watch_list.discard(ticker)
        self._save_state()
        
        return {'success': True, 'message': f'✅ {ticker} 감시 중지'}

    def _save_state(self):
        """상태 파일 저장"""
        try:
            state = {
                'date': datetime.now().strftime('%Y-%m-%d'),
                'watch_list': self.watch_list,
                # ✨ [v1.1 신규] 수동 추가 종목 저장
                'manual_watch_list': list(self.manual_watch_list),
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
            self.manual_watch_list = set(state.get('manual_watch_list', []))
            
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
            
            # ✨ [v1.1 신규] 수동 추가 종목 복구
            self.manual_watch_list = set(state.get('manual_watch_list', []))
            
            excluded_count = len(self.touched_but_skipped) + len(self.permanently_excluded)
            manual_count = len(self.manual_watch_list)
            
            if manual_count > 0 or excluded_count > 0:
                logger.info(
                    f"✅ 상태 복구 완료\n"
                    f"  👤 수동 추가: {manual_count}개\n"
                    f"  🔴 터치 후 제외: {len(self.touched_but_skipped)}개\n"
                    f"  🚫 손절/익절 완료: {len(self.permanently_excluded)}개\n"
                    f"  📋 감시 목록은 최신 TOP 3으로 새로 구성합니다"
                )
            else:
                logger.info("📝 제외 정보 없음, 완전히 새로 시작")
        
        except FileNotFoundError:
            logger.info("📝 상태 파일 없음, 새로 시작")
        
        except Exception as e:
            logger.error(f"❌ 상태 로드 오류: {e}")
    def _check_exit_conditions(self, ticker: str, current_price: float, buy_price: float) -> tuple[bool, str]:
        """
        3단계 출구 전략 체크
    
        Returns:
            (매도 여부, 매도 사유)
        """
        profit_rate = ((current_price - buy_price) / buy_price) * 100
    
        # 티어별 최고 수익률 추적
        if ticker not in self.position_peak_profit:
            self.position_peak_profit[ticker] = profit_rate
        else:
            self.position_peak_profit[ticker] = max(self.position_peak_profit[ticker], profit_rate)
    
        # Tier 1: 초기 손절 (-3%)
        if profit_rate <= self.STOP_LOSS:
            return (True, f"초기 손절 ({profit_rate:.2f}%)")
    
        # Tier 2: 1차 익절 (3% 도달 시 50% 매도)
        if profit_rate >= self.TAKE_PROFIT_TIER1 and not self.position_partial_sold.get(ticker, False):
            self.position_partial_sold[ticker] = True
            return (True, f"1차 익절 50% ({profit_rate:.2f}%)")
    
        # Tier 3: 추적 손절 (1차 익절 후 활성화)
        if self.position_partial_sold.get(ticker, False):
            if profit_rate < self.position_peak_profit[ticker] - self.TRAILING_STOP:
                return (True, f"추적 손절 (최고 {self.position_peak_profit[ticker]:.1f}% → 현재 {profit_rate:.1f}%)")
    
        # Tier 4: 최종 익절 (6%)
        if profit_rate >= self.TAKE_PROFIT_TIER2:
            return (True, f"최종 익절 ({profit_rate:.2f}%)")
    
        return (False, "")
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 코드
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == '__main__':
    print("⚠️ auto_trader.py는 main.py를 통해 실행됩니다.")
    print("테스트: python3 main.py")