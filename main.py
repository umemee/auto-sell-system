import time
import csv
import os
import sys
from datetime import datetime

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

def main():
    try:
        # 1. 인프라 초기화
        auth = KisAuth()
        kis = KisApi(auth)
        
        # [Bot] 봇 생성 (Start는 나중에)
        bot = TelegramBot()
        
        market_listener = MarketListener(kis)
        state_manager = StateManager() # One-Shot 관리자
        
        # 2. 전략 장착
        if Config.ACTIVE_STRATEGY == "ATOM_SUP_EMA200":
            strategy = AtomSupEma200()
        else:
            raise ValueError(f"Unknown Strategy: {Config.ACTIVE_STRATEGY}")
            
        init_log_file()
        logger.info(f"🔥 [Zone 1] System Ready. Strategy: {strategy.name}")

    except Exception as e:
        print(f"❌ Init Error: {e}")
        return

    # 상태 변수
    current_position = None 
    today_loss = 0.0
    
    # [NEW] 30분 생존 신고 타이머 (시작 시간으로 초기화)
    last_heartbeat_time = time.time()

    # ====================================================
    # 🤖 [UI] 봇에게 시스템 상태를 알려주는 콜백 함수 정의
    # ====================================================
    def get_status_snapshot():
        # 현재가 업데이트 (포지션 있을 때만)
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
            'targets': market_listener.get_current_targets(), # 리스너에서 가져옴
            'position': current_position, 
            'oneshot': state_manager.traded_symbols
        }

    # 봇에게 콜백 연결 및 시작
    bot.set_status_provider(get_status_snapshot)
    bot.start()
    bot.send_message(f"🔥 <b>Zone 1 실전 봇 시작</b>\n전략: {strategy.name} (Risk: 98%)\n\n✅ 30분마다 생존 신고 문자를 보냅니다.")

    while True:
        try:
            # 3. 손실 한도 체크
            if today_loss >= Config.MAX_DAILY_LOSS:
                bot.send_message("🛑 금일 손실 한도 초과. 종료합니다.")
                break
                
            # ============================================
            # [NEW] ⏰ 30분 정기 생존 신고 (Heartbeat)
            # ============================================
            if time.time() - last_heartbeat_time >= 1800: # 1800초 = 30분
                targets = market_listener.get_current_targets()
                target_str = ", ".join(targets) if targets else "없음"
                
                hb_msg = (
                    f"⏱️ <b>[30분 생존 신고]</b>\n"
                    f"시스템 정상 작동 중입니다.\n\n"
                    f"🔭 <b>현재 감시 종목:</b>\n"
                    f"👉 {target_str}\n\n"
                    f"⏰ {datetime.now().strftime('%H:%M:%S')}"
                )
                bot.send_message(hb_msg)
                last_heartbeat_time = time.time() # 타이머 리셋

            # ============================================
            # A. EXIT LOGIC (보유 중)
            # ============================================
            if current_position:
                symbol = current_position['symbol']
                df = kis.get_minute_candles("NASD", symbol)
                
                if df.empty:
                    time.sleep(1)
                    continue

                strategy.calculate_indicators(df)
                curr_price = df.iloc[-1]['close']
                
                # HWM 갱신
                if curr_price > current_position['max_price']:
                    current_position['max_price'] = curr_price
                
                # 청산 판단
                exit_signal = strategy.check_exit(
                    df, 
                    current_position['entry_price'], 
                    current_position['max_price'],
                    None
                )
                
                if exit_signal:
                    # 안전 매도 실행
                    res_odno = kis.sell_market(symbol, current_position['qty'])
                    if res_odno:
                        kis.wait_for_fill(res_odno) # 체결 대기
                        
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

            # ============================================
            # B. ENTRY LOGIC (미보유)
            # ============================================
            else:
                # 매분 실시간 스캔 (Real-time Scanning)
                targets = market_listener.scan_markets(min_change=Config.MIN_CHANGE_PCT)
                
                for symbol in targets:
                    # [One-Shot Rule] 금일 매매 이력이 있는 종목은 즉시 패스
                    if state_manager.is_traded_today(symbol):
                        continue

                    df = kis.get_minute_candles("NASD", symbol)
                    if df.empty or len(df) < 2: continue
                    
                    strategy.calculate_indicators(df)
                    
                    # 확정된 봉(iloc[:-1])으로 진입 판단
                    entry_signal = strategy.check_entry(df.iloc[:-1])
                    
                    if entry_signal:
                        # 자금 관리: 98% All-in
                        cash = kis.get_buyable_cash()
                        if cash < 10: continue 

                        buy_amt = cash * Config.ALL_IN_RATIO
                        qty = int(buy_amt / entry_signal['price'])
                        
                        if qty > 0:
                            # 1. 뜰채(지정가 주문) 투척
                            ord_no = kis.buy_limit(symbol, entry_signal['price'], qty)
                            
                            if ord_no:
                                logger.info(f"⏳ 뜰채 설치 (No: {ord_no}) - {symbol} @ ${entry_signal['price']} 대기 중...")
                                
                                # 2. 입질 대기 (60초)
                                is_fully_filled = kis.wait_for_fill(ord_no, timeout=60)
                                
                                final_qty = 0
                                
                                # A. 완전 체결
                                if is_fully_filled:
                                    final_qty = qty
                                # B. 타임아웃 -> 취소 시도 & 부분 체결 확인
                                else:
                                    logger.warning(f"⏳ 타임아웃. 주문 취소 및 체결량 확인 중... (No: {ord_no})")
                                    
                                    # 취소 재시도 로직 (3회)
                                    cancel_success = False
                                    for retry in range(3):
                                        if kis.cancel_order(ord_no, "NASD", symbol, qty):
                                            cancel_success = True
                                            break
                                        time.sleep(1)
                                    
                                    # [Kill Switch] 취소 실패 시 시스템 종료
                                    if not cancel_success:
                                        msg = f"🚨 CRITICAL: 주문 취소 실패 ({ord_no}). 봇을 긴급 정지합니다."
                                        logger.critical(msg)
                                        bot.send_message(msg)
                                        sys.exit(1) # 강제 종료
                                    
                                    # 취소 성공 -> 부분 체결량 확인
                                    final_qty = kis.get_filled_qty(ord_no)
                                
                                # 3. 결과 처리 (완전 or 부분 체결)
                                if final_qty > 0:
                                    current_position = {
                                        'symbol': symbol,
                                        'qty': final_qty, # 실제 체결된 수량 적용
                                        'entry_price': entry_signal['price'],
                                        'max_price': entry_signal['price']
                                    }
                                    state_manager.record_trade(symbol) # One-Shot 기록
                                    
                                    log_trade({
                                        "symbol": symbol, "action": "BUY", 
                                        "price": entry_signal['price'], "qty": final_qty, 
                                        "reason": entry_signal['comment']
                                    })
                                    
                                    msg = f"🎣 Entry Success {symbol} | Qty: {final_qty} (Partial: {qty != final_qty})"
                                    logger.info(msg)
                                    bot.send_message(msg)
                                    break # 보유 상태로 전환
                                    
                                else:
                                    # 완전 미체결
                                    logger.info(f"💨 미체결 종료. 뜰채 회수 완료.")
                                    continue

            time.sleep(Config.CHECK_INTERVAL_SEC)

        except KeyboardInterrupt:
            bot.send_message("👋 시스템 종료")
        except Exception as e:
            logger.error(f"Critical Error: {e}")
            bot.send_message(f"🔥 시스템 에러: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()