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
    current_date_str = datetime.datetime.now(pytz.timezone('US/Eastern')).strftime("%Y-%m-%d")

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
            # 🗓️ 0. [Daily Reset] 날짜 변경 감지 및 밴 리스트 초기화
            # 최초 시행시 날짜 설정
            now_et = datetime.datetime.now(pytz.timezone('US/Eastern'))
            new_date_str = now_et.strftime("%Y-%m-%d")
            
            # 날짜가 바뀌었으면 (미국 시간 기준)
            if new_date_str != current_date_str:
                logger.info(f"📅 [New Day] 날짜 변경 감지: {current_date_str} -> {new_date_str}")
                
                # 금일 매매 금지 리스트 초기화
                portfolio.ban_list.clear()
                logger.info("✨ 금일 매매 금지 리스트(Ban List) 초기화 완료")
                
                # 날짜 업데이트
                current_date_str = new_date_str
            # 1. 장 운영 시간 체크
            is_active, market_status = is_active_market_time()
            if not is_active:
                if time.time() - last_heartbeat_time > HEARTBEAT_INTERVAL:
                    logger.info(f"💤 장 마감/대기 중 ({market_status})")
                    last_heartbeat_time = time.time()
                time.sleep(60)
                continue

            # 2. 시장 스캔 (급등주 포착)
            # market_listener.py의 scan_markets()는 리스트를 반환해야 합니다.
            detected_stocks = listener.scan_markets() 
            
            # ------------------------------------------------------
            # 3. 매수 신호 처리 (진입) - Buy Loop
            # ------------------------------------------------------
            if detected_stocks:
                # [수정 요청하신 부분] 감지된 종목을 하나씩 순회합니다.
                for sym in detected_stocks:
                    
                    # A. 이미 보유 중인지 체크 (중복 진입 방지)
                    if sym in portfolio.positions:
                        continue
                        
                    # B. 금일 매매 금지(Ban) 목록 체크
                    if sym in portfolio.ban_list:
                        continue

                    # C. 슬롯 여유 확인 (Double Engine)
                    # Config.MAX_SLOTS(2)를 사용
                    if not portfolio.has_open_slot():
                        logger.warning(f"🔒 [Slot Full] {sym} 포착했으나 슬롯 꽉 참 (Max: {Config.MAX_SLOTS})")
                        break # 슬롯이 없으면 더 볼 필요 없음

                    # D. 전략 검증 (EMA Dip & Rebound)
                    # 현재가 데이터 조회
                    df = kis.get_minute_candles(sym) # 1분봉 조회
                    if df is None or df.empty:
                        continue
                        
                    buy_signal = strategy.check_buy_signal(df)
                    
                    if buy_signal:
                        logger.info(f"⚡ [BUY SIGNAL] {sym} | 전략 조건 만족")
                        
                        # E. 주문 실행 (RealOrderManager)
                        # signal에 필요한 정보 보강
                        buy_signal['ticker'] = sym
                        buy_signal['price'] = df['close'].iloc[-1]
                        buy_signal['time'] = datetime.datetime.now()
                        
                        result_msg = order_manager.execute_buy(portfolio, buy_signal)
                        if result_msg:
                            bot.send_message(result_msg)
            
            # ------------------------------------------------------
            # 4. 보유 종목 청산 관리 (청산) - Exit Loop (새로 추가됨)
            # ------------------------------------------------------
            if portfolio.positions:
                # 딕셔너리 변경 방지를 위해 리스트로 키 복사
                for ticker in list(portfolio.positions.keys()):
                    pos = portfolio.positions[ticker]
                    
                    # A. 현재가 조회
                    current_price = kis.get_current_price(ticker)
                    if not current_price:
                        continue
                        
                    # B. 고가 갱신 (트레일링 스탑용)
                    # RealPortfolio에 update_highest_price 메서드가 있어야 함
                    portfolio.update_highest_price(ticker, current_price)
                    
                    # C. 매도 신호 확인 (Strategy에 위임)
                    highest_price = pos.get('highest_price', pos['entry_price'])
                    
                    exit_signal = strategy.check_exit_signal(
                        current_price=current_price,
                        entry_price=pos['entry_price'],
                        highest_price=highest_price
                    )
                    
                    # D. 매도 실행
                    if exit_signal:
                        logger.info(f"👋 [EXIT SIGNAL] {ticker} | {exit_signal['reason']}")
                        result_msg = order_manager.execute_sell(portfolio, ticker, exit_signal)
                        if result_msg:
                            bot.send_message(result_msg)

            # 5. 생존 신고 (Heartbeat)
            if time.time() - last_heartbeat_time > HEARTBEAT_INTERVAL:
                eq = portfolio.total_equity
                pos_cnt = len(portfolio.positions)
                bot.send_message(f"💓 [생존] 자산 ${eq:,.0f} | 보유 {pos_cnt}/{Config.MAX_SLOTS}")
                last_heartbeat_time = time.time()

            time.sleep(1) # 루프 과부하 방지 (1초 대기)

        except Exception as e:
            logger.error(f"메인 루프 에러: {e}")
            bot.send_message(f"🚨 [에러] 메인 루프 중단: {e}")
            time.sleep(10)
            # 인증 에러 시 토큰 갱신 로직은 KisApi 내부나 별도 처리가능

if __name__ == "__main__":
    main()