import time
import datetime
import pytz 
from config import Config
from infra.utils import get_logger
from infra.kis_api import KisApi
from infra.kis_auth import KisAuth
from infra.telegram_bot import TelegramBot
from infra.real_portfolio import RealPortfolio      # [NEW]
from infra.real_order_manager import RealOrderManager # [NEW]

from data.market_listener import MarketListener
from strategy import get_strategy

logger = get_logger("Main")

# [시간 설정] 미국 동부 시간(ET) 기준
ACTIVE_START_HOUR = Config.ACTIVE_START_HOUR
ACTIVE_END_HOUR = Config.ACTIVE_END_HOUR

def is_active_market_time():
    """현재 시간이 활동 시간(Pre~Close)인지 확인"""
    now_et = datetime.datetime.now(pytz.timezone('US/Eastern'))
    if now_et.weekday() >= 5: return False, "주말 (Weekend)"
    current_hour = now_et.hour
    if ACTIVE_START_HOUR <= current_hour < ACTIVE_END_HOUR:
        return True, "Active Market"
    return False, "After Market / Night"

def main():
    logger.info("🚀 GapZone System v4.0 (Double Engine Architect) Starting...")
    
    # [시스템 상태 변수]
    last_heartbeat_time = time.time()
    HEARTBEAT_INTERVAL = getattr(Config, 'HEARTBEAT_INTERVAL_SEC', 1800)
    was_sleeping = False
    current_watchlist = []

    try:
        # 1. 인프라 초기화
        token_manager = KisAuth()
        kis = KisApi(token_manager)
        bot = TelegramBot()
        listener = MarketListener(kis)
        
        # 2. [핵심] 뇌(Portfolio)와 손(OrderManager) 장착
        portfolio = RealPortfolio(kis)
        order_manager = RealOrderManager(kis)
        
        # 3. 전략 로딩
        active_strategy = get_strategy()
        
        # 전략 파라미터 (SL/TP)
        tp_rate = getattr(active_strategy, 'tp_pct', 0.10) 
        sl_pct_val = getattr(active_strategy, 'sl_pct', 0.05)
        sl_rate = -abs(sl_pct_val) 

        # 4. 초기 상태 동기화
        logger.info("📡 증권사 서버와 동기화 중...")
        portfolio.sync_with_kis()
        
        start_msg = (
            f"⚔️ [시스템 가동 v4.0]\n"
            f"🧠 전략: {active_strategy.name}\n"
            f"💰 자산: ${portfolio.total_equity:,.0f} (Cash: ${portfolio.balance:,.0f})\n"
            f"🎯 목표: TP +{tp_rate*100:.1f}% / SL {sl_rate*100:.1f}%\n"
            f"🎰 슬롯: {len(portfolio.positions)} / {portfolio.MAX_SLOTS}"
        )
        bot.send_message(start_msg)
        
        # 5. Telegram Bot 상태 제공 함수 (Portfolio 연결)
        def get_status_data():
            # 봇이 물어볼 때마다 최신 상태 리턴
            return {
                'cash': portfolio.balance,
                'total_equity': portfolio.total_equity,
                'positions': portfolio.positions, # 딕셔너리 통째로 전달
                'targets': current_watchlist,
                'loss': 0.0, # (RiskManager 로직이 필요하면 추가)
                'loss_limit': Config.MAX_DAILY_LOSS_PCT
            }
        
        # 봇의 _cmd_status 함수도 이에 맞게 수정 필요 (하단 설명 참조)
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
            # 1. 시간 체크
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
                portfolio.sync_with_kis() # 자고 일어났으니 계좌 확인

            # 2. [SYNC] 현실 동기화 (가장 중요)
            # 매 루프마다 내 장부와 증권사 장부를 맞춤
            portfolio.sync_with_kis()

            # 3. [EXIT] 청산 로직 (보유 종목 순회)
            # 딕셔너리 변경 에러 방지를 위해 list(keys) 사용
            for ticker in list(portfolio.positions.keys()):
                pos = portfolio.positions[ticker]
                
                # 현재가 및 수익률 계산
                current_price = pos['current_price']
                entry_price = pos['entry_price']
                pnl_rate = pos['pnl_pct'] / 100.0
                
                # -------------------------------------------------------
                # [Logic] Trailing Stop & Hard Stop Loss
                # -------------------------------------------------------
                sell_signal = False
                reason = ""
                
                # 1. 고가 갱신 (High Water Mark) 트래킹
                # 포지션 딕셔너리에 'highest_price'가 없으면 초기화
                if 'highest_price' not in pos:
                    pos['highest_price'] = current_price
                
                # 고가 갱신
                if current_price > pos['highest_price']:
                    pos['highest_price'] = current_price
                    # (선택) 고가 갱신 로그가 너무 많으면 주석 처리
                    # logger.debug(f"📈 [{ticker}] 고점 갱신: ${pos['highest_price']:.2f}")

                # 2. 트레일링 스탑 계산
                # Config에서 설정 로드
                ts_trigger = Config.TP_PCT       # 예: 0.06 (6%)
                ts_callback = getattr(Config, 'TS_CALLBACK', 0.01) # 예: 0.01 (1%)
                
                # 최고 수익률 계산
                max_pnl_rate = (pos['highest_price'] - entry_price) / entry_price
                
                # A. 트레일링 스탑 발동 조건 충족? (수익이 Trigger 이상 났었는가?)
                if max_pnl_rate >= ts_trigger:
                    # 매도 기준가 계산 (최고가 대비 Callback 만큼 하락한 가격)
                    trail_stop_price = pos['highest_price'] * (1 - ts_callback)
                    
                    if current_price <= trail_stop_price:
                        sell_signal = True
                        reason = f"Trailing Stop (High: ${pos['highest_price']:.2f} -> Now: ${current_price:.2f})"
                
                # B. 하드 손절 (Hard Stop Loss)
                # Config.SL_PCT (예: 0.45)
                elif pnl_rate <= -Config.SL_PCT:
                    sell_signal = True
                    reason = f"Stop Loss ({pnl_rate*100:.2f}%)"

                # 3. 매도 실행
                if sell_signal:
                    order_manager.execute_sell(portfolio, ticker, reason)
                    bot.send_message(f"👋 [{reason}] 매도 실행: {ticker}")


            # 4. [ENTRY] 진입 로직
            # 슬롯이 꽉 찼으면 스캔조차 하지 않음 (API 절약 & 뇌동매매 방지)
            if not portfolio.has_open_slot():
                # logger.debug("🔒 슬롯 Full - 스캔 건너뜀")
                time.sleep(10)
                continue

            # 슬롯 남음 -> 스캔 시작
            scanned_targets = listener.scan_markets()
            current_watchlist = scanned_targets
            
            if not scanned_targets:
                time.sleep(10) # 감시 대상 없으면 대기
                continue

            for sym in scanned_targets:
                # 이미 보유중이면 패스
                if portfolio.is_holding(sym): continue
                
                # 전략 검증을 위한 캔들 조회
                df = kis.get_minute_candles("NASD", sym)
                if df.empty: continue

                # 전략 판정
                signal = active_strategy.check_buy_signal(df)
                
                if signal:
                    signal['ticker'] = sym
                    
                    # [Double Engine] OrderManager에게 매수 위임
                    # 자금 계산, 호가 계산, 로컬 업데이트 등은 매니저가 알아서 함
                    ord_no = order_manager.execute_buy(portfolio, signal)
                    
                    if ord_no:
                        msg = f"⚡ [{active_strategy.name}] 매수 체결! {sym}\n주문번호: {ord_no}"
                        bot.send_message(msg)
                        
                        # 체결 후 슬롯이 다 찼는지 확인해보고 루프 탈출
                        if not portfolio.has_open_slot():
                            break 
            
            # 5. 생존 신고
            if time.time() - last_heartbeat_time > HEARTBEAT_INTERVAL:
                # 간단한 요약본 전송
                eq = portfolio.total_equity
                pos_cnt = len(portfolio.positions)
                bot.send_message(f"💓 [생존] 자산 ${eq:,.0f} | 보유 {pos_cnt}종목")
                last_heartbeat_time = time.time()

            time.sleep(5) # 루프 딜레이

        except KeyboardInterrupt:
            logger.info("🛑 수동 종료")
            break
        except Exception as e:
            logger.error(f"⚠️ Main Loop Error: {e}")
            time.sleep(30)
            # 인증 에러 시 토큰 갱신 로직은 KisApi 내부나 별도 처리가능

if __name__ == "__main__":
    main()