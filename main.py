# main.py
import time
import datetime
import pytz 
import json # [필수]
import os   # [필수]
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
# 💾 [Persistence] 상태 저장/로드 함수 (Main 위로 이동)
# =========================================================
def save_state(ban_list, active_candidates):
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
    if not os.path.exists(STATE_FILE):
        return set(), set()
    
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
            
        # 날짜가 다르면(어제 파일이면) 초기화
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        if state.get("date") != today:
            logger.info("📅 날짜 변경으로 저장된 상태를 초기화합니다.")
            return set(), set()
            
        return set(state.get("ban_list", [])), set(state.get("active_candidates", []))
    except Exception as e:
        logger.error(f"⚠️ 상태 로드 실패: {e}")
        return set(), set()

# =========================================================
# 🕒 시간 및 휴장일 체크
# =========================================================
ACTIVE_START_HOUR = getattr(Config, 'ACTIVE_START_HOUR', 4) 
ACTIVE_END_HOUR = getattr(Config, 'ACTIVE_END_HOUR', 20)    

def is_active_market_time():
    """현재 시간이 활동 시간(Pre~Close)인지 확인 (휴장일 로직 추가)"""
    now_et = datetime.datetime.now(pytz.timezone('US/Eastern'))
    
    if now_et.weekday() >= 5: return False, "주말 (Weekend)"

    holidays = [
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", 
        "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07", 
        "2026-11-26", "2026-12-25"
    ]
    
    if now_et.strftime("%Y-%m-%d") in holidays:
        return False, "미국 증시 휴장일 (Holiday)"

    current_hour = now_et.hour
    if ACTIVE_START_HOUR <= current_hour < ACTIVE_END_HOUR:
        return True, "Active Market"
    return False, "After Market / Night"

# =========================================================
# 🚀 MAIN SYSTEM
# =========================================================
def main():
    logger.info("🚀 GapZone System v5.0 (Final Stability) Starting...")
    
    last_heartbeat_time = time.time()
    HEARTBEAT_INTERVAL = getattr(Config, 'HEARTBEAT_INTERVAL_SEC', 1800)
    was_sleeping = False
    current_date_str = datetime.datetime.now(pytz.timezone('US/Eastern')).strftime("%Y-%m-%d")

    try:
        # 1. 인프라 초기화
        token_manager = KisAuth()
        kis = KisApi(token_manager)
        bot = TelegramBot()
        listener = MarketListener(kis)
        
        # 2. 객체 생성
        portfolio = RealPortfolio(kis)
        order_manager = RealOrderManager(kis)
        strategy = get_strategy() 
        
        target_profit_rate = getattr(Config, 'TP_PCT', 0.10)
        sl_rate = -abs(getattr(Config, 'SL_PCT', 0.40))

        # 3. 상태 동기화
        logger.info("📡 증권사 서버와 동기화 중...")
        portfolio.sync_with_kis()
        
        # ---------------------------------------------------------
        # 💾 [수정 1] 재부팅 시 기억 복구 (Load)
        # ---------------------------------------------------------
        loaded_ban, loaded_candidates = load_state()
        
        # 복구된 밴 리스트 적용
        portfolio.ban_list.update(loaded_ban)
        # 감시 명단 복구
        active_candidates = loaded_candidates 
        
        # (선택) 하드코딩된 수동 밴 리스트가 있다면 추가 병합
        manual_ban = ['IVF', 'TWG', 'BTTC', 'RAPT', 'CCHH', 'CRVS', 'ICON', 'SHPH', 'AFJK', 'PTLE', 'SEGG', 'POLA', 'JAGX', 'LCFY', 'JFBR', 'AFJK', 'SVRE']
        portfolio.ban_list.update(manual_ban)
        
        logger.info(f"💾 [Memory] 시스템 상태 복구 완료 | 🚫Ban: {len(portfolio.ban_list)}개, 👁️Watch: {len(active_candidates)}개")
        # ---------------------------------------------------------

        start_msg = (
            f"⚔️ [시스템 가동 v5.1 - Sniper Mode]\n"
            f"🧠 전략: {strategy.name} (MA {strategy.ma_length})\n"
            f"💰 자산: ${portfolio.total_equity:,.0f}\n"
            f"🎯 목표: 익절 +{target_profit_rate*100:.1f}% / 손절 {sl_rate*100:.1f}%\n"
            f"🎰 슬롯: {len(portfolio.positions)} / {portfolio.MAX_SLOTS}"
        )
        bot.send_message(start_msg)
        
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
        bot.start()

    except Exception as e:
        logger.critical(f"❌ 초기화 실패: {e}")
        return

    # ---------------------------------------------------------
    # Main Loop
    # ---------------------------------------------------------
    while True:
        try:
            now_et = datetime.datetime.now(pytz.timezone('US/Eastern'))
            
            # 0. [Daily Reset]
            new_date_str = now_et.strftime("%Y-%m-%d")
            if new_date_str != current_date_str:
                logger.info(f"📅 [New Day] 날짜 변경: {current_date_str} -> {new_date_str}")
                portfolio.ban_list.clear()
                active_candidates.clear()
                
                # [수정 2] 초기화된 상태 즉시 저장
                save_state(portfolio.ban_list, active_candidates) 
                
                logger.info("✨ 금일 매매 금지 리스트 및 감시 명단 초기화 완료")
                current_date_str = new_date_str

            # 1. [EOS] 강제 청산
            if now_et.hour == 15 and now_et.minute >= 50:
                logger.info("🏁 [EOS] 정규장 마감 임박. 강제 청산 및 금일 매매 종료.")
                if portfolio.positions:
                    bot.send_message("🚨 [장 마감] EOS 강제 청산 실행 및 매매 종료!")
                    for ticker in list(portfolio.positions.keys()):
                        msg = order_manager.execute_sell(portfolio, ticker, "End of Session (EOS)")
                        if msg: bot.send_message(msg)
                        time.sleep(1)
                else:
                    logger.info("🏁 보유 포지션 없음. 안전하게 마감.")

                bot.send_message("😴 [Sleep] 금일 매매를 종료하고 내일 프리마켓까지 대기합니다.")
                
                # 장 마감 후 상태 저장하고 긴 대기
                save_state(portfolio.ban_list, active_candidates)
                time.sleep(60 * 60 * 4)
                continue

            # 2. [Active Time]
            is_active, reason = is_active_market_time()
            if not is_active:
                if not was_sleeping:
                    logger.warning(f"💤 Sleep Mode: {reason}")
                    bot.send_message(f"💤 [Sleep] {reason}")
                    was_sleeping = True
                time.sleep(60)
                continue
            
            if was_sleeping:
                bot.send_message("🌅 [Wake Up] 시장 감시 재개!")
                was_sleeping = False
                portfolio.sync_with_kis()

            # 3. [Sync]
            portfolio.sync_with_kis()

            # 4. [Exit Logic]
            for ticker in list(portfolio.positions.keys()):
                real_time_price = kis.get_current_price(ticker)
                if real_time_price is None or real_time_price <= 0: continue
                
                pos = portfolio.positions[ticker]
                pos['current_price'] = real_time_price
                entry_price = pos['entry_price']
                pnl_rate = (real_time_price - entry_price) / entry_price
                pos['pnl_pct'] = pnl_rate * 100

                sell_signal = False
                reason = ""
                if pnl_rate >= target_profit_rate:
                    sell_signal = True
                    reason = f"TAKE_PROFIT ({pnl_rate*100:.2f}% >= {target_profit_rate*100:.1f}%)"
                elif pnl_rate <= sl_rate:
                    sell_signal = True
                    reason = f"STOP_LOSS ({pnl_rate*100:.2f}%)"

                if sell_signal:
                    limit_price = None
                    if "TAKE_PROFIT" in reason: limit_price = real_time_price 
                    
                    result = order_manager.execute_sell(portfolio, ticker, reason, price=limit_price)
                    if result:
                        bot.send_message(result['msg'])
                        # [수정 2] 매도 후 밴 리스트 변경되었으므로 저장
                        save_state(portfolio.ban_list, active_candidates)

            # 5. [Entry Logic]
            fresh_targets = listener.scan_markets()
            
            if fresh_targets:
                active_candidates.update(fresh_targets)
                # [수정 2] 새로운 감시 종목 추가 시 저장
                save_state(portfolio.ban_list, active_candidates)
            
            scanned_targets = [
                sym for sym in list(active_candidates)
                if not portfolio.is_holding(sym) and not portfolio.is_banned(sym)
            ]
            listener.current_watchlist = scanned_targets 

            if not scanned_targets:
                time.sleep(1)
                continue

            for sym in scanned_targets:
                time.sleep(0.5)
                scanned_targets = scanned_targets[:10] 
                
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
                                if not portfolio.has_open_slot(): break
                        else:
                            logger.warning(f"🚌 [Missed Bus] {sym} 진입 실패. 금일 제외.")
                            portfolio.ban_list.add(sym)
                            # [수정 2] 밴 리스트 업데이트 저장
                            save_state(portfolio.ban_list, active_candidates) 
                    else:
                        logger.warning(f"🔒 [Shadow Scan] {sym} 기회 포착했으나 슬롯 Full. 금일 제외.")
                        portfolio.ban_list.add(sym)
                        # [수정 2] 밴 리스트 업데이트 저장
                        save_state(portfolio.ban_list, active_candidates)

            # 6. 생존 신고
            if time.time() - last_heartbeat_time > HEARTBEAT_INTERVAL:
                eq = portfolio.total_equity
                pos_cnt = len(portfolio.positions)
                bot.send_message(f"💓 [생존] 자산 ${eq:,.0f} | 보유 {pos_cnt}/{portfolio.MAX_SLOTS}")
                last_heartbeat_time = time.time()

            time.sleep(1)

        except KeyboardInterrupt:
            logger.info("🛑 수동 종료")
            bot.send_message("🛑 시스템이 관리자에 의해 수동으로 종료되었습니다.")
            # 종료 전 마지막 저장
            save_state(portfolio.ban_list, active_candidates)
            break
            
        except Exception as e:
            error_msg = f"⚠️ [CRITICAL ERROR] 시스템 에러 발생!\n내용: {e}\n👉 10초 후 재시도합니다."
            logger.error(error_msg)
            bot.send_message(error_msg)
            time.sleep(10)

if __name__ == "__main__":
    main()