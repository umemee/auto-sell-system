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
# [New] 상태 관리자 로드
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
        bot = TelegramBot()
        market_listener = MarketListener(kis)
        
        # [New] 상태 관리자 초기화 (금일 매매 기록 관리)
        state_manager = StateManager()
        
        # 2. 전략 장착
        if Config.ACTIVE_STRATEGY == "ATOM_SUP_EMA200":
            strategy = AtomSupEma200()
        else:
            raise ValueError(f"Unknown Strategy: {Config.ACTIVE_STRATEGY}")
            
        init_log_file()
        logger.info(f"🔥 [Zone 1] System Ready. Strategy: {strategy.name}")
        bot.send_message(f"🔥 Zone 1 실전 봇 시작. 전략: {strategy.name} (Risk: 98% All-in)")

    except Exception as e:
        print(f"❌ Init Error: {e}")
        return

    # 상태 변수
    current_position = None 
    today_loss = 0.0

    while True:
        try:
            # 3. 손실 한도 체크
            if today_loss >= Config.MAX_DAILY_LOSS:
                bot.send_message("🛑 금일 손실 한도 초과. 종료합니다.")
                break

            # ============================================
            # A. EXIT LOGIC (보유 중)
            # ============================================
            if current_position:
                symbol = current_position['symbol']
                df = kis.get_minute_candles("NASD", symbol) # [Fix] 4자리 코드 사용 권장 (혹은 _get_lookup_excd 자동 변환 의존)
                
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
                # 40% 이상 급등주 스캔 (메서드명 통일됨)
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
                            res_odno = kis.buy_limit(symbol, entry_signal['price'], qty)
                            if res_odno:
                                if kis.wait_for_fill(res_odno): # 체결 완료 시에만 포지션 잡음
                                    current_position = {
                                        'symbol': symbol,
                                        'qty': qty,
                                        'entry_price': entry_signal['price'],
                                        'max_price': entry_signal['price']
                                    }
                                    
                                    # [One-Shot Rule] 매매 기록 저장 (중복 진입 방지)
                                    state_manager.record_trade(symbol)
                                    
                                    log_trade({
                                        "symbol": symbol, "action": "BUY", 
                                        "price": entry_signal['price'], "qty": qty, 
                                        "reason": entry_signal['comment']
                                    })
                                    
                                    msg = f"🎣 Entry {symbol} at ${entry_signal['price']} | Qty: {qty}"
                                    logger.info(msg)
                                    bot.send_message(msg)
                                    break # 현재 스캔 루프 탈출 (보유 상태로 전환)

            time.sleep(Config.CHECK_INTERVAL_SEC)

        except KeyboardInterrupt:
            bot.send_message("👋 시스템 종료")
        except Exception as e:
            logger.error(f"Critical Error: {e}")
            bot.send_message(f"🔥 시스템 에러: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()