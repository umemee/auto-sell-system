# main.py (Final Orchestrator) - v3.1 Hybrid Edition
import time
import logging
import os
import sys
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
from infra.utils import is_market_open, get_next_market_open, get_us_time
from data.market_listener import MarketListener
import config

# 로깅 설정 (utils.py의 설정을 따름)
from infra.utils import get_logger
logger = get_logger("Main")

def generate_trade_id(symbol):
    now = datetime.now()
    return f"{now.strftime('%Y%m%d')}_{symbol}_{now.strftime('%H%M%S')}"

def main():
    print("🚀 Auto-Sell System v3.1 (Hybrid Edition) Booting Up...")
    logger.info("✅ 시스템 초기화 시작")

    # 1. 인프라 초기화
    try:
        kis_auth = KisAuth()       
        api = KisApi(kis_auth)
        
        state_manager = StateManager()
        
        # [V3.1] 봇에 state_manager 주입 (상태 조회 명령어용)
        bot = TelegramBot(state_manager)
        bot.start() # 봇 스레드 시작 (명령어 수신 대기)
        
        bot.send_message("🤖 <b>Auto-Sell System v3.1 가동</b>\n(Core: V3 + Safety: V2 + SmartTime: V1)")
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
    HEARTBEAT_INTERVAL = 3600 # 1시간마다 생존 신고

    active_trade = None 

    logger.info("✅ Main Loop 진입")

    try:
        while True:
            # =========================================================
            # [V2 Feature] 안전 종료 (Kill Switch File)
            # =========================================================
            if os.path.exists("STOP.txt"):
                msg = "⛔ [Kill Switch] STOP.txt 감지됨. 시스템을 안전하게 종료합니다."
                logger.warning(msg)
                bot.send_message(msg)
                os.remove("STOP.txt") # 파일 삭제 후 종료
                break

            current_state = state_manager.get_state()
            us_now = get_us_time()
            now_ts = time.time()

            # =========================================================
            # [V1 Feature] 스마트 타임 & 주말 체크
            # =========================================================
            # 포지션이 없고(IDLE/SCANNING), 장 운영 시간이 아니면 슬립 모드
            if current_state in [SystemState.IDLE, SystemState.SCANNING] and not active_trade:
                if not is_market_open():
                    next_open = get_next_market_open()
                    wait_seconds = (next_open - us_now).total_seconds()
                    
                    if wait_seconds > 0:
                        msg = (f"💤 <b>Smart Sleep Mode</b>\n"
                               f"현재: {us_now.strftime('%m-%d %H:%M')} (NY)\n"
                               f"오픈: {next_open.strftime('%m-%d %H:%M')} (NY)\n"
                               f"상태: 장 시작 전 대기합니다.")
                        
                        logger.info(f"Sleep until {next_open}")
                        bot.send_message(msg)
                        
                        # IDLE 상태 전환
                        state_manager.set_state(SystemState.IDLE, "Market Closed")
                        
                        # 긴 대기 (최대 1시간 단위로 끊어서 대기 - 봇 명령 수신 위해)
                        sleep_chunk = 3600
                        while wait_seconds > 0:
                             # 대기 중에도 STOP.txt 체크
                            if os.path.exists("STOP.txt"): break
                            
                            to_sleep = min(wait_seconds, sleep_chunk)
                            time.sleep(to_sleep)
                            wait_seconds -= to_sleep
                            
                            # 다시 시간 체크 (정확도 보정)
                            us_now = get_us_time()
                            if is_market_open(): break
                        
                        continue

            # 장 시간이면 SCANNING으로 자동 전환
            if is_market_open() and current_state == SystemState.IDLE:
                state_manager.set_state(SystemState.SCANNING, "Market Open")
                bot.send_message("🔔 <b>Market Open!</b> 감시를 시작합니다.")

            # =========================================================
            # [Phase 2] 생존 신고 (Dashboard)
            # =========================================================
            if now_ts - last_heartbeat_time > HEARTBEAT_INTERVAL:
                targets = market_listener.target_symbols
                target_str = ", ".join(targets) if targets else "없음"
                
                msg = (f"💓 <b>System Heartbeat</b>\n"
                       f"상태: {current_state.name}\n"
                       f"감시중: {len(targets)}개\n"
                       f"목록: {target_str}")
                bot.send_message(msg)
                last_heartbeat_time = now_ts

            # =========================================================
            # [Phase 3] 스캔 및 매매 로직 (V3 Core Logic 유지)
            # =========================================================
            if current_state == SystemState.SCANNING:
                
                # 스캔 주기 체크
                is_regular_scan = (now_ts - last_scan_time > SCAN_INTERVAL)
                is_retry_scan = (not market_listener.target_symbols) and (now_ts - last_scan_time > RETRY_INTERVAL)

                if last_scan_time == 0 or is_regular_scan or is_retry_scan:
                    logger.info("📡 Scanning market...")
                    found_symbols = market_listener.scan_for_candidates()
                    last_scan_time = now_ts
                    
                    # [V2 Feature] 스캔 결과 브리핑 (Top 3)
                    if found_symbols:
                        top3 = found_symbols[:3]
                        bot.send_message(f"🔎 <b>New Candidates</b>\nTop3: {', '.join(top3)}")

                if market_listener.target_symbols:
                    # 예수금 조회
                    my_cash = api.get_buyable_cash()
                    
                    market_data = market_listener.get_market_data()
                    
                    for symbol, data in market_data.items():
                        # Signal Engine 분석
                        action_plan = signal_engine.analyze(
                            symbol=symbol,
                            current_price=data.get('price'),
                            open_price=data.get('open'),
                            pm_volume=data.get('vol'),
                            available_balance=my_cash 
                        )

                        if action_plan:
                            state_manager.set_state(SystemState.SIGNAL_LOCKED, f"Signal on {symbol}")
                            
                            # Risk Manager 검증
                            if risk_manager.check_entry_permit(action_plan, my_cash):
                                
                                # [V2 Feature] config.get_order_qty 사용 (동적 수량 재계산)
                                # SignalEngine이 제안한 수량과 Config 계산 수량 중 안전한 쪽 선택
                                config_safe_qty = config.Config.get_order_qty(action_plan.entry_price, my_cash)
                                final_qty = min(action_plan.quantity, config_safe_qty)
                                
                                if final_qty < 1:
                                    logger.warning(f"수량 부족으로 진입 실패 ({symbol})")
                                    state_manager.set_state(SystemState.SCANNING, "Low Qty")
                                    continue

                                trade_id = generate_trade_id(symbol)
                                logger.info(f"[{trade_id}] 🚀 Signal Confirmed. Qty: {final_qty}")

                                # 중복 주문 방지
                                try:
                                    unfilled = api.get_unfilled_qty(config.Config.EXCHANGE_CD, symbol)
                                    if unfilled > 0:
                                        logger.warning(f"중복 방지: {symbol} 미체결 있음")
                                        continue
                                except:
                                    continue

                                # 주문 실행
                                odno = api.place_order_final(
                                    exchange=config.Config.EXCHANGE_CD,
                                    symbol=symbol,
                                    side="BUY",
                                    qty=final_qty,
                                    price=action_plan.entry_price,
                                    trade_id=trade_id
                                )
                                
                                if odno:
                                    # [V2 Feature] Rich Notification 전송
                                    noti_data = {
                                        "symbol": symbol,
                                        "qty": final_qty,
                                        "price": action_plan.entry_price,
                                        "order_no": odno
                                    }
                                    bot.send_rich_notification("BUY", noti_data)
                                    
                                    active_trade = {
                                        "trade_id": trade_id,
                                        "symbol": symbol,
                                        "qty": final_qty,
                                        "entry_price": action_plan.entry_price,
                                        "stop_loss": action_plan.stop_loss,
                                        "order_no": odno
                                    }
                                    state_manager.set_state(SystemState.IN_POSITION, f"Entry Success {trade_id}")
                                else:
                                    state_manager.set_state(SystemState.SCANNING, "Order Fail")
                            else:
                                state_manager.set_state(SystemState.SCANNING, "Risk Check Fail")

            # =========================================================
            # [Phase 4] 포지션 감시 (청산 로직)
            # =========================================================
            elif current_state == SystemState.IN_POSITION:
                if not active_trade:
                    state_manager.set_state(SystemState.SCANNING, "Trade info lost")
                    continue

                tid = active_trade.get("trade_id", "?")
                symbol = active_trade["symbol"]
                entry_price = active_trade["entry_price"]
                qty = active_trade["qty"]
                stop_loss = active_trade["stop_loss"]

                curr_price = api.get_current_price(config.Config.EXCHANGE_CD, symbol)
                
                if curr_price > 0:
                    pnl_rate = ((curr_price - entry_price) / entry_price) * 100
                    
                    # 손절 조건 (-2.0%) - RiskManager 정책 따름
                    if pnl_rate <= risk_manager.MAX_DAILY_LOSS_PCT or curr_price <= stop_loss:
                        
                        esc_price = curr_price * 0.95 # 시장가성 지정가
                        odno = api.place_order_final(config.Config.EXCHANGE_CD, symbol, "SELL", qty, esc_price, tid)
                        
                        if odno:
                            # [V2 Feature] Rich Notification (손절)
                            noti_data = {
                                "symbol": symbol,
                                "qty": qty,
                                "price": curr_price,
                                "pnl": pnl_rate,
                                "order_no": odno
                            }
                            bot.send_rich_notification("SELL", noti_data)
                            
                            # 리스크 매니저에 결과 기록
                            risk_manager.record_trade_result(pnl_rate)
                            
                            active_trade = None
                            state_manager.set_state(SystemState.COOLDOWN, "Stop Loss Triggered")
                        else:
                            bot.send_message(f"❌ [{tid}] 청산 주문 실패! 수동 확인 요망!")

                else:
                    time.sleep(1)

            # =========================================================
            # [Phase 5] 쿨다운
            # =========================================================
            elif current_state == SystemState.COOLDOWN:
                time.sleep(10) # 10초 휴식
                state_manager.set_state(SystemState.SCANNING, "Cooldown Finished")

            time.sleep(1) # Main Loop Interval

    except KeyboardInterrupt:
        bot.send_message("👋 시스템 수동 종료 (KeyboardInterrupt)")
    except Exception as e:
        logger.critical(f"🔥 Critical Error: {e}")
        bot.send_message(f"🔥 시스템 에러 발생: {e}")
        state_manager.trigger_kill_switch("System Crash")
    finally:
        bot.stop() # 봇 스레드 종료

if __name__ == "__main__":
    main()