# main.py
import time
import datetime
import pytz 
import json 
import os   
import threading
import random # [필수 추가] 좀비 리스트 방지를 위한 셔플용
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
    # [메인 루프] 무한 반복
    # ---------------------------------------------------------
    while True:
        try:
            # 1. 현재 시간 측정 (가장 먼저 해야 함)
            now_et = datetime.datetime.now(pytz.timezone('US/Eastern'))
            
            # ==================================================================
            # 🚩 [순서 변경] 2. [Active Time] 활동 시간 체크 (최우선 순위)
            # ==================================================================
            # 이유: 장이 닫힌 시간(Sleep)에는 생존 신고도, 매매도 할 필요가 없으므로
            # 가장 먼저 체크하여 루프 하단부 실행을 원천 차단해야 합니다.
            is_active, reason = is_active_market_time()
            
            if not is_active:
                # [슬립 모드 진입]
                if not was_sleeping:
                    logger.warning(f"💤 Sleep Mode: {reason}")
                    bot.send_message(f"💤 [대기] {reason}")
                    was_sleeping = True
                
                # [핵심 수정] 슬립 모드일 때는 1분 대기 후 continue로 루프 처음으로 돌아갑니다.
                # 이렇게 하면 아래에 있는 'E. 생존 신고' 로직에 도달하지 않으므로 문자가 오지 않습니다.
                time.sleep(60) 
                continue 
            
            # [기상 알림] 잠에서 깨어난 경우
            if was_sleeping:
                bot.send_message(f"🌅 [기상] 시장 감시 시작 ({reason})")
                was_sleeping = False
                portfolio.sync_with_kis() # 자고 일어나면 잔고 동기화

            # ==================================================================
            # E. [생존 신고] (Active 상태일 때만 실행됨)
            # ==================================================================
            # 위에서 continue로 걸러지지 않고 내려왔다는 것은 '깨어있다(Active)'는 뜻입니다.
            if time.time() - last_heartbeat_time > HEARTBEAT_INTERVAL:
                eq = portfolio.total_equity
                pos_cnt = len(portfolio.positions)
                cur_k = datetime.datetime.now(tz_kst).strftime("%H:%M")
                cur_n = datetime.datetime.now(tz_et).strftime("%H:%M")
                
                bot.send_message(f"💓 [생존] KR {cur_k} / NY {cur_n}\n자산 ${eq:,.0f} | 보유 {pos_cnt}개")
                last_heartbeat_time = time.time()

            # ============================================
            # 0. [Daily Reset] 날짜 변경 체크
            # ============================================
            new_date_str = now_et.strftime("%Y-%m-%d")
            if new_date_str != current_date_str:
                logger.info(f"📅 [New Day] {current_date_str} -> {new_date_str}")
                portfolio.ban_list.clear()
                active_candidates.clear()
                save_state(portfolio.ban_list, active_candidates) 
                logger.info("✨ 데이터 초기화 완료")
                current_date_str = new_date_str

            # ============================================
            # 1. [EOS] 장 마감 강제 청산 (15:50)
            # ============================================
            if now_et.hour == 15 and now_et.minute >= 50:
                logger.info("🏁 [EOS] 정규장 마감 임박. 강제 청산.")
                if portfolio.positions:
                    bot.send_message("🚨 [장 마감] 안전을 위해 전량 매도합니다.")
                    for ticker in list(portfolio.positions.keys()):
                        msg = order_manager.execute_sell(portfolio, ticker, "EOS (장마감)")
                        if msg: bot.send_message(msg)
                        time.sleep(1)
                
                # 마감 후 긴 대기 (4시간)
                save_state(portfolio.ban_list, active_candidates)
                bot.send_message("😴 [Sleep] 내일 뵙겠습니다.")
                time.sleep(60 * 60 * 4)
                continue

            # ============================================
            # 2. [Active Time] 활동 시간 체크
            # ============================================
            is_active, reason = is_active_market_time()
            if not is_active:
                if not was_sleeping:
                    logger.warning(f"💤 Sleep Mode: {reason}")
                    bot.send_message(f"💤 [대기] {reason}")
                    was_sleeping = True
                time.sleep(60)
                continue
            
            if was_sleeping:
                bot.send_message(f"🌅 [기상] 시장 감시 시작 ({reason})")
                was_sleeping = False
                portfolio.sync_with_kis()

            # ============================================
            # 3. [Logic] 매매 로직
            # ============================================
            
            # A. 동기화
            portfolio.sync_with_kis()

            # B. [매도] 보유 종목 관리
            for ticker in list(portfolio.positions.keys()):
                real_time_price = kis.get_current_price(ticker)
                
                # 가격 조회 실패 시 건너뜀
                if real_time_price is None or real_time_price <= 0: 
                    continue
                
                # 포지션 정보 및 진입 시간 가져오기
                pos = portfolio.positions[ticker]
                entry_price = pos['entry_price']
                
                # 🕒 [Time Cut 핵심] real_portfolio에서 저장한 진입 시간 호출
                entry_time = pos.get('entry_time') 

                # 🧠 [전략 호출] 매도 판단을 Strategy에게 위임
                # (수익률 계산, 타임 컷 여부 등을 전략 내부에서 판단함)
                exit_signal = strategy.check_exit_signal(
                    current_price=real_time_price, 
                    entry_price=entry_price,
                    entry_time=entry_time
                )
                
                # 매도 신호가 왔다면 실행
                if exit_signal:
                    reason = exit_signal['reason']
                    # [Fix] 매도 시 현재가(real_time_price)를 전달하여 $0.00 표기 오류 수정
                    result = order_manager.execute_sell(portfolio, ticker, reason, price=real_time_price)
                    
                    if result:
                        bot.send_message(result['msg'])
                        save_state(portfolio.ban_list, active_candidates)

            # C. [매수] 신규 종목 스캔 (핵심 수정 구간)
            fresh_targets = listener.scan_markets(
                ban_list=portfolio.ban_list,
                active_candidates=active_candidates
            )
            
            if fresh_targets:
                # 새로운 종목 발견 로그
                new_ones = [t for t in fresh_targets if t not in active_candidates]
                if new_ones:
                    logger.info(f"🔎 [Scan] 신규 발견: {new_ones}")
                
                # 감시 목록에 업데이트 (누적)
                active_candidates.update(fresh_targets)
                save_state(portfolio.ban_list, active_candidates)
            
            # [FIX 1] 검사할 후보군 선정 (보유 중/밴 당한 것 제외)
            valid_candidates = [
                sym for sym in list(active_candidates)
                if not portfolio.is_holding(sym) and not portfolio.is_banned(sym)
            ]

            # [FIX 2] 셔플 (Shuffle) - 중요!
            # 매번 순서를 섞어서, 리스트 뒤쪽에 있는 종목도 검사 기회를 갖게 함
            random.shuffle(valid_candidates)
            
            # [FIX 3] 상위 10개만 추출 (Rate Limit 고려)
            scanned_targets = valid_candidates[:10]
            
            # 상태 표시용 리스트 업데이트
            listener.current_watchlist = scanned_targets 

            if not scanned_targets:
                time.sleep(1)
                continue

            # D. [전략 확인]
            for sym in scanned_targets:
                time.sleep(0.5) # API 호출 간격
                
                df = kis.get_minute_candles("NASD", sym)
                if df.empty: continue

                signal = strategy.check_buy_signal(df, ticker=sym)
                if signal:
                    signal['ticker'] = sym
                    
                    if portfolio.has_open_slot():
                        result = order_manager.execute_buy(portfolio, signal)
                        if result and result.get('msg'):
                            bot.send_message(result['msg'])
                            if result['status'] == 'success':
                                # 매수 성공 시 감시 리스트에서 제거할 수도 있지만,
                                # 여기선 보유 중 체크 로직이 있으므로 놔둬도 됨
                                if not portfolio.has_open_slot(): break
                        else:
                            # 진입 실패 (자금 부족 등) -> 밴 처리
                            logger.warning(f"🚌 [실패] {sym} 매수 실패. 금일 제외.")
                            portfolio.ban_list.add(sym)
                            save_state(portfolio.ban_list, active_candidates) 
                    else:
                        logger.warning(f"🔒 [Full] {sym} 슬롯 꽉 참. 금일 제외.")
                        portfolio.ban_list.add(sym)
                        save_state(portfolio.ban_list, active_candidates)

            time.sleep(1)

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