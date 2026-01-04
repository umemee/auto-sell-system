# main.py (Final Orchestrator) - v4.0 Sniper Edition
import time
import logging
import os
import sys
import pandas as pd
from datetime import datetime

# --- Core Modules ---
from core.state_manager import StateManager, SystemState
from core.risk_manager import RiskManager
from core.signal_engine import SignalEngine
from core.action_plan import ActionPlan

# --- Infra ---
from infra.kis_auth import KisAuth
from infra.kis_api import KisApi
from infra.telegram_bot import TelegramBot
from infra.utils import is_market_open, get_next_market_open, get_us_time, get_logger
from data.market_listener import MarketListener
import config

# 로깅 설정
logger = get_logger("Main")

def save_trade_log(trade_data):
    """실전 매매 로그 저장"""
    file_path = "results/live_trade_journal.csv"
    if not os.path.exists("results"):
        os.makedirs("results")
    
    df = pd.DataFrame([trade_data])
    
    if not os.path.exists(file_path):
        df.to_csv(file_path, index=False, mode='w', encoding='utf-8-sig')
    else:
        df.to_csv(file_path, index=False, mode='a', header=False, encoding='utf-8-sig')

def generate_trade_id(symbol):
    now = datetime.now()
    return f"{now.strftime('%Y%m%d')}_{symbol}_{now.strftime('%H%M%S')}"

def main():
    print("🚀 Auto-Sell System v4.0 (ROD_B Sniper) Booting Up...")
    logger.info("✅ 시스템 초기화 시작")

    # 1. 인프라 초기화
    try:
        # 설정 체크
        if not config.Config.check_settings():
            return

        kis_auth = KisAuth()       
        api = KisApi(kis_auth)
        
        state_manager = StateManager()
        bot = TelegramBot(state_manager)
        bot.start() 
        
        bot.send_message(f"🤖 <b>System v4.0 가동 (ROD_B)</b>\n"
                         f"전략: {config.Config.STRATEGY_NAME}\n"
                         f"손절: {config.Config.STOP_LOSS_PCT*100}%\n"
                         f"익절: {config.Config.TAKE_PROFIT_PCT*100}%")
        logger.info("✅ 인프라 연결 성공")
    except Exception as e:
        logger.critical(f"❌ 인프라 초기화 실패: {e}")
        return

    # 2. 엔진 초기화
    risk_manager = RiskManager(state_manager)
    signal_engine = SignalEngine()
    market_listener = MarketListener(api)

    # 3. 변수 초기화
    last_scan_time = 0
    SCAN_INTERVAL = 600 
    RETRY_INTERVAL = 60
    
    last_heartbeat_time = time.time()
    HEARTBEAT_INTERVAL = 3600 

    active_trade = None 

    logger.info("✅ Main Loop 진입")

    try:
        while True:
            # [Safety] Kill Switch Check
            if os.path.exists("STOP.txt"):
                msg = "⛔ [Kill Switch] STOP.txt 감지됨. 종료합니다."
                logger.warning(msg)
                bot.send_message(msg)
                os.remove("STOP.txt")
                break

            current_state = state_manager.get_state()
            us_now = get_us_time()
            now_ts = time.time()

            # [Smart Time] 장 운영 시간 체크 (포지션 없을 때만)
            if current_state in [SystemState.IDLE, SystemState.SCANNING] and not active_trade:
                if not is_market_open():
                    next_open = get_next_market_open()
                    wait_seconds = (next_open - us_now).total_seconds()
                    
                    if wait_seconds > 0:
                        msg = (f"💤 <b>Market Closed</b>\n"
                               f"오픈: {next_open.strftime('%m-%d %H:%M')} (NY)")
                        bot.send_message(msg)
                        state_manager.set_state(SystemState.IDLE, "Market Closed")
                        
                        sleep_chunk = 3600
                        while wait_seconds > 0:
                            if os.path.exists("STOP.txt"): break
                            to_sleep = min(wait_seconds, sleep_chunk)
                            time.sleep(to_sleep)
                            wait_seconds -= to_sleep
                            if is_market_open(): break
                        continue

            if is_market_open() and current_state == SystemState.IDLE:
                state_manager.set_state(SystemState.SCANNING, "Market Open")
                bot.send_message("🔔 <b>Market Open!</b> 스나이핑을 시작합니다.")

            # [Heartbeat]
            if now_ts - last_heartbeat_time > HEARTBEAT_INTERVAL:
                targets = market_listener.target_symbols
                msg = (f"💓 <b>Alive</b>\n상태: {current_state.name}\n타겟: {len(targets)}개")
                bot.send_message(msg)
                last_heartbeat_time = now_ts

            # =========================================================
            # [Logic] 스캔 및 매매
            # =========================================================
            if current_state == SystemState.SCANNING:
                
                # 주기적 종목 스캔 (Market Listener)
                is_regular_scan = (now_ts - last_scan_time > SCAN_INTERVAL)
                is_retry_scan = (not market_listener.target_symbols) and (now_ts - last_scan_time > RETRY_INTERVAL)

                if last_scan_time == 0 or is_regular_scan or is_retry_scan:
                    found_symbols = market_listener.scan_for_candidates()
                    last_scan_time = now_ts
                    if found_symbols:
                        bot.send_message(f"🔎 <b>Scan Result</b>: {len(found_symbols)} candidates")

                if market_listener.target_symbols:
                    my_cash = api.get_buyable_cash()
                    
                    for symbol in market_listener.target_symbols:
                        
                        # [Critical Fix 1] One-Shot Rule: 오늘 거래한 종목은 패스
                        if state_manager.is_traded_today(symbol):
                            continue

                        # [Critical Fix 2] SMA 200 계산을 위해 300개 캔들 요청
                        candles = api.get_minute_candles(
                            config.Config.EXCHANGE_CD, 
                            symbol, 
                            limit=config.Config.CANDLE_LIMIT # 300
                        )
                        if not candles: continue
                            
                        # 엔진 분석 (SignalEngine 내부에 40% 급등 & 10분 지연 로직 포함됨)
                        action_plan = signal_engine.analyze(symbol, candles, my_cash)

                        if action_plan:
                            state_manager.set_state(SystemState.SIGNAL_LOCKED, f"Signal on {symbol}")
                            
                            if risk_manager.check_entry_permit(action_plan, my_cash):
                                
                                # 수량 확정
                                config_safe_qty = config.Config.get_order_qty(action_plan.entry_price, my_cash)
                                final_qty = min(action_plan.quantity, config_safe_qty)
                                
                                if final_qty < 1:
                                    state_manager.set_state(SystemState.SCANNING, "Low Qty")
                                    continue

                                trade_id = generate_trade_id(symbol)
                                logger.info(f"🚀 Execute Buy: {symbol} @ ${action_plan.entry_price}")

                                # 주문 실행 (ROD_B는 Limit Price = Entry Price)
                                odno = api.place_order_final(
                                    exchange=config.Config.EXCHANGE_CD,
                                    symbol=symbol,
                                    side="BUY",
                                    qty=final_qty,
                                    price=action_plan.entry_price,
                                    trade_id=trade_id
                                )
                                
                                if odno:
                                    # [Critical Fix 3] One-Shot 기록: 오늘 이 종목은 졸업
                                    state_manager.record_trade(symbol)
                                    
                                    noti_data = {
                                        "symbol": symbol, "qty": final_qty,
                                        "price": action_plan.entry_price, "order_no": odno
                                    }
                                    bot.send_rich_notification("BUY", noti_data)
                                    
                                    # Active Trade에 TP/SL 정보 정확히 저장
                                    active_trade = {
                                        "trade_id": trade_id,
                                        "symbol": symbol,
                                        "qty": final_qty,
                                        "entry_price": action_plan.entry_price,
                                        "stop_loss": action_plan.stop_loss,   # -8%
                                        "take_profit": action_plan.take_profit[0], # +10%
                                        "order_no": odno
                                    }
                                    state_manager.set_state(SystemState.IN_POSITION, f"Entry {symbol}")
                                    
                                    # One-Shot Rule에 의해 한 번 진입하면 루프 탈출 (단일 포지션 집중)
                                    break 
                                else:
                                    state_manager.set_state(SystemState.SCANNING, "Order Fail")
                            else:
                                state_manager.set_state(SystemState.SCANNING, "Risk Check Fail")

            # =========================================================
            # [Logic] 청산 감시 (ROD_B Exit)
            # =========================================================
            elif current_state == SystemState.IN_POSITION:
                if not active_trade:
                    state_manager.set_state(SystemState.SCANNING, "Lost Trade Info")
                    continue

                symbol = active_trade["symbol"]
                entry_price = active_trade["entry_price"]
                qty = active_trade["qty"]
                stop_loss = active_trade["stop_loss"]
                take_profit = active_trade["take_profit"] # [New] 익절가

                curr_price = api.get_current_price(config.Config.EXCHANGE_CD, symbol)
                
                if curr_price > 0:
                    pnl_rate = ((curr_price - entry_price) / entry_price) * 100
                    
                    exit_signal = False
                    exit_reason = ""
                    
                    # [Critical Fix 4] 정확한 TP/SL 로직
                    if curr_price >= take_profit:
                        exit_signal = True
                        exit_reason = "Take Profit (ROD_B)"
                    elif curr_price <= stop_loss:
                        exit_signal = True
                        exit_reason = "Stop Loss (ROD_B)"
                    
                    # (옵션) 3시 50분 강제 청산 로직을 추가할 수도 있음
                        
                    if exit_signal:
                        # 지정가 매도 (현재가보다 약간 유리하게 던지거나 시장가로)
                        # 여기서는 확실한 체결을 위해 시장가성 지정가(-2% range) or 시장가 사용
                        # KIS API 특성상 지정가가 안전함
                        esc_price = curr_price * 0.98 if "Stop" in exit_reason else curr_price
                        
                        odno = api.place_order_final(config.Config.EXCHANGE_CD, symbol, "SELL", qty, esc_price, active_trade["trade_id"])
                        
                        if odno:
                            noti_data = {
                                "symbol": symbol, "qty": qty,
                                "price": curr_price, "pnl": pnl_rate,
                                "order_no": odno
                            }
                            bot.send_rich_notification("SELL", noti_data)
                            bot.send_message(f"🏁 <b>{exit_reason}</b>\n{symbol} PnL: {pnl_rate:.2f}%")

                            # 로그 저장
                            trade_log = {
                                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "symbol": symbol,
                                "strategy": config.Config.STRATEGY_NAME,
                                "side": "SELL",
                                "entry": entry_price,
                                "exit": curr_price,
                                "pnl_pct": round(pnl_rate, 2),
                                "reason": exit_reason
                            }
                            save_trade_log(trade_log)
                            risk_manager.record_trade_result(pnl_rate)
                            
                            active_trade = None
                            state_manager.set_state(SystemState.COOLDOWN, exit_reason)
                        else:
                            bot.send_message(f"❌ 매도 주문 실패! {symbol} 수동 청산 요망")
                else:
                    time.sleep(1)

            # =========================================================
            # [Logic] 쿨다운
            # =========================================================
            elif current_state == SystemState.COOLDOWN:
                # 매매 종료 후 잠시 대기
                time.sleep(30)
                state_manager.set_state(SystemState.SCANNING, "Cooldown Done")

            time.sleep(1)

    except KeyboardInterrupt:
        bot.send_message("👋 시스템 종료 요청")
    except Exception as e:
        logger.critical(f"🔥 Critical Error: {e}")
        bot.send_message(f"🔥 시스템 에러: {e}")
        state_manager.trigger_kill_switch("Crash")
    finally:
        bot.stop()

if __name__ == "__main__":
    main()