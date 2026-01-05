import time
import csv
import os
import sys
from datetime import datetime

# [기존 모듈 활용]
from infra.kis_api import KisApi
from infra.telegram_bot import TelegramBot
from infra.utils import get_logger
from data.market_listener import MarketListener # 기존 스캐너 활용
from config import Config

# [신규 전략 모듈]
from core.strategies.atom_ema200 import AtomSupEma200

# 로깅 설정
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
    # 1. 인프라 초기화
    try:
        # Config에서 설정 로드 (기존 .env 로직 유지)
        kis = KisApi(Config.APP_KEY, Config.APP_SECRET, Config.CANO, Config.URL_BASE)
        bot = TelegramBot()
        market_listener = MarketListener(kis) # 기존 스캐너 객체 생성
        
        # 전략 초기화 (레고 블록 조립)
        if Config.ACTIVE_STRATEGY == "ATOM_SUP_EMA200":
            strategy = AtomSupEma200()
        else:
            raise ValueError(f"Unknown Strategy: {Config.ACTIVE_STRATEGY}")
            
        init_log_file()
        logger.info(f"🔥 [Zone 1] System Ready. Strategy: {strategy.name}")
        bot.send_message(f"🔥 Zone 1 실전 봇 시작. 전략: {strategy.name} (Risk: All-in 98%)")

    except Exception as e:
        print(f"❌ Init Error: {e}")
        return

    # 상태 변수
    current_position = None # {symbol, qty, entry_price, max_price}
    today_loss = 0.0

    while True:
        try:
            # 2. 일일 손실 한도 체크
            if today_loss >= Config.MAX_DAILY_LOSS:
                logger.warning("🛑 Max Daily Loss Reached.")
                bot.send_message("🛑 금일 최대 손실 도달. 봇 종료.")
                break

            # ============================================
            # A. EXIT LOGIC (보유 중일 때)
            # ============================================
            if current_position:
                symbol = current_position['symbol']
                # 차트 데이터 조회 (기존 API 활용)
                df = kis.get_minute_candle(symbol) # or get_minute_chart depending on your API method name
                
                if df is None or df.empty:
                    time.sleep(1)
                    continue

                # 지표 및 신호 계산
                strategy.calculate_indicators(df)
                curr_price = df.iloc[-1]['close']
                
                # HWM 갱신
                if curr_price > current_position['max_price']:
                    current_position['max_price'] = curr_price
                
                # 전략에게 청산 여부 물어보기
                exit_signal = strategy.check_exit(
                    df, 
                    current_position['entry_price'], 
                    current_position['max_price'],
                    None
                )
                
                if exit_signal:
                    # 매도 실행
                    res = kis.sell_market(symbol, current_position['qty'])
                    if res:
                        pnl = (curr_price - current_position['entry_price']) * current_position['qty']
                        if pnl < 0: today_loss += abs(pnl)
                        
                        mfe = 0.0
                        if current_position['max_price'] > current_position['entry_price']:
                            mfe = (curr_price - current_position['entry_price']) / (current_position['max_price'] - current_position['entry_price'])

                        log_data = {
                            "symbol": symbol, "action": "SELL", "price": curr_price,
                            "qty": current_position['qty'], "reason": exit_signal['reason'],
                            "mfe_captured": round(mfe, 2), "pnl": round(pnl, 2)
                        }
                        log_trade(log_data)
                        
                        msg = f"👋 Exit {symbol} | PnL: ${pnl:.2f} | {exit_signal['reason']}"
                        logger.info(msg)
                        bot.send_message(msg)
                        current_position = None

            # ============================================
            # B. ENTRY LOGIC (포지션 없을 때)
            # ============================================
            else:
                # 1. 급등주 스캔 (기존 market_listener 사용)
                # target_stocks는 ['AAPL', 'TSLA'...] 형태의 리스트
                target_stocks = market_listener.get_target_symbols(min_change=Config.MIN_CHANGE_PCT)
                
                for symbol in target_stocks:
                    df = kis.get_minute_candle(symbol)
                    if df is None or df.empty: continue
                    
                    strategy.calculate_indicators(df)
                    entry_signal = strategy.check_entry(df.iloc[:-1])
                    
                    if entry_signal:
                        # 자금 관리: All-in 98%
                        balance = kis.get_balance() # 기존 API 메서드 확인 필요
                        cash = float(balance.get('dnca_tot_amt', 0)) 
                        
                        if cash < 10: continue 

                        buy_amt = cash * Config.ALL_IN_RATIO
                        qty = int(buy_amt / entry_signal['price'])
                        
                        if qty > 0:
                            # 매수 실행
                            res = kis.buy_limit(symbol, entry_signal['price'], qty)
                            if res:
                                current_position = {
                                    'symbol': symbol,
                                    'qty': qty,
                                    'entry_price': entry_signal['price'],
                                    'max_price': entry_signal['price']
                                }
                                log_data = {"symbol": symbol, "action": "BUY", "price": entry_signal['price'], "qty": qty, "reason": entry_signal['comment']}
                                log_trade(log_data)
                                
                                msg = f"🎣 Entry {symbol} at ${entry_signal['price']} | Qty: {qty}"
                                logger.info(msg)
                                bot.send_message(msg)
                                break # One-Shot Rule

            time.sleep(Config.CHECK_INTERVAL_SEC)

        except KeyboardInterrupt:
            bot.send_message("👋 사용자 요청으로 시스템 종료")
            break

        except Exception as e:
            logger.error(f"Critical Error: {e}")
            bot.send_message(f"🔥 시스템 에러: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()