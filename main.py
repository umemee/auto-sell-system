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

# [시스템 상태 관리]
class SystemState:
    """전역 상태를 관리하는 클래스"""
    def __init__(self):
        self.daily_start_cash = 0.0
        self.daily_start_time = None
        self.current_watchlist = []
        self.last_heartbeat_time = time.time()
        
class RiskManager:
    """리스크 관리 클래스"""
    def __init__(self, kis, config):
        self.kis = kis
        self.config = config
        self.daily_start_cash = None
        self.daily_start_time = None
        self.reset_daily()
        
    def reset_daily(self):
        """매일 자정(ET)에 리셋"""
        try:
            self.daily_start_cash = self.kis.get_buyable_cash()
            self.daily_start_time = datetime.datetime.now(pytz.timezone('US/Eastern'))
            logger.info(f"💰 일일 시작 자금: ${self.daily_start_cash: ,.2f}")
        except Exception as e:
            logger.error(f"RiskManager 초기화 실패: {e}")
            self.daily_start_cash = 0
            
    def check_daily_loss(self):
        """일일 손실 체크 - True면 거래 가능, False면 중단"""
        try:
            current_cash = self.kis.get_buyable_cash()
            balances = self.kis.get_balance()
            
            # 현재 포지션 평가액 포함
            total_value = current_cash
            if balances:
                for item in balances:
                    qty = item['qty']
                    # 'price' 필드는 이미 (qty * 현재가) 값임
                    position_value = item.get('price', 0)
                    total_value += position_value
            
            if self.daily_start_cash == 0:
                return True, 0.0
                
            loss_pct = ((self.daily_start_cash - total_value) / self.daily_start_cash) * 100
            
            if loss_pct >= self.config.MAX_DAILY_LOSS_PCT:
                return False, loss_pct
            return True, loss_pct
            
        except Exception as e: 
            logger.error(f"일일 손실 체크 실패: {e}")
            return True, 0.0
    
    def should_reset_daily(self):
        """날짜가 바뀌었는지 체크"""
        if self.daily_start_time is None: 
            return True
        now = datetime.datetime.now(pytz.timezone('US/Eastern'))
        if now.date() > self.daily_start_time.date():
            return True
        return False

# [시간 설정] 미국 동부 시간(ET) 기준
ACTIVE_START_HOUR = Config.ACTIVE_START_HOUR
ACTIVE_END_HOUR = Config.ACTIVE_END_HOUR

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
    
    # 시스템 상태 초기화
    state = SystemState()
    HEARTBEAT_INTERVAL = Config.HEARTBEAT_INTERVAL_SEC
    was_sleeping = False  

    try:
        # 1. 인프라 초기화
        token_manager = KisAuth()
        kis = KisApi(token_manager)
        bot = TelegramBot()
        listener = MarketListener(kis)
        engine = GapZoneStrategy()     

        # 4. 리스크 관리자 초기화
        risk_manager = RiskManager(kis, Config)
        
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
        state.current_watchlist = initial_targets 
        
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
                
        # 5. Telegram Bot 양방향 연결
        bot.start()  # 명령어 수신 시작
        
        # 상태 제공 함수 연결
        def get_status_data():
            try:
                cash = kis.get_buyable_cash()
                balances = kis.get_balance()
                position = balances[0] if balances else None
                
                # 일일 손실 계산
                _, loss_pct = risk_manager.check_daily_loss()
                
                return {
                    'cash': cash,
                    'position': position,
                    'targets': state.current_watchlist,
                    'loss':  loss_pct,
                    'loss_limit': Config.MAX_DAILY_LOSS_PCT,
                    'oneshot': set()
                }
            except Exception as e:
                logger.error(f"상태 조회 실패: {e}")
                return {
                    'cash': 0, 
                    'position': None, 
                    'targets': [], 
                    'loss': 0, 
                    'loss_limit': 0, 
                    'oneshot': set()
                }
        
        bot.set_status_provider(get_status_data)
        logger.info("✅ Telegram Bot 양방향 연결 완료")
        
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
            
            real_watchlist = [s for s in state.current_watchlist if s not in holding_symbols]
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
            amount = cash * Config.ALL_IN_RATIO
            return int(amount / price)
        except: return 0

    # ---------------------------------------------------------
    # Main Loop
    # ---------------------------------------------------------
    while True:
        try:
            # 1. 수면 모드 체크
            is_active, reason = is_active_market_time()
            logger.info(f"🕐 시간 체크: {reason} | 활성화={is_active}")

            if not is_active:
                if not was_sleeping:
                    logger.warning(f"💤 Sleep 모드 진입: {reason}")  # 강조
                    bot.send_message(f"💤 [Sleep Mode] {reason}")
                    was_sleeping = True
                time.sleep(60)
                continue
            
            # 날짜 리셋 체크
            if risk_manager.should_reset_daily():
                risk_manager.reset_daily()
                bot.send_message("🌅 [일일 리셋] 손실 한도 초기화")
            
            # 일일 손실 체크
            can_trade, loss_pct = risk_manager.check_daily_loss()
            if not can_trade:
                msg = f"🚨 [거래 중단] 일일 손실 한도 도달!\n손실률: {loss_pct:.2f}% (한도: {Config.MAX_DAILY_LOSS_PCT}%)"
                logger.critical(msg)
                bot.send_message(msg)
                time.sleep(1800)  # 30분 대기
                continue
                        
            if was_sleeping:
                bot.send_message("🌅 [Wake Up] 시장 감시 재개!")
                was_sleeping = False
                state.last_heartbeat_time = 0


            # 2. 하트비트
            if time.time() - state.last_heartbeat_time > HEARTBEAT_INTERVAL: 
                send_heartbeat()
                state.last_heartbeat_time = time.time()

            # 3. [복구됨] 보유 종목 관리 및 매도(청산) 로직
            balances = kis.get_balance()
            holding_symbols = []

            if balances:
                for item in balances:
                    sym = item['symbol']
                    qty = item['qty']
                    # [버그 수정] API의 % 값을 소수점 단위로 한 번만 변환
                    raw_pnl = item.get('pnl_pct', 0.0) 
                    current_pnl_rate = raw_pnl / 100.0 
                    
                    holding_symbols.append(sym)
                    
                    sell_signal = False
                    reason = ""
                    
                    # 설정값(예: 0.07)과 변환된 수익률(예: 0.08) 비교
                    if current_pnl_rate >= tp_rate:
                        sell_signal = True
                        reason = f"TP 달성 (+{raw_pnl:.2f}%)"
                    elif current_pnl_rate <= sl_rate:
                        sell_signal = True
                        reason = f"SL 발동 ({raw_pnl:.2f}%)"
                        
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
                state.current_watchlist = [] 
                continue 

            # 4. 스캐닝
            scanned_targets = listener.scan_markets()
            state.current_watchlist = scanned_targets
            
            if not scanned_targets:
                logger.info("🔭 감시 대상 없음 (Scanning...)")
                time.sleep(60)
                continue

            # 5. 타겟 분석 및 매수
            for sym in scanned_targets: 
                # [수정] Race Condition 방지:  실시간 잔고 재확인
                current_balances = kis.get_balance()
                current_holdings = [b['symbol'] for b in current_balances] if current_balances else []
                
                if sym in current_holdings:
                    logger.warning(f"⚠️ {sym} 이미 보유 중 (스킵)")
                    continue
                
                df = kis.get_minute_candles("NASD", sym)
                if df.empty: continue

                # 현재가 정보를 미리 가져옵니다.
                price_info = kis.get_current_price("NASD", sym)
                
                # 정보를 같이 넘겨줍니다.
                signal = engine.get_buy_signal(df, sym, current_price_data=price_info)
                                
                if signal: 
                    # [수정] Price Staleness 방지: 주문 직전 가격 재확인
                    fresh_price_info = kis.get_current_price("NASD", sym)
                    fresh_price = fresh_price_info.get('last', signal['price'])
                    
                    # 가격 변동 체크
                    price_change_pct = abs(fresh_price - signal['price']) / signal['price']
                    if price_change_pct > (Config.MAX_PRICE_DEVIATION_PCT / 100):
                        logger.warning(f"⚠️ {sym} 가격 급변 ({price_change_pct*100:.2f}%) - 매수 스킵")
                        continue
                    
                    qty = get_buy_qty(fresh_price)
                    
                    if qty > 0:
                        msg = f"⚡ [{active_strat_name}] 매수 신호! {sym} @ ${fresh_price:.2f} (Qty: {qty})"
                        logger.info(msg)
                        bot.send_message(msg)
                        
                        ord_no = kis.buy_limit(sym, fresh_price, qty)
                        if ord_no:
                            bot.send_message(f"✅ 매수 주문 완료: {ord_no}")
                            time.sleep(Config.MAIN_LOOP_INTERVAL_SEC)
                            break 
            time.sleep(10)

        except KeyboardInterrupt:
            bot.send_message("👋 시스템 수동 종료")
            break
        except Exception as e:
            error_msg = str(e).lower()
            logger.error(f"Main Loop Error: {e}")
            
            # 인증 에러 감지 및 토큰 갱신 시도
            if "unauthorized" in error_msg or "token" in error_msg or "auth" in error_msg:
                logger.warning("🔑 토큰 만료 감지 - 갱신 시도...")
                try:
                    token_manager.refresh_token()
                    bot.send_message("🔑 토큰 갱신 완료")
                    time.sleep(5)
                    continue
                except Exception as refresh_error:
                    bot.send_message(f"❌ 토큰 갱신 실패: {refresh_error}\n시스템 종료")
                    break
            else:
                bot.send_message(f"⚠️ 에러: {e}")
                time.sleep(30)

if __name__ == "__main__":

    main()




