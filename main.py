import time
import datetime
import pytz 
from config import Config
from infra.utils import get_logger
from infra.kis_api import KisApi
from infra.kis_auth import KisAuth
from infra.telegram_bot import TelegramBot
from data.market_listener import MarketListener
from strategy import GapZoneStrategy

logger = get_logger("Main")

# [시간 설정] 미국 동부 시간(ET) 기준
ACTIVE_START_HOUR = 4
ACTIVE_END_HOUR = 16 

def is_active_market_time():
    """현재 시간이 활동 시간(Pre~Close)인지, 주말인지 확인"""
    now_et = datetime.datetime.now(pytz.timezone('US/Eastern'))
    if now_et.weekday() >= 5: return False, "주말 (Weekend)"
    current_hour = now_et.hour
    if ACTIVE_START_HOUR <= current_hour < ACTIVE_END_HOUR:
        return True, "Active Market"
    return False, "After Market / Night"

def main():
    logger.info("🚀 GapZone System v3.4 (Auto-Sell Restored) Starting...")
    
    # 전역 변수 초기화
    last_heartbeat_time = time.time()
    HEARTBEAT_INTERVAL = 30 * 60 
    current_watchlist = [] 
    was_sleeping = False   

    try:
        # 1. 인프라 초기화
        token_manager = KisAuth()
        kis = KisApi(token_manager)
        bot = TelegramBot()
        listener = MarketListener(kis)
        engine = GapZoneStrategy()     
        
        # 2. 전략 파라미터 로딩
        # [수정] 하드코딩 제거 -> Config에서 전략 이름 가져오기 #
        active_strat_name = Config.ACTIVE_STRATEGY
        strat_params = engine.strategies.get(active_strat_name, {})
        
        # 전략이 없을 경우를 대비한 안전장치
        if not strat_params:
            logger.warning(f"⚠️ 전략 '{active_strat_name}'을 찾을 수 없습니다. 기본값(NEW_PRE)을 사용합니다.")
            active_strat_name = "NEW_PRE"
            strat_params = engine.strategies.get(active_strat_name, {})

        tp_rate = strat_params.get('take_profit', 0.12)
        sl_rate = strat_params.get('stop_loss', -0.05)
        
        # 3. 부팅 직후 즉시 스캔
        logger.info("🔭 시스템 부팅 중... 초기 시장 스캔 수행...")
        initial_targets = listener.scan_markets()
        current_watchlist = initial_targets 
        
        watch_str = ", ".join(initial_targets) if initial_targets else "없음"
        
        start_msg = (
            f"⚔️ [시스템 가동 완료]\n"
            f"🧠 전략: {active_strat_name}\n"
            f"🎯 목표: TP +{tp_rate*100:.2f}% / SL {sl_rate*100:.2f}%\n"
            f"🔭 초기 감시 종목: {watch_str}\n"
            f"⏰ 활동 시간: 04:00 ~ 16:00 (ET)"
        )
        logger.info(start_msg)
        bot.send_message(start_msg)
        
    except Exception as e:
        logger.critical(f"❌ 초기화 실패: {e}")
        return

    # ---------------------------------------------------------
    # Helper Functions
    # ---------------------------------------------------------
    def send_heartbeat():
        try:
            now_str = datetime.datetime.now().strftime("%H:%M:%S")
            cash = kis.get_buyable_cash()
            balances = kis.get_balance()
            
            holdings_str = "없음"
            holding_symbols = []
            if balances:
                h_list = []
                for item in balances:
                    sym = item['symbol']
                    qty = item['qty']
                    # 수익률 계산 (API 제공 값 사용)
                    pnl = item.get('pnl_pct', 0.0)
                    h_list.append(f"{sym}({qty}주/{pnl:+.2f}%)")
                    holding_symbols.append(sym)
                holdings_str = ", ".join(h_list)
            
            real_watchlist = [s for s in current_watchlist if s not in holding_symbols]
            watch_str = ", ".join(real_watchlist) if real_watchlist else "없음"
            
            msg = (
                f"💓 [생존 신고] {now_str}\n"
                f"💰 예수금: ${cash:,.2f}\n"
                f"📦 보유: {holdings_str}\n"
                f"🔭 감시 중: {watch_str}"
            )
            bot.send_message(msg)
            logger.info(f"Heartbeat: Cash ${cash} | Watch {len(real_watchlist)}")
        except Exception as e:
            logger.error(f"Heartbeat Error: {e}")

    def get_buy_qty(price):
        try:
            cash = kis.get_buyable_cash()
            if cash < 50: return 0 
            amount = cash * 0.98
            return int(amount / price)
        except: return 0

    # ---------------------------------------------------------
    # Main Loop
    # ---------------------------------------------------------
    while True:
        try:
            # 1. 수면 모드 체크
            is_active, reason = is_active_market_time()
            if not is_active:
                if not was_sleeping:
                    bot.send_message(f"💤 [Sleep Mode] {reason}")
                    was_sleeping = True
                time.sleep(60) 
                continue
            
            if was_sleeping:
                bot.send_message("🌅 [Wake Up] 시장 감시 재개!")
                was_sleeping = False
                last_heartbeat_time = 0

            # 2. 하트비트
            if time.time() - last_heartbeat_time > HEARTBEAT_INTERVAL:
                send_heartbeat()
                last_heartbeat_time = time.time()

            # 3. [복구됨] 보유 종목 관리 및 매도(청산) 로직
            balances = kis.get_balance()
            holding_symbols = []

            if balances:
                for item in balances:
                    sym = item['symbol']
                    qty = item['qty']
                    pnl_pct = item.get('pnl_pct', 0.0) / 100.0 # API는 보통 %단위(예: 3.5)로 줌 -> 0.035로 변환 필요할 수도 있음. 
                    # *KIS API frcr_evlu_pfls_rt는 퍼센트(%) 단위입니다. (예: 12.5 -> 12.5%)
                    # 따라서 설정값 tp_rate(0.12)와 비교하려면 pnl_pct를 그대로 쓰거나 단위를 맞춰야 합니다.
                    # 여기서는 안전하게 API 값(%)을 소수점(0.12) 단위로 변환해서 비교합니다.
                    
                    current_pnl_rate = pnl_pct / 100.0 
                    holding_symbols.append(sym)
                    
                    # [매도 조건 체크]
                    sell_signal = False
                    reason = ""
                    
                    if current_pnl_rate >= tp_rate:
                        sell_signal = True
                        reason = f"TP 달성 (+{pnl_pct:.2f}%)"
                    elif current_pnl_rate <= sl_rate:
                        sell_signal = True
                        reason = f"SL 발동 ({pnl_pct:.2f}%)"
                        
                    if sell_signal:
                        msg = f"👋 [{reason}] 매도 시도: {sym} ({qty}주)"
                        logger.info(msg)
                        bot.send_message(msg)
                        
                        # 시장가 매도 (확실한 청산)
                        ord_no = kis.sell_market(sym, qty)
                        if ord_no:
                            bot.send_message(f"✅ 매도 주문 완료: {ord_no}")
                            time.sleep(5) # 체결 대기
                        else:
                            bot.send_message(f"❌ 매도 실패! 수동 확인 요망")

                # 보유 중일 때는 추가 매수 금지 (단일 종목 원칙) & 스캔 중단
                time.sleep(10)
                current_watchlist = [] 
                continue 

            # 4. 스캐닝
            scanned_targets = listener.scan_markets()
            current_watchlist = scanned_targets
            
            if not scanned_targets:
                logger.info("🔭 감시 대상 없음 (Scanning...)")
                time.sleep(60)
                continue

            # 5. 타겟 분석 및 매수
            for sym in scanned_targets:
                if sym in holding_symbols: continue
                
                df = kis.get_minute_candles("NASD", sym)
                if df.empty: continue

                # [추가] 현재가 정보를 미리 가져옵니다.
                price_info = kis.get_current_price("NASD", sym)
                
                # 정보를 같이 넘겨줍니다.
                signal = engine.get_buy_signal(df, sym, current_price_data=price_info)
                                
                if signal:
                    price = signal['price']
                    qty = get_buy_qty(price)
                    
                    if qty > 0:
                        msg = f"⚡ [{active_strat_name}] 매수 신호! {sym} @ ${price:.2f} (Qty: {qty})"
                        logger.info(msg)
                        bot.send_message(msg)
                        
                        ord_no = kis.buy_limit(sym, price, qty)
                        if ord_no:
                            bot.send_message(f"✅ 매수 주문 완료: {ord_no}")
                            time.sleep(60)
                            break 

            time.sleep(10)

        except KeyboardInterrupt:
            bot.send_message("👋 시스템 수동 종료")
            break
        except Exception as e:
            logger.error(f"Main Loop Error: {e}")
            bot.send_message(f"⚠️ 에러: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()