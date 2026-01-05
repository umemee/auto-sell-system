import time
import csv
import os
import sys
from datetime import datetime
from pytz import timezone # [New] 시간대 처리를 위해 필수

# [모듈 로드]
from infra.kis_auth import KisAuth
from infra.kis_api import KisApi
from infra.telegram_bot import TelegramBot
from infra.utils import get_logger
from data.market_listener import MarketListener
from config import Config
from core.strategies.atom_ema200 import AtomSupEma200
from core.state_manager import StateManager

logger = get_logger("Main")
LOG_FILE = "results/zone1_live_journal.csv"

# [운영 시간 설정 (ET 기준)]
MARKET_OPEN_HOUR = 4   # 04:00 ET (한국 18:00) - 프리마켓 시작
MARKET_CLOSE_HOUR = 16 # 16:00 ET (한국 06:00) - 정규장 종료 (애프터마켓은 휴식)

def init_log_file():
    if not os.path.exists("results"): os.makedirs("results")
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "symbol", "action", "price", "qty", "reason", "mfe_captured", "pnl"])

def log_trade(data):
    with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            data.get('symbol'), data.get('action'), data.get('price'),
            data.get('qty'), data.get('reason'), data.get('mfe_captured', 0), data.get('pnl', 0)
        ])

def get_market_state():
    """현재 시간이 장 운영 시간인지, 주말인지, 슬립 시간인지 판단"""
    try:
        now_et = datetime.now(timezone('US/Eastern'))
        
        # 1. 주말 체크 (토=5, 일=6)
        if now_et.weekday() >= 5:
            return "WEEKEND"
            
        # 2. 시간 체크 (04:00 ~ 16:00)
        current_hour = now_et.hour + (now_et.minute / 60.0)
        
        if MARKET_OPEN_HOUR <= current_hour < MARKET_CLOSE_HOUR:
            return "OPEN"
        else:
            return "SLEEP"
    except Exception as e:
        logger.error(f"Time Check Error: {e}")
        return "SLEEP" # 에러 나면 안전하게 슬립

def main():
    try:
        # 1. 인프라 초기화
        auth = KisAuth()
        kis = KisApi(auth)
        bot = TelegramBot()
        market_listener = MarketListener(kis)
        state_manager = StateManager()
        
        # 2. 전략 장착
        if Config.ACTIVE_STRATEGY == "ATOM_SUP_EMA200":
            strategy = AtomSupEma200()
        else:
            raise ValueError(f"Unknown Strategy: {Config.ACTIVE_STRATEGY}")
            
        init_log_file()
        logger.info(f"🔥 [Zone 1] System Initialized. Strategy: {strategy.name}")

    except Exception as e:
        print(f"❌ Init Error: {e}")
        return

    # 상태 변수
    current_position = None 
    today_loss = 0.0
    
    # 타이머 변수
    last_scan_time = 0        # 스캔 타이머 (10분)
    last_heartbeat_time = 0   # 보고 타이머 (30분)
    last_state = "INIT"       # 이전 상태 (상태 변경 감지용)

    # [UI] 봇에게 상태 제공 콜백
    def get_status_snapshot():
        curr_price = 0
        if current_position:
            try:
                price_data = kis.get_current_price("NASD", current_position['symbol'])
                if price_data:
                    curr_price = price_data['last']
                    current_position['current_price'] = curr_price
            except: pass

        return {
            'cash': kis.get_buyable_cash(),
            'loss': today_loss,
            'loss_limit': Config.MAX_DAILY_LOSS,
            'targets': market_listener.get_current_targets(),
            'position': current_position,
            'oneshot': state_manager.traded_symbols
        }

    bot.set_status_provider(get_status_snapshot)
    bot.start()
    
    # 시스템 시작 알림
    bot.send_message(f"🤖 <b>시스템 부팅 완료</b>\n전략: {strategy.name}\n현재 상태를 확인하고 모드를 전환합니다...")

    while True:
        try:
            # ============================================
            # 1. 시장 상태 확인 (Auto Sleep/Wake)
            # ============================================
            current_state = get_market_state()
            
            # 상태가 변했을 때만 알림 전송 (엣지 트리거)
            if current_state != last_state:
                if current_state == "OPEN":
                    bot.send_message("☀️ <b>장 시작 (Market Open)</b>\n감시를 시작합니다. (04:00 ET)")
                elif current_state == "SLEEP":
                    bot.send_message("🌙 <b>장 마감 (Sleep Mode)</b>\n매매를 중단하고 대기합니다. (16:00 ET)")
                elif current_state == "WEEKEND":
                    bot.send_message("🌴 <b>주말 휴장 (Weekend Mode)</b>\n월요일 18:00(KST)까지 대기합니다.")
                
                logger.info(f"State Change: {last_state} -> {current_state}")
                last_state = current_state

            # 장 운영 시간이 아니면 1분 대기 후 루프 재시작
            if current_state != "OPEN":
                time.sleep(60)
                continue

            # ============================================
            # 2. 손실 한도 체크
            # ============================================
            if today_loss >= Config.MAX_DAILY_LOSS:
                bot.send_message("🛑 금일 손실 한도 초과. 금일 매매를 종료합니다.")
                # OPEN 상태여도 손실 한도 차면 슬립처럼 대기
                time.sleep(600) 
                continue

            # ============================================
            # 3. 주기적 작업 (스캔 & 보고)
            # ============================================
            current_time = time.time()
            
            # [A] 10분 주기 스캔 (targets 갱신)
            if current_time - last_scan_time >= 600: # 600초 = 10분
                logger.info("📡 정기 스캔 수행 (10분 주기)...")
                # scan_markets가 내부적으로 current_targets를 업데이트함
                market_listener.scan_markets(min_change=Config.MIN_CHANGE_PCT)
                last_scan_time = current_time

            # [B] 30분 주기 생존 보고 (Heartbeat)
            if current_time - last_heartbeat_time >= 1800: # 1800초 = 30분
                targets = market_listener.get_current_targets()
                target_str = ", ".join(targets) if targets else "없음"
                
                excluded = list(state_manager.traded_symbols)
                excluded_str = ", ".join(excluded) if excluded else "없음"
                
                hb_msg = (
                    f"⏱️ <b>[30분 정기 보고]</b>\n"
                    f"시스템 정상 가동 중 (Zone 1)\n\n"
                    f"🔭 <b>현재 감시 대상:</b>\n👉 {target_str}\n\n"
                    f"⛔ <b>제외 대상 (One-Shot):</b>\n👉 {excluded_str}\n\n"
                    f"⏰ {datetime.now().strftime('%H:%M:%S')}"
                )
                bot.send_message(hb_msg)
                last_heartbeat_time = current_time

            # ============================================
            # 4. 실전 매매 로직 (Exit -> Entry)
            # ============================================
            
            # A. 보유 중일 때 (Exit Logic)
            if current_position:
                symbol = current_position['symbol']
                df = kis.get_minute_candles("NASD", symbol)
                
                if df.empty:
                    time.sleep(1)
                    continue

                strategy.calculate_indicators(df)
                curr_price = df.iloc[-1]['close']
                
                if curr_price > current_position['max_price']:
                    current_position['max_price'] = curr_price
                
                exit_signal = strategy.check_exit(
                    df, 
                    current_position['entry_price'], 
                    current_position['max_price'],
                    None
                )
                
                if exit_signal:
                    res_odno = kis.sell_market(symbol, current_position['qty'])
                    if res_odno:
                        kis.wait_for_fill(res_odno)
                        
                        pnl = (curr_price - current_position['entry_price']) * current_position['qty']
                        if pnl < 0: today_loss += abs(pnl)
                        
                        mfe = 0.0
                        if current_position['max_price'] > current_position['entry_price']:
                            mfe = (curr_price - current_position['entry_price']) / (current_position['max_price'] - current_position['entry_price'])

                        log_trade({
                            "symbol": symbol, "action": "SELL", "price": curr_price,
                            "qty": current_position['qty'], "reason": exit_signal['reason'],
                            "mfe_captured": round(mfe, 2), "pnl": round(pnl, 2)
                        })
                        
                        msg = f"👋 Exit {symbol} | PnL: ${pnl:.2f} | {exit_signal['reason']}"
                        logger.info(msg)
                        bot.send_message(msg)
                        current_position = None

            # B. 미보유 시 진입 (Entry Logic)
            else:
                # 10분마다 갱신된 타겟 리스트 사용
                targets = market_listener.get_current_targets()
                
                for symbol in targets:
                    if state_manager.is_traded_today(symbol):
                        continue

                    df = kis.get_minute_candles("NASD", symbol)
                    if df.empty or len(df) < 2: continue
                    
                    strategy.calculate_indicators(df)
                    entry_signal = strategy.check_entry(df.iloc[:-1])
                    
                    if entry_signal:
                        cash = kis.get_buyable_cash()
                        if cash < 10: continue 

                        buy_amt = cash * Config.ALL_IN_RATIO
                        qty = int(buy_amt / entry_signal['price'])
                        
                        if qty > 0:
                            ord_no = kis.buy_limit(symbol, entry_signal['price'], qty)
                            
                            if ord_no:
                                logger.info(f"⏳ 뜰채 설치 (No: {ord_no}) - {symbol} @ ${entry_signal['price']} 대기 중...")
                                
                                is_fully_filled = kis.wait_for_fill(ord_no, timeout=60)
                                final_qty = 0
                                
                                if is_fully_filled:
                                    final_qty = qty
                                else:
                                    logger.warning(f"⏳ 타임아웃. 주문 취소 및 체결량 확인 중... (No: {ord_no})")
                                    # 취소 및 부분 체결 확인 로직 (이전과 동일)
                                    cancel_success = False
                                    for retry in range(3):
                                        if kis.cancel_order(ord_no, "NASD", symbol, qty):
                                            cancel_success = True
                                            break
                                        time.sleep(1)
                                    
                                    if not cancel_success:
                                        msg = f"🚨 CRITICAL: 주문 취소 실패 ({ord_no}). 봇을 긴급 정지합니다."
                                        logger.critical(msg)
                                        bot.send_message(msg)
                                        sys.exit(1)
                                    
                                    final_qty = kis.get_filled_qty(ord_no)
                                
                                if final_qty > 0:
                                    current_position = {
                                        'symbol': symbol,
                                        'qty': final_qty,
                                        'entry_price': entry_signal['price'],
                                        'max_price': entry_signal['price']
                                    }
                                    state_manager.record_trade(symbol)
                                    
                                    log_trade({
                                        "symbol": symbol, "action": "BUY", 
                                        "price": entry_signal['price'], "qty": final_qty, 
                                        "reason": entry_signal['comment']
                                    })
                                    
                                    msg = f"🎣 Entry Success {symbol} | Qty: {final_qty}"
                                    logger.info(msg)
                                    bot.send_message(msg)
                                    break 
                                else:
                                    logger.info(f"💨 미체결 종료. 뜰채 회수 완료.")
                                    continue

            # 기본 루프 딜레이 (1분 간격으로 로직 수행)
            time.sleep(Config.CHECK_INTERVAL_SEC)

        except KeyboardInterrupt:
            bot.send_message("👋 시스템 종료")
        except Exception as e:
            logger.error(f"Critical Error: {e}")
            bot.send_message(f"🔥 시스템 에러: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()