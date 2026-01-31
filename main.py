# main.py
import time
import datetime
import pytz 
import json 
import os   
import threading
import random 
from config import Config
from infra.utils import get_logger
from infra.kis_api import KisApi
from infra.kis_auth import KisAuth
from infra.telegram_bot import TelegramBot
from infra.real_portfolio import RealPortfolio
from infra.real_order_manager import RealOrderManager
from data.market_listener import MarketListener
from strategy import get_strategy

logger = get_logger("Main")
STATE_FILE = "system_state.json"

# =========================================================
# 💾 [상태 저장/로드] 시스템 재부팅 대비
# =========================================================
def save_state(ban_list, active_candidates):
    """[설명] 밴 리스트와 감시 중인 종목을 파일로 저장합니다."""
    try:
        state = {
            "ban_list": list(ban_list),
            "active_candidates": list(active_candidates),
            "date": datetime.datetime.now().strftime("%Y-%m-%d")
        }
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        logger.error(f"⚠️ 상태 저장 실패: {e}")

def load_state():
    """[설명] 저장된 상태 파일이 있다면 불러옵니다 (재부팅 시 유용)."""
    if not os.path.exists(STATE_FILE):
        return set(), set()
    
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
            
        # 날짜가 바뀌었으면(어제 파일이면) 초기화
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        if state.get("date") != today:
            logger.info("📅 날짜 변경으로 저장된 상태를 초기화합니다.")
            return set(), set()
            
        return set(state.get("ban_list", [])), set(state.get("active_candidates", []))
    except Exception as e:
        logger.error(f"⚠️ 상태 로드 실패: {e}")
        return set(), set()

# =========================================================
# 🕒 [시간 체크] 한국 시간 vs 미국 시간
# =========================================================
ACTIVE_START_HOUR = getattr(Config, 'ACTIVE_START_HOUR', 4) 
ACTIVE_END_HOUR = getattr(Config, 'ACTIVE_END_HOUR', 20)    

def is_active_market_time():
    """
    [설명] 현재 미국 시간이 매매 가능한 시간인지 확인합니다.
    """
    tz_et = pytz.timezone('US/Eastern')
    now_et = datetime.datetime.now(tz_et)
    
    tz_kst = pytz.timezone('Asia/Seoul')
    now_kst = datetime.datetime.now(tz_kst)

    # 주말 체크
    if now_et.weekday() >= 5: 
        return False, f"주말 (Weekend) - KST: {now_kst.strftime('%H:%M')}"

    # 휴장일 체크 (2026년 기준)
    holidays = [
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", 
        "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07", 
        "2026-11-26", "2026-12-25"
    ]
    if now_et.strftime("%Y-%m-%d") in holidays:
        return False, "미국 증시 휴장일 (Holiday)"

    current_hour = now_et.hour
    if ACTIVE_START_HOUR <= current_hour < ACTIVE_END_HOUR:
        return True, f"Active Market (NY: {now_et.strftime('%H:%M')} | KR: {now_kst.strftime('%H:%M')})"
    
    return False, f"After Market / Night (NY: {now_et.strftime('%H:%M')} | KR: {now_kst.strftime('%H:%M')})"

# =========================================================
# 🚀 [메인 시스템]
# =========================================================
def main():
    logger.info("🚀 GapZone System v5.3 (Final Edition) Starting...")
    
    tz_kst = pytz.timezone('Asia/Seoul')
    tz_et = pytz.timezone('US/Eastern')
    now_kst_start = datetime.datetime.now(tz_kst)
    now_et_start = datetime.datetime.now(tz_et)
    
    logger.info(f"⏰ [Time Check] Korea: {now_kst_start.strftime('%Y-%m-%d %H:%M:%S')} | NY: {now_et_start.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"⚙️ [Config] 활동 시간: NY {ACTIVE_START_HOUR}:00 ~ {ACTIVE_END_HOUR}:00")

    last_heartbeat_time = time.time()
    HEARTBEAT_INTERVAL = getattr(Config, 'HEARTBEAT_INTERVAL_SEC', 1800)
    was_sleeping = False
    
    # [수정] 중복 실행 방지를 위한 변수 추가
    last_processed_minute = None
    
    current_date_str = now_et_start.strftime("%Y-%m-%d")

    try:
        # 1. 인프라 초기화
        token_manager = KisAuth()
        kis = KisApi(token_manager)
        bot = TelegramBot()
        listener = MarketListener(kis)
        
        # 2. 포트폴리오 및 주문 관리자
        portfolio = RealPortfolio(kis)
        order_manager = RealOrderManager(kis)
        strategy = get_strategy() 
        
        target_profit_rate = getattr(Config, 'TP_PCT', 0.10)
        sl_rate = -abs(getattr(Config, 'SL_PCT', 0.40))

        # 3. 서버 동기화 및 상태 복구
        logger.info("📡 증권사 서버와 동기화 중...")
        portfolio.sync_with_kis()
        
        loaded_ban, loaded_candidates = load_state()
        portfolio.ban_list.update(loaded_ban)
        active_candidates = loaded_candidates 
        
        logger.info(f"💾 [Memory] 복구 완료 | 🚫Ban: {len(portfolio.ban_list)}개, 👁️Watch: {len(active_candidates)}개")
        
        start_msg = (
            f"⚔️ [시스템 가동 v5.3]\n"
            f"⏰ 시간: KR {now_kst_start.strftime('%H:%M')} / NY {now_et_start.strftime('%H:%M')}\n"
            f"💰 자산: ${portfolio.total_equity:,.0f}\n"
            f"🎰 슬롯: {len(portfolio.positions)} / {portfolio.MAX_SLOTS}"
        )
        bot.send_message(start_msg)
        
        # 상태 조회 함수 (Telegram 연동)
        def get_status_data():
            return {
                'cash': portfolio.balance,
                'total_equity': portfolio.total_equity,
                'positions': portfolio.positions,
                'targets': getattr(listener, 'current_watchlist', []),
                'ban_list': list(portfolio.ban_list),
                'loss': 0.0,
                'loss_limit': getattr(Config, 'MAX_DAILY_LOSS_PCT', 0.0)
            }
        bot.set_status_provider(get_status_data)
        
        # 텔레그램 봇 스레드 실행
        def run_bot_thread():
            bot.start()
            
        t = threading.Thread(target=run_bot_thread)
        t.daemon = True 
        t.start()
        logger.info("🤖 텔레그램 봇 시작됨")

    except Exception as e:
        logger.critical(f"❌ 초기화 실패: {e}")
        return

    # ---------------------------------------------------------
    # [메인 루프] 무한 반복 (Final Optimized Version)
    # ---------------------------------------------------------
    while True:
        try:
            # =========================================================
            # 🕒 [Time Sync] 캔들 완성형 (00초~05초 진입)
            # =========================================================
            # 미국 현지 시간 기준
            now = datetime.datetime.now(pytz.timezone('America/New_York'))
            current_minute_str = now.strftime("%H:%M")
            
            # [핵심 수정] 0초~5초 사이(매분 시작)에만 로직 실행 (캔들 마감 확인용)
            # 59초 방식은 데이터가 덜 닫힌 상태일 수 있어 위험합니다.
            # 5초가 넘어가면 다음 분까지 대기합니다.
            if now.second > 5:
                # CPU 낭비 방지를 위해 적당히 쉽니다 (0.5초)
                time.sleep(0.5)
                continue
            
            # [핵심 수정] 이번 분에 이미 실행했다면 건너뜀 (중복 실행 방지)
            if last_processed_minute == current_minute_str:
                time.sleep(0.5)
                continue
                
            # --- 여기서부터는 매 분의 00초~05초 사이에 "딱 한 번"만 실행됩니다 ---
            last_processed_minute = current_minute_str
            # logger.info(f"⏱️ [New Candle] {current_minute_str} Analysis Start...") 
            
            # ---------------------------------------------------------
            # 🛑 [EOD] 장 마감 강제 청산 (안전장치)
            # ---------------------------------------------------------
            # settings.py의 TIME_HARD_CUTOFF 확인 (기본값 15:55)
            cutoff_time = getattr(Config, 'TIME_HARD_CUTOFF', "15:55") 
            
            if now.strftime("%H:%M") == cutoff_time:
                logger.warning(f"⏰ [장 마감] 강제 청산 실행 ({cutoff_time})")
                bot.send_message(f"🚨 [장 마감] {cutoff_time} 강제 청산 실행")
                
                # 보유 중인 모든 종목 시장가 매도
                if portfolio.is_holding():
                    for ticker in list(portfolio.positions.keys()):
                        order_manager.execute_sell(portfolio, ticker, "FORCE_EOD_EXIT")
                        time.sleep(0.2) # 주문 간격
                
                # 상태 저장 후 루프 종료 (다음 날 재실행 필요)
                save_state(portfolio.ban_list, active_candidates)
                logger.info("👋 [System] 장 마감으로 시스템을 종료합니다.")
                time.sleep(300) 
                continue

            # =========================================================
            # 💤 [Sleep Mode] 활동 시간 체크
            # =========================================================
            is_active, reason = is_active_market_time()
            
            if not is_active:
                if not was_sleeping:
                    logger.warning(f"💤 Sleep Mode: {reason}")
                    bot.send_message(f"💤 [대기] {reason}")
                    was_sleeping = True
                    save_state(portfolio.ban_list, active_candidates) # 자기 전 상태 저장
                
                # 활동 시간이 아니면 1분 통째로 대기 (다음 분 0초까지 대기)
                time.sleep(30)
                continue
            
            # [기상] 잠에서 깨어난 경우
            if was_sleeping:
                bot.send_message(f"🌅 [기상] 시장 감시 시작 ({reason})")
                was_sleeping = False
                portfolio.sync_with_kis() # 자고 일어나면 잔고 동기화

            # =========================================================
            # 💓 [Heartbeat] 생존 신고
            # =========================================================
            if time.time() - last_heartbeat_time > HEARTBEAT_INTERVAL:
                eq = portfolio.total_equity
                pos_cnt = len(portfolio.positions)
                cur_k = datetime.datetime.now(tz_kst).strftime("%H:%M")
                cur_n = datetime.datetime.now(tz_et).strftime("%H:%M")
                
                bot.send_message(f"💓 [생존] KR {cur_k} / NY {cur_n}\n자산 ${eq:,.0f} | 보유 {pos_cnt}개")
                last_heartbeat_time = time.time()

            # =========================================================
            # 📅 [Daily Reset] 날짜 변경 체크
            # =========================================================
            new_date_str = now.strftime("%Y-%m-%d")
            if new_date_str != current_date_str:
                logger.info(f"📅 [New Day] {current_date_str} -> {new_date_str}")
                portfolio.ban_list.clear()
                active_candidates.clear()
                save_state(portfolio.ban_list, active_candidates)
                logger.info("✨ 데이터 초기화 완료")
                current_date_str = new_date_str

            # =========================================================
            # 🧠 [Logic] 매매 로직 시작 (매 분 1회 실행)
            # =========================================================
            
            # A. 포트폴리오 동기화 (오차 방지)
            portfolio.sync_with_kis()

            # ---------------------------------------------------------
            # B. [매도] 보유 종목 관리 (Check Exit)
            # ---------------------------------------------------------
            for ticker in list(portfolio.positions.keys()):
                
                # [추가] 1. 미체결 주문 확인 (중복 매도 방지)
                try:
                    pending_orders = kis.get_pending_orders(ticker)
                    if pending_orders:
                        # 이미 매도 주문이 걸려있으면 패스 (로그 생략 가능)
                        continue 
                except Exception:
                    pass
                # [수정] 단순 현재가(get_current_price) ❌ -> 분봉 데이터(get_minute_candles) ✅
                # 00초에 실행되므로 df.iloc[-2]가 방금 마감된 1분봉입니다.
                df = kis.get_minute_candles("NAS", ticker, limit=60)

                if df.empty or len(df) < 1: 
                    continue
                
                # [전략] 현재가(Tick)보다는 '방금 확정된 종가' 혹은 '현재 시가'를 기준으로 판단
                real_time_price = df.iloc[-1]['close'] # 현재 진행중인 봉의 현재가
                
                pos = portfolio.positions[ticker]
                entry_price = pos['entry_price']
                entry_time = pos.get('entry_time')

                # 전략에 매도 문의
                exit_signal = strategy.check_exit_signal(
                    current_price=real_time_price, 
                    entry_price=entry_price,
                    entry_time=entry_time
                )
                
                if exit_signal:
                    reason = exit_signal['reason']
                    result = order_manager.execute_sell(portfolio, ticker, reason, price=real_time_price)
                    if result:
                        bot.send_message(result['msg'])
                        save_state(portfolio.ban_list, active_candidates)
            
            # ---------------------------------------------------------
            # C. [스캔] 신규 급등주 포착
            # ---------------------------------------------------------
            fresh_targets = listener.scan_markets(
                ban_list=portfolio.ban_list,
                active_candidates=active_candidates
            )
            
            if fresh_targets:
                active_candidates.update(fresh_targets)
                save_state(portfolio.ban_list, active_candidates)
            
            # ---------------------------------------------------------
            # D. [매수] 진입 타점 확인 (핵심 수정: 히스토리 로딩)
            # ---------------------------------------------------------
            buy_candidates = [
                sym for sym in list(active_candidates)
                if not portfolio.is_holding(sym) and not portfolio.is_banned(sym)
            ]

            # [Random Shuffle] 좀비 리스트 방지
            random.shuffle(buy_candidates)
            
            # API 제한 고려 상위 15개만 체크
            targets_to_check = buy_candidates[:15]
            listener.current_watchlist = targets_to_check 

            for sym in targets_to_check:
                try:
                    # =========================================================
                    # [API 최적화] 분봉 데이터 조회 (하나로 통합)
                    # =========================================================
                    # 기존: 호가 조회(get_market_spread) -> 분봉 조회(get_minute_candles) 2번 호출
                    # 변경: 분봉 조회(get_recent_candles) 1번만 호출하여 판단 (API 절약)
                    
                    # kis_api.py에 새로 만든 함수 호출 (limit=60분)
                    # 이 함수는 API 문서에 맞춘 필드명(open, close 등)을 반환합니다.
                    df = kis.get_recent_candles(sym, limit=60)

                    if df.empty or len(df) < 20:
                        continue

                    # =========================================================
                    # 🧠 [Strategy] 전략 엔진 호출 (T-1 확정 봉 기준)
                    # =========================================================
                    # 수정된 strategy.py는 df의 [-2]번 인덱스(직전 완성봉)를 분석합니다.
                    signal = strategy.check_entry(sym, df)

                    if signal and signal['type'] == 'BUY':
                        
                        # [Double Check] 호가 확인 (선택 사항)
                        # 매수 신호가 떴을 때만 호가를 조회하여 슬리피지 방지
                        ask, bid, ask_vol, bid_vol = kis.get_market_spread(sym)
                        
                        # 호가 스프레드가 너무 크거나(3% 이상), 매도 물량(ask_vol)이 없으면 스킵
                        if ask > 0 and bid > 0:
                            spread = (ask - bid) / ask * 100
                            if spread > 3.0:
                                logger.warning(f"⚠️ [Spread] {sym}: 괴리율 과다 ({spread:.2f}%). 진입 보류.")
                                continue
                        
                        # 신호에 현재가(ask) 정보 업데이트 (시장가 매수 시 참고용)
                        signal['price'] = ask if ask > 0 else signal['price']
                        signal['ticker'] = sym

                        # =========================================================
                        # ⚡ [Execution] 주문 집행
                        # =========================================================
                        if portfolio.has_open_slot():
                            result = order_manager.execute_buy(portfolio, signal)
                            
                            if result:
                                if result.get('msg'):
                                    bot.send_message(result['msg'])
                                
                                if result['status'] == 'success':
                                    save_state(portfolio.ban_list, active_candidates)
                                    if not portfolio.has_open_slot():
                                        break # 슬롯 꽉 차면 루프 종료
                                else:
                                    # 실패 시 밴 처리
                                    logger.warning(f"🚌 [실패] {sym} 매수 실패. 금일 제외.")
                                    portfolio.ban_list.add(sym)
                                    save_state(portfolio.ban_list, active_candidates)

                    # [Rate Limit] API 호출 간격 조절 (초당 5회 제한 준수)
                    time.sleep(0.2)

                except Exception as e:
                    logger.error(f"❌ 매수 로직 에러({sym}): {e}")
                    continue
            
            # =========================================================
            # 💰 [Sync] 매도 후 잔고 최신화 (자금 부족 해결)
            # =========================================================
            if not portfolio.positions and portfolio.balance < 10:
                logger.info("🔄 [Sync] 매도 후 잔고 재동기화 수행...")
                portfolio.sync_balance() 

            # ---------------------------------------------------------
            # 루프 종료 후 대기
            # ---------------------------------------------------------
            # 이미 상단에서 타이밍 제어를 하므로 여기선 짧게 쉽니다.
            time.sleep(0.1)

        except KeyboardInterrupt:
            logger.info("🛑 관리자에 의한 수동 종료")
            bot.send_message("🛑 시스템을 종료합니다.")
            save_state(portfolio.ban_list, active_candidates)
            break
            
        except Exception as e:
            error_msg = f"⚠️ [ERROR] 시스템 오류: {e}\n👉 10초 후 재시도..."
            logger.error(error_msg)
            time.sleep(10)

if __name__ == "__main__":
    main()