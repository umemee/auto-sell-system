import time
import datetime
import pytz 
from config import Config
from infra.utils import get_logger
from infra.kis_api import KisApi
from infra.kis_auth import KisAuth
from infra.telegram_bot import TelegramBot
from infra.real_portfolio import RealPortfolio
from infra.real_order_manager import RealOrderManager
from data.market_listener import MarketListener
from strategy import get_strategy

logger = get_logger("Main")

# [시간 설정] 미국 동부 시간(ET) 기준
ACTIVE_START_HOUR = getattr(Config, 'ACTIVE_START_HOUR', 4) # 프리마켓 시작
ACTIVE_END_HOUR = getattr(Config, 'ACTIVE_END_HOUR', 20)    # 애프터마켓 종료

def is_active_market_time():
    """현재 시간이 활동 시간(Pre~Close)인지 확인 (휴장일 로직 추가)"""
    now_et = datetime.datetime.now(pytz.timezone('US/Eastern'))
    
    # 1. 주말 체크
    if now_et.weekday() >= 5: return False, "주말 (Weekend)"

    # 2. [추가] 2026년 미국 주식 시장 휴장일 (주요 날짜)
    # 매년 업데이트가 필요합니다.
    holidays = [
        "2026-01-01", # New Year's Day
        "2026-01-19", # Martin Luther King, Jr. Day
        "2026-02-16", # Washington's Birthday
        "2026-04-03", # Good Friday
        "2026-05-25", # Memorial Day
        "2026-06-19", # Juneteenth
        "2026-07-03", # Independence Day (Observed)
        "2026-09-07", # Labor Day
        "2026-11-26", # Thanksgiving Day
        "2026-12-25", # Christmas Day
    ]
    
    if now_et.strftime("%Y-%m-%d") in holidays:
        return False, "미국 증시 휴장일 (Holiday)"

    current_hour = now_et.hour
    
    # 04:00 ~ 20:00 (미국 현지 시간 기준 전체 장 운영 시간)
    if ACTIVE_START_HOUR <= current_hour < ACTIVE_END_HOUR:
        return True, "Active Market"
    return False, "After Market / Night"

def main():
    logger.info("🚀 GapZone System v5.0 (Final Stability) Starting...")
    
    # [시스템 상태 변수]
    last_heartbeat_time = time.time()
    HEARTBEAT_INTERVAL = getattr(Config, 'HEARTBEAT_INTERVAL_SEC', 1800)
    was_sleeping = False
    
    # 일일 리셋을 위한 날짜 추적
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
        
        # 3. 전략 로딩 (변수명 'strategy'로 통일)
        strategy = get_strategy() 
        
        # 전략 파라미터 로드
        tp_rate = getattr(Config, 'TP_PCT', 0.06)        # 익절/TS발동 (기본 6%)
        ts_callback = getattr(Config, 'TS_CALLBACK', 0.01) # 고점대비 하락 (1%)
        sl_rate = -abs(getattr(Config, 'SL_PCT', 0.45))  # 손절 (기본 -45%)

        # 4. 초기 상태 동기화
        logger.info("📡 증권사 서버와 동기화 중...")
        portfolio.sync_with_kis()
        
        # [긴급 추가] 재시작 시 아까 밴 당한 종목들 복구
        # 시스템 재시작 후 이 줄은 나중에 지워도 됩니다.
        portfolio.ban_list.update(['IVF', 'TWG', 'BTTC', 'RAPT', 'CCHH', 'CRVS', 'ICON', 'SHPH', 'AFJK', 'SVRE']) 
        logger.info(f"🚫 수동 밴 리스트 적용 완료: {portfolio.ban_list}")
        
        start_msg = (
            f"⚔️ [시스템 가동 v5.0]\n"
            f"🧠 전략: {strategy.name}\n"
            f"💰 자산: ${portfolio.total_equity:,.0f} (Cash: ${portfolio.balance:,.0f})\n"
            f"🎯 목표: TS +{tp_rate*100:.1f}%(CallBack {ts_callback*100:.1f}%) / SL {sl_rate*100:.1f}%\n"
            f"🎰 슬롯: {len(portfolio.positions)} / {portfolio.MAX_SLOTS}"
        )
        bot.send_message(start_msg)
        
        # 5. Telegram Bot 상태 제공 함수
        def get_status_data():
            return {
                'cash': portfolio.balance,
                'total_equity': portfolio.total_equity,
                'positions': portfolio.positions,
                'targets': getattr(listener, 'current_watchlist', []), # 리스너에 변수 없으면 빈 리스트
                'ban_list': list(portfolio.ban_list), # [추가] 밴 리스트를 봇에게 전달
                'loss': 0.0,
                'loss_limit': getattr(Config, 'MAX_DAILY_LOSS_PCT', 0.0)
            }
        
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
            now_et = datetime.datetime.now(pytz.timezone('US/Eastern'))
            
            # ---------------------------------------------------------
            # 🗓️ 0. [Daily Reset] 날짜 변경 시 밴 리스트 초기화
            # ---------------------------------------------------------
            new_date_str = now_et.strftime("%Y-%m-%d")
            if new_date_str != current_date_str:
                logger.info(f"📅 [New Day] 날짜 변경: {current_date_str} -> {new_date_str}")
                portfolio.ban_list.clear()
                logger.info("✨ 금일 매매 금지 리스트(Ban List) 초기화 완료")
                current_date_str = new_date_str

            # ---------------------------------------------------------
            # 🕒 1. [EOS] 장 마감 강제 청산 (15:50 ET)
            # ---------------------------------------------------------
            if now_et.hour == 15 and now_et.minute >= 50:
                if portfolio.positions:
                    bot.send_message("🚨 [장 마감 임박] EOS 강제 청산 실행!")
                    for ticker in list(portfolio.positions.keys()):
                        msg = order_manager.execute_sell(portfolio, ticker, "End of Session (EOS)")
                        if msg: bot.send_message(msg)
                        time.sleep(1)
                time.sleep(60) # 청산 후 대기
                continue

            # ---------------------------------------------------------
            # 💤 2. [Active Time] 장 운영 시간 체크
            # ---------------------------------------------------------
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
                portfolio.sync_with_kis()

            # ---------------------------------------------------------
            # 📡 3. [Sync] 잔고 동기화 (가장 중요)
            # ---------------------------------------------------------
            portfolio.sync_with_kis()

            # ---------------------------------------------------------
            # 📉 4. [Exit] 청산 로직 (Trailing Stop & Stop Loss)
            # ---------------------------------------------------------
            for ticker in list(portfolio.positions.keys()):
                pos = portfolio.positions[ticker]
                
                current_price = pos['current_price']
                entry_price = pos['entry_price']
                pnl_rate = pos['pnl_pct'] / 100.0
                
                # 고가 갱신 (Portfolio가 이미 update_highest_price를 가지고 있다면 호출, 아니면 직접 처리)
                # 여기서는 직접 로직을 수행하여 안전성 확보
                if 'highest_price' not in pos:
                    pos['highest_price'] = max(current_price, entry_price)
                
                if current_price > pos['highest_price']:
                    pos['highest_price'] = current_price

                # 조건 검사
                sell_signal = False
                reason = ""
                
                # A. Trailing Stop
                # 최고 수익률 계산
                max_pnl_rate = (pos['highest_price'] - entry_price) / entry_price
                
                if max_pnl_rate >= tp_rate: # 목표 수익(예: 6%) 도달 했었음
                    # 고점 대비 하락폭 계산
                    trail_stop_price = pos['highest_price'] * (1 - ts_callback)
                    if current_price <= trail_stop_price:
                        sell_signal = True
                        reason = f"Trailing Stop (High ${pos['highest_price']:.2f} -> Now ${current_price:.2f})"
                
                # B. Stop Loss (Hard)
                elif pnl_rate <= sl_rate:
                    sell_signal = True
                    reason = f"Stop Loss ({pnl_rate*100:.2f}%)"

                # 매도 실행
                if sell_signal:
                    result = order_manager.execute_sell(portfolio, ticker, reason)
                    
                    if result:
                        # 성공이든 실패든 메시지 전송
                        bot.send_message(result['msg'])

            # ---------------------------------------------------------
            # 🔭 5. [Entry] 진입 로직 (Shadow Scanning 포함)
            # ---------------------------------------------------------
            scanned_targets = listener.scan_markets()
            
            # 리스너에 감시 종목 업데이트 (상태창용)
            listener.current_watchlist = scanned_targets 

            if not scanned_targets:
                time.sleep(1)
                continue

            for sym in scanned_targets:
                # [수정] API 호출 제한 방지를 위한 0.5초 대기 (가장 쉬운 해결책)
                time.sleep(0.5)
                
                # 1. 이미 보유중이거나, 밴(금일 매매 금지) 리스트면 패스
                if portfolio.is_holding(sym): continue
                if portfolio.is_banned(sym): continue 
                
                # 2. 캔들 조회
                # [수정 완료] 파라미터 개수 오류 해결 ("NASD" 추가)
                df = kis.get_minute_candles("NASD", sym)
                
                if df.empty: continue

                # 3. 전략 판정
                signal = strategy.check_buy_signal(df, ticker=symbol)
                
                if signal:
                    signal['ticker'] = sym
                    
                    # [Core Logic] 슬롯 확인
                    if portfolio.has_open_slot():
                        # A. 자리가 있으면 -> 매수
                        result = order_manager.execute_buy(portfolio, signal)
                        
                        if result and result.get('msg'):
                            bot.send_message(result['msg'])
                            
                            # 성공했다면 슬롯 체크 후 탈출
                            if result['status'] == 'success':
                                if not portfolio.has_open_slot():
                                    break
                    else:
                        # B. 자리가 없으면 -> 그림자 밴(Shadow Ban)
                        logger.warning(f"🔒 [Shadow Scan] {sym} 기회 포착했으나 슬롯 Full. 금일 제외.")
                        portfolio.ban_list.add(sym)

            # 6. 생존 신고
            if time.time() - last_heartbeat_time > HEARTBEAT_INTERVAL:
                eq = portfolio.total_equity
                pos_cnt = len(portfolio.positions)
                bot.send_message(f"💓 [생존] 자산 ${eq:,.0f} | 보유 {pos_cnt}/{portfolio.MAX_SLOTS}")
                last_heartbeat_time = time.time()

            # 루프 속도 조절 (1초)
            time.sleep(1)

        except KeyboardInterrupt:
            logger.info("🛑 수동 종료")
            bot.send_message("🛑 시스템이 관리자에 의해 수동으로 종료되었습니다.")
            break
            
        except Exception as e:
            # [수정] 에러 발생 시 텔레그램으로 즉시 알림 (가장 중요한 수정)
            error_msg = f"⚠️ [CRITICAL ERROR] 시스템 에러 발생!\n내용: {e}\n👉 10초 후 재시도합니다."
            logger.error(error_msg)
            bot.send_message(error_msg) # 봇에게 메시지 전송 요청
            
            time.sleep(10) # 에러 발생 시 잠시 대기 후 재시도

if __name__ == "__main__":

    main()
