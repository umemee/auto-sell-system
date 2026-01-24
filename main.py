import time
import datetime
import pytz 
import json 
import os   
import threading
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
# Config에서 시간 설정 가져오기 (없으면 기본값 4시~20시)
ACTIVE_START_HOUR = getattr(Config, 'ACTIVE_START_HOUR', 4) 
ACTIVE_END_HOUR = getattr(Config, 'ACTIVE_END_HOUR', 20)    

def is_active_market_time():
    """
    [설명] 현재 미국 시간이 매매 가능한 시간인지 확인합니다.
    - 서버 시간이 한국(KST)이어도, 'US/Eastern' 기준으로 변환하여 판단합니다.
    """
    # 1. 미국 동부 시간(EST/EDT) 구하기
    tz_et = pytz.timezone('US/Eastern')
    now_et = datetime.datetime.now(tz_et)
    
    # 2. 한국 시간 구하기 (로그 출력용)
    tz_kst = pytz.timezone('Asia/Seoul')
    now_kst = datetime.datetime.now(tz_kst)

    # 3. 주말 체크 (0:월 ~ 4:금, 5:토, 6:일)
    if now_et.weekday() >= 5: 
        return False, f"주말 (Weekend) - KST: {now_kst.strftime('%H:%M')}"

    # 4. 휴장일 체크 (미국 공휴일)
    holidays = [
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", 
        "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07", 
        "2026-11-26", "2026-12-25"
    ]
    if now_et.strftime("%Y-%m-%d") in holidays:
        return False, "미국 증시 휴장일 (Holiday)"

    # 5. 시간 범위 체크
    current_hour = now_et.hour
    if ACTIVE_START_HOUR <= current_hour < ACTIVE_END_HOUR:
        # [정상] 활동 시간
        return True, f"Active Market (NY: {now_et.strftime('%H:%M')} | KR: {now_kst.strftime('%H:%M')})"
    
    # [비활성] 장 마감 후 또는 장 시작 전
    return False, f"After Market / Night (NY: {now_et.strftime('%H:%M')} | KR: {now_kst.strftime('%H:%M')})"

# =========================================================
# 🚀 [메인 시스템]
# =========================================================
def main():
    logger.info("🚀 GapZone System v5.2 (Vibe Coding Edition) Starting...")
    
    # [초기 진단 로그] 현재 시간 인식 상태 출력
    tz_kst = pytz.timezone('Asia/Seoul')
    tz_et = pytz.timezone('US/Eastern')
    now_kst_start = datetime.datetime.now(tz_kst)
    now_et_start = datetime.datetime.now(tz_et)
    
    logger.info(f"⏰ [Time Check] Korea: {now_kst_start.strftime('%Y-%m-%d %H:%M:%S')} | NY: {now_et_start.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"⚙️ [Config] 활동 시간: NY {ACTIVE_START_HOUR}:00 ~ {ACTIVE_END_HOUR}:00")

    last_heartbeat_time = time.time()
    HEARTBEAT_INTERVAL = getattr(Config, 'HEARTBEAT_INTERVAL_SEC', 1800)
    was_sleeping = False
    
    # 날짜 변경 감지용 (미국 시간 기준)
    current_date_str = now_et_start.strftime("%Y-%m-%d")

    try:
        # 1. 인프라 초기화 (객체 생성)
        token_manager = KisAuth()
        kis = KisApi(token_manager)
        bot = TelegramBot()
        listener = MarketListener(kis)
        
        # 2. 포트폴리오 및 주문 관리자 생성
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
        
        # (수동 밴 리스트 - 필요시 사용)
        manual_ban = ['IVF', 'TWG', 'BTTC'] # 예시
        portfolio.ban_list.update(manual_ban)
        
        logger.info(f"💾 [Memory] 복구 완료 | 🚫Ban: {len(portfolio.ban_list)}개, 👁️Watch: {len(active_candidates)}개")
        
        # 시작 메시지 전송
        start_msg = (
            f"⚔️ [시스템 가동 v5.2]\n"
            f"⏰ 시간: KR {now_kst_start.strftime('%H:%M')} / NY {now_et_start.strftime('%H:%M')}\n"
            f"💰 자산: ${portfolio.total_equity:,.0f}\n"
            f"🎰 슬롯: {len(portfolio.positions)} / {portfolio.MAX_SLOTS}"
        )
        bot.send_message(start_msg)
        
        # 텔레그램 상태 조회 함수 연결
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
        
        # [수정] 텔레그램 봇을 별도 스레드로 분리하여 메인 루프가 막히지 않게 함
        def run_bot_thread():
            bot.start()
            
        # 데몬 스레드로 실행 (메인 프로그램 종료 시 봇도 같이 종료됨)
        t = threading.Thread(target=run_bot_thread)
        t.daemon = True 
        t.start()
        
        logger.info("🤖 텔레그램 봇이 백그라운드 스레드에서 시작되었습니다.") # 확인용 로그

    except Exception as e:
        logger.critical(f"❌ 초기화 실패: {e}")
        return

    # ---------------------------------------------------------
    # [메인 루프] 무한 반복
    # ---------------------------------------------------------
    while True:
        try:
            # 매 루프마다 현재 미국 시간 갱신
            now_et = datetime.datetime.now(pytz.timezone('US/Eastern'))
            
            # ============================================
            # 0. [Daily Reset] 하루가 지났는지 체크
            # ============================================
            new_date_str = now_et.strftime("%Y-%m-%d")
            if new_date_str != current_date_str:
                logger.info(f"📅 [New Day] 날짜 변경: {current_date_str} -> {new_date_str}")
                portfolio.ban_list.clear()
                active_candidates.clear()
                save_state(portfolio.ban_list, active_candidates) 
                logger.info("✨ 금일 데이터 초기화 완료")
                current_date_str = new_date_str

            # ============================================
            # 1. [EOS] 장 마감 강제 청산 (오후 3:50)
            # ============================================
            if now_et.hour == 15 and now_et.minute >= 50:
                logger.info("🏁 [EOS] 정규장 마감 임박. 강제 청산 실행.")
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
                # 자는 시간이라면 (Sleep Mode)
                if not was_sleeping:
                    logger.warning(f"💤 Sleep Mode: {reason}")
                    bot.send_message(f"💤 [대기] {reason}")
                    was_sleeping = True
                time.sleep(60) # 1분 대기
                continue
            
            # 깨어나는 순간
            if was_sleeping:
                bot.send_message(f"🌅 [기상] 시장 감시를 시작합니다! ({reason})")
                was_sleeping = False
                portfolio.sync_with_kis() # 자고 일어나면 잔고 동기화

            # ============================================
            # 3. [Logic] 매매 로직 실행
            # ============================================
            
            # A. 잔고/보유종목 동기화
            portfolio.sync_with_kis()

            # B. [매도 검사] 보유 중인 종목 체크
            for ticker in list(portfolio.positions.keys()):
                real_time_price = kis.get_current_price(ticker)
                if real_time_price is None or real_time_price <= 0: continue
                
                # 수익률 계산
                pos = portfolio.positions[ticker]
                entry_price = pos['entry_price']
                pnl_rate = (real_time_price - entry_price) / entry_price
                
                # 매도 조건 확인 (익절/손절)
                sell_signal = False
                reason_sell = ""
                
                if pnl_rate >= target_profit_rate:
                    sell_signal = True
                    reason_sell = f"TAKE_PROFIT (익절 {pnl_rate*100:.1f}%)"
                elif pnl_rate <= sl_rate:
                    sell_signal = True
                    reason_sell = f"STOP_LOSS (손절 {pnl_rate*100:.1f}%)"

                # 매도 실행
                if sell_signal:
                    result = order_manager.execute_sell(portfolio, ticker, reason_sell)
                    if result:
                        bot.send_message(result['msg'])
                        save_state(portfolio.ban_list, active_candidates)

            # C. [매수 검사] 신규 종목 스캔
            # 새벽 시간대엔 종목이 잘 안 잡힐 수 있음
            fresh_targets = listener.scan_markets()
            
            if fresh_targets:
                # 새로운 종목 발견 시
                new_ones = [t for t in fresh_targets if t not in active_candidates]
                if new_ones:
                    logger.info(f"🔎 [Scan] 신규 발견: {new_ones}")
                
                active_candidates.update(fresh_targets)
                save_state(portfolio.ban_list, active_candidates)
            
            # 포트폴리오에 없고, 밴 당하지 않은 종목만 추림
            scanned_targets = [
                sym for sym in list(active_candidates)
                if not portfolio.is_holding(sym) and not portfolio.is_banned(sym)
            ]
            listener.current_watchlist = scanned_targets 

            # 감시 대상이 없으면 잠시 대기
            if not scanned_targets:
                time.sleep(1)
                continue

            # D. [전략 확인] 분봉 데이터 분석 후 매수
            for sym in scanned_targets:
                time.sleep(0.5) # API 호출 제한 고려
                # 너무 많은 종목을 다 보면 느려지므로 앞에서부터 10개만 봄
                if scanned_targets.index(sym) > 10: break 
                
                df = kis.get_minute_candles("NASD", sym)
                if df.empty: continue

                signal = strategy.check_buy_signal(df, ticker=sym)
                if signal:
                    signal['ticker'] = sym
                    
                    # 슬롯(자금) 확인
                    if portfolio.has_open_slot():
                        result = order_manager.execute_buy(portfolio, signal)
                        if result and result.get('msg'):
                            bot.send_message(result['msg'])
                            if result['status'] == 'success':
                                if not portfolio.has_open_slot(): break
                        else:
                            logger.warning(f"🚌 [실패] {sym} 매수 실패하여 밴 처리.")
                            portfolio.ban_list.add(sym)
                            save_state(portfolio.ban_list, active_candidates) 
                    else:
                        logger.warning(f"🔒 [Full] {sym} 자리가 없어 패스.")
                        portfolio.ban_list.add(sym)
                        save_state(portfolio.ban_list, active_candidates)

            # E. [생존 신고] 30분마다
            if time.time() - last_heartbeat_time > HEARTBEAT_INTERVAL:
                eq = portfolio.total_equity
                pos_cnt = len(portfolio.positions)
                
                # 현재 시간도 같이 보내줌 (안심용)
                cur_k = datetime.datetime.now(tz_kst).strftime("%H:%M")
                cur_n = datetime.datetime.now(tz_et).strftime("%H:%M")
                
                bot.send_message(f"💓 [생존] KR {cur_k} / NY {cur_n}\n자산 ${eq:,.0f} | 보유 {pos_cnt}개")
                last_heartbeat_time = time.time()

            time.sleep(1) # 루프 과부하 방지

        except KeyboardInterrupt:
            logger.info("🛑 관리자에 의한 수동 종료")
            bot.send_message("🛑 시스템을 종료합니다.")
            save_state(portfolio.ban_list, active_candidates)
            break
            
        except Exception as e:
            error_msg = f"⚠️ [ERROR] 시스템 오류: {e}\n👉 10초 후 재시도..."
            logger.error(error_msg)
            # 에러가 너무 자주 오면 텔레그램 끄는 게 나을 수도 있음
            # bot.send_message(error_msg) 
            time.sleep(10)

if __name__ == "__main__":
    main()

