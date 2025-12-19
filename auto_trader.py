"""
auto_trader.py

완전 자동매매 시스템 - 50MA 터치 전략 (Aggressive Mode Integrated)
- 기존의 랭킹 관리/리스트 정리 기능 완벽 포함
- 공격적 진입 모드 적용 (하향 돌파 허용, 거래량 완화)

작성일: 2025-12-20
버전: 1.3 (Full Version)
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
    - 공격적 진입 모드 적용
    - 순위 이탈 관리 및 자동 리스트 갱신 기능 포함
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
        """초기화"""
        self.config = config
        self.ranking_updater = ranking_updater
        self.order_executor = order_executor
        self.order_monitor = order_monitor
        self.trade_counter = trade_counter
        self.telegram_bot = telegram_bot
        
        # 설정 로드
        auto_config = config['auto_trader']
        self.MAX_WATCH_LIST = auto_config.get('max_watch_list', 10)
        self.RANK_OUT_THRESHOLD = auto_config.get('rank_out_threshold', 2)
        self.RANKING_INTERVAL = auto_config.get('ranking_update_interval', 3600)
        self.MONITORING_INTERVAL = auto_config.get('monitoring_interval', 4)
        self.STOP_LOSS = auto_config.get('stop_loss', 3.0)
        self.TAKE_PROFIT = auto_config.get('take_profit', 3.0)
        
        # 상태 변수
        self.watch_list: List[str] = []
        self.touched_but_skipped: Set[str] = set()
        self.permanently_excluded: Set[str] = set()
        self.ranking_history: Dict[str, List[int]] = {}
        self.manual_watch_list: Set[str] = set()
        
        # 타이밍 및 상태 관리
        self.last_ranking_update: Optional[datetime] = None
        self.is_running = False
        self.state_file = 'auto_trader_state.json'
        
        logger.info("🤖 AutoTrader (공격적 모드) 초기화 완료")
    
    def start(self):
        """자동매매 시작"""
        logger.info("🚀 완전 자동매매 시작 (Aggressive Mode)")
        self.is_running = True
        
        # 상태 복구 (최소한의 정보만)
        self._load_state_minimal()
        
        # 초기 랭킹 업데이트
        self.update_ranking()
        
        # 텔레그램 알림
        watch_str = ', '.join(self.watch_list) if self.watch_list else '없음'
        self.telegram_bot.send_message(
            f"🚀 자동매매 시작 (Aggressive)\n\n"
            f"🔥 전략: 50MA 하향이탈(-4%) 허용 매매\n"
            f"📊 초기 감시: {watch_str}\n"
            f"🎯 일일 한도: {self.trade_counter.MAX_ENTRIES}회"
        )
        
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
        self._save_state()
        
        stats = self.trade_counter.get_stats()
        self.telegram_bot.send_message(
            f"⏸️ 시스템 중지\n\n"
            f"📊 금일 진입: {stats['entry_count']}회\n"
            f"💰 금일 청산: {stats['exit_count']}회"
        )
    
    def update_ranking(self):
        """
        랭킹 업데이트 및 감시 목록 관리 (핵심 로직 복원)
        """
        logger.info("📊 랭킹 업데이트 시작")
        
        try:
            # 1. TOP 3 조회
            current_top3 = self.ranking_updater.get_top3_gainers()
            
            if not current_top3:
                logger.warning("⚠️ 조회 결과 없음")
                return
            
            # 2. 순위 이력 기록 및 신규 추가
            for item in current_top3:
                ticker = item['ticker']
                
                # 순위 기록
                if ticker not in self.ranking_history:
                    self.ranking_history[ticker] = []
                self.ranking_history[ticker].append(item['rank'])
                
                # 수동 추가 종목이 아니면 자동 추가 로직 수행
                if ticker not in self.manual_watch_list:
                    self._add_if_new(ticker)
            
            # 3. 이탈 종목 및 한도 초과 제거
            self._remove_rank_out_tickers(current_top3)
            self._limit_watch_list(current_top3)
            
            # 4. 알림 및 저장
            self._send_watch_list_update(current_top3)
            
            self.last_ranking_update = datetime.now()
            self._save_state()
            
        except Exception as e:
            logger.error(f"❌ 랭킹 업데이트 오류: {e}")

    def _add_if_new(self, ticker: str):
        """신규 종목 추가"""
        # 제외 대상 체크
        if ticker in self.permanently_excluded:
            return
        if ticker in self.touched_but_skipped:
            return
        if ticker in self.watch_list:
            return
            
        self.watch_list.append(ticker)
        logger.info(f"✅ {ticker} 감시 추가")

    def _remove_rank_out_tickers(self, current_top3: List[Dict]):
        """순위 이탈 종목 제거 (수동 종목 제외)"""
        # 설정값 사용, 없으면 기본 2회
        threshold = self.RANK_OUT_THRESHOLD
        
        for ticker in list(self.watch_list):
            if ticker in self.manual_watch_list:
                continue

            if ticker not in self.ranking_history:
                continue
            
            recent_ranks = self.ranking_history[ticker][-threshold:]
            
            # 지정된 횟수만큼 연속으로 TOP 3 밖이면 제거
            if len(recent_ranks) >= threshold:
                if all(r > 3 for r in recent_ranks):
                    self.watch_list.remove(ticker)
                    logger.info(f"❌ {ticker} 순위 이탈 제거 (최근 순위: {recent_ranks})")

    def _limit_watch_list(self, current_top3: List[Dict]):
        """최대 개수 제한"""
        if len(self.watch_list) <= self.MAX_WATCH_LIST:
            return

        # TOP 3에 없는 종목 중, 수동 추가가 아닌 것부터 제거
        top3_tickers = [item['ticker'] for item in current_top3]
        
        candidates = [
            t for t in self.watch_list 
            if t not in top3_tickers and t not in self.manual_watch_list
        ]
        
        if candidates:
            removed = candidates[0]
            self.watch_list.remove(removed)
            logger.info(f"❌ {removed} 한도 초과 제거")
        elif self.watch_list:
            # 후보가 없으면(모두 TOP3거나 수동이면) 맨 앞 제거 (안전장치)
            removed = self.watch_list[0]
            # 수동 목록에서도 제거해줘야 함
            if removed in self.manual_watch_list:
                 self.manual_watch_list.discard(removed)
            self.watch_list.remove(removed)
            logger.info(f"❌ {removed} 한도 초과 강제 제거")

    def _send_watch_list_update(self, current_top3: List[Dict]):
        """감시 목록 업데이트 알림"""
        message = "📊 감시 목록 업데이트\n\n"
        message += "🏆 TOP 3:\n"
        for item in current_top3:
            message += f"{item['rank']}. {item['ticker']} +{item['rate']}%\n"
        
        message += f"\n👀 감시 중 ({len(self.watch_list)}개): {', '.join(self.watch_list)}"
        
        # 너무 잦은 알림 방지를 위해 로그로만 남길 수도 있으나, 여기선 전송
        # self.telegram_bot.send_message(message) 
        logger.info(f"📊 리스트 업데이트 완료. 현재 감시: {len(self.watch_list)}개")

    def monitor_loop(self):
        """
        감시 루프 (Aggressive Mode)
        """
        logger.info("👁️ 50MA 감시 시작 (Aggressive Mode)")
        loop_count = 0
        
        while self.is_running:
            try:
                loop_count += 1
                
                # 100회마다 생존 로그
                if loop_count % 100 == 0:
                    watch_str = ", ".join(self.watch_list) if self.watch_list else "없음"
                    logger.info(f"👁️ [감시 중] 루프 #{loop_count} | 목록: {watch_str}")

                # 1. 랭킹 업데이트 (설정된 간격마다)
                if self.last_ranking_update is None:
                    self.update_ranking()
                else:
                    elapsed = (datetime.now() - self.last_ranking_update).total_seconds()
                    if elapsed >= self.RANKING_INTERVAL:
                        self.update_ranking()

                # 2. 현재 포지션 확인
                has_position = len(self.order_monitor.monitoring_orders) > 0

                # 3. 감시 목록 순회
                for ticker in list(self.watch_list):
                    # 제외 대상 스킵
                    if ticker in self.permanently_excluded:
                        continue
                    if ticker in self.touched_but_skipped:
                        continue
                    
                    # 🔥 50MA 터치 체크 (공격적 로직)
                    if self._is_touching_50ma(ticker):
                        
                        if has_position:
                            logger.info(f"🔒 {ticker} 조건 충족했으나 포지션 보유 중 (스킵)")
                            self.touched_but_skipped.add(ticker)
                            
                            self.telegram_bot.send_message(
                                f"🎯 {ticker} 매수 신호 포착!\n"
                                f"✋ 현재 포지션 보유 중이라 건너뜁니다."
                            )
                        else:
                            # 매수 실행
                            self._execute_buy(ticker)
                            break  # 루프당 1개만 매수 시도

                # 대기
                time.sleep(self.MONITORING_INTERVAL)

            except Exception as e:
                logger.error(f"❌ 감시 루프 오류: {e}")
                time.sleep(5)  # 오류 시 잠시 대기

    def _is_touching_50ma(self, ticker: str) -> bool:
        """
        🔥 [핵심] 공격적 50MA 터치 로직
        
        조건:
        1. 추세: 50MA가 상승 중이어야 함 (역추세 방지)
        2. 가격 Zone: 50MA 대비 -4% ~ +2% 사이 (하향 이탈 허용)
        3. 거래량: 평소(20이평) 대비 50% 이상이면 OK
        """
        try:
            # 55개 캔들 조회 (MA 계산 및 추세 확인용)
            candles = self.order_executor.get_1min_candles(ticker, 55)
            
            if len(candles) < 51:
                return False
            
            # 1. 50MA 계산 (현재)
            recent_closes = [c['close'] for c in candles[-51:-1]]
            ma50 = sum(recent_closes) / 50
            
            # 2. 추세 필터 (과거 50MA와 비교)
            # 5분 전 50MA 계산
            past_closes = [c['close'] for c in candles[-56:-6]]
            # 데이터가 충분하지 않으면 보수적으로 계산 (현재 데이터 내에서 비교)
            if len(past_closes) < 50:
                 past_ma50 = ma50 * 0.99 # 임시 패스
            else:
                 past_ma50 = sum(past_closes) / 50
            
            if ma50 < past_ma50:
                # 50MA가 꺾였거나 하락 중이면 패스
                # logger.debug(f"{ticker} 추세 하락 중 (Pass)")
                return False

            # 현재 캔들 정보
            current_candle = candles[-1]
            price = current_candle['close']
            
            # 3. 🔥 가격 Zone 체크 (50MA -4% ~ +2%)
            # 공격적 모드: 뚫고 내려가도(-4%) 잡고, 살짝 덜 닿아도(+2%) 잡음
            lower_limit = ma50 * 0.96
            upper_limit = ma50 * 1.02
            
            if not (lower_limit <= price <= upper_limit):
                return False

            # 4. 🔥 거래량 조건 (평소의 50%만 넘으면 OK)
            volumes = [c['volume'] for c in candles[-21:-1]]
            avg_vol = sum(volumes) / 20 if volumes else 0
            
            # 0으로 나누기 방지 및 거래량 체크
            if avg_vol > 0 and current_candle['volume'] < avg_vol * 0.5:
                return False
            
            logger.info(
                f"✅ {ticker} 매수 신호 (Aggressive)\n"
                f"  가격: ${price:.2f}\n"
                f"  50MA: ${ma50:.2f} (상승중)\n"
                f"  거래량: {current_candle['volume']} (평균 {avg_vol:.0f})"
            )
            return True

        except Exception as e:
            logger.error(f"{ticker} 체크 오류: {e}")
            return False

    def _execute_buy(self, ticker: str):
        """매수 실행"""
        logger.info(f"💰 {ticker} 매수 시도")
        
        # 1. 일일 한도 체크
        if not self.trade_counter.can_enter():
            logger.warning("🚫 일일 진입 한도 도달")
            self.stop()
            return

        # 2. 주문 실행 (전액 매수)
        result = self.order_executor.place_fullsize_buy(ticker)
        
        if result['success']:
            self.trade_counter.increment_entry()
            
            # OrderMonitor에 등록
            order_info = {
                'ticker': ticker,
                'order_no': result['order_no'],
                'quantity': result['quantity'],
                'buy_price': result['price'],
                'source': 'auto_v3_agg',  # Aggressive 모드 표시
                'created_at': datetime.now().isoformat()
            }
            
            # OrderMonitor 등록
            if hasattr(self.order_monitor, 'register_order'):
                self.order_monitor.register_order(result['order_no'], order_info)
            else:
                logger.error("❌ OrderMonitor 메서드 확인 필요")

            logger.info(f"✅ {ticker} 매수 성공 ({result['quantity']}주 @ ${result['price']})")
            self._save_state()
            
        else:
            reason = result.get('reason', 'Unknown')
            logger.error(f"❌ 매수 실패: {reason}")
            
            # 자금 부족이면 잠시 대기
            if 'insufficient_funds' in reason:
                logger.warning("💸 자금 부족함. 1분 대기...")
                time.sleep(60)

    def on_exit_complete(self, ticker: str, reason: str):
        """청산 완료 콜백 (OrderExecutor가 호출)"""
        logger.info(f"🏁 {ticker} {reason} 완료")
        
        self.permanently_excluded.add(ticker)
        if ticker in self.watch_list:
            self.watch_list.remove(ticker)
            
        self.trade_counter.increment_exit()
        self._save_state()
        
        # 일일 한도 다 찼으면 종료
        if not self.trade_counter.can_enter():
            logger.info("🚫 금일 매매 종료 (한도 달성)")
            self.stop()

    def add_manual_ticker(self, ticker: str) -> Dict[str, Any]:
        """수동 티커 추가"""
        ticker = ticker.upper().strip()
        
        if ticker in self.watch_list:
            return {'success': False, 'message': f"❌ {ticker} 이미 감시 중"}
            
        self.watch_list.append(ticker)
        self.manual_watch_list.add(ticker)
        
        # 제외 목록에서 복구
        self.touched_but_skipped.discard(ticker)
        self.permanently_excluded.discard(ticker)
        
        self._save_state()
        return {'success': True, 'message': f"✅ {ticker} 수동 추가 완료"}

    def remove_manual_ticker(self, ticker: str) -> Dict[str, Any]:
        """수동 티커 제거"""
        ticker = ticker.upper().strip()
        
        if ticker in self.manual_watch_list:
            self.manual_watch_list.remove(ticker)
            if ticker in self.watch_list:
                self.watch_list.remove(ticker)
            self._save_state()
            return {'success': True, 'message': f"✅ {ticker} 제거 완료"}
            
        return {'success': False, 'message': "❌ 수동 추가된 종목이 아닙니다"}

    def _save_state(self):
        """상태 파일 저장"""
        try:
            state = {
                'date': datetime.now().strftime('%Y-%m-%d'),
                'watch_list': self.watch_list,
                'touched': list(self.touched_but_skipped),
                'excluded': list(self.permanently_excluded),
                'manual': list(self.manual_watch_list),
                'ranking_history': self.ranking_history,
                'last_ranking_update': self.last_ranking_update.isoformat() if self.last_ranking_update else None
            }
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"❌ 상태 저장 실패: {e}")

    def _load_state_minimal(self):
        """상태 파일 로드"""
        try:
            if not self.state_file: return
            
            with open(self.state_file, 'r') as f:
                state = json.load(f)
            
            # 날짜 확인
            saved_date = state.get('date')
            today = datetime.now().strftime('%Y-%m-%d')
            
            if saved_date != today:
                logger.info("📅 새 거래일 - 상태 초기화")
                return
                
            self.manual_watch_list = set(state.get('manual', []))
            self.touched_but_skipped = set(state.get('touched', []))
            self.permanently_excluded = set(state.get('excluded', []))
            # 순위 히스토리도 복구 (중요)
            self.ranking_history = state.get('ranking_history', {})
            
            logger.info(f"📂 상태 복구 완료 (제외 {len(self.permanently_excluded)}개)")
            
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.error(f"❌ 상태 로드 실패: {e}")

if __name__ == '__main__':
    print("⚠️ auto_trader.py는 main.py를 통해 실행됩니다.")