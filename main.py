# main.py
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
        
        # 기존: tp_rate(TS발동), ts_callback 등 -> 삭제
        # 변경: 고정 익절(Target Profit) 설정
        target_profit_rate = getattr(Config, 'TP_PCT', 0.10)     # [변경] 10%
        sl_rate = -abs(getattr(Config, 'SL_PCT', 0.40))          # [유지] -40%

        # 4. 초기 상태 동기화
        logger.info("📡 증권사 서버와 동기화 중...")
        portfolio.sync_with_kis()
        
        # [긴급 추가] 재시작 시 아까 밴 당한 종목들 복구
        # 시스템 재시작 후 이 줄은 나중에 지워도 됩니다.
        portfolio.ban_list.update(['nito', 'glsi', 'jem', 'RAPT', 'CCHH', 'CRVS', 'ICON', 'SHPH', 'AFJK', 'PTLE', 'SEGG', 'POLA', 'JAGX', 'LCFY', 'JFBR', 'AFJK', 'SVRE']) 
        logger.info(f"🚫 수동 밴 리스트 적용 완료: {portfolio.ban_list}")
        
        start_msg = (
            f"⚔️ [시스템 가동 v5.1 - Sniper Mode]\n"
            f"🧠 전략: {strategy.name} (MA {strategy.ma_length})\n"
            f"💰 자산: ${portfolio.total_equity:,.0f}\n"
            f"🎯 목표: 익절 +{target_profit_rate*100:.1f}% / 손절 {sl_rate*100:.1f}%\n"
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
    
    # 감시 명단을 기억할 집합(Set) 선언 (Loop 진입 전)
    active_candidates = set()

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
                active_candidates.clear() # <--- [추가] 어제의 급등주는 잊어야 함
                logger.info("✨ 금일 매매 금지 리스트 및 감시 명단 초기화 완료")
                current_date_str = new_date_str
            # ---------------------------------------------------------
            # 🕒 1. [EOS] 장 마감 강제 청산 (15:50 ET)
            # ---------------------------------------------------------
            if now_et.hour == 15 and now_et.minute >= 50:
                logger.info("🏁 [EOS] 정규장 마감 임박. 강제 청산 및 금일 매매 종료.")
                
                # 1. 보유 종목 전량 매도
                if portfolio.positions:
                    bot.send_message("🚨 [장 마감] EOS 강제 청산 실행 및 매매 종료!")
                    for ticker in list(portfolio.positions.keys()):
                        msg = order_manager.execute_sell(portfolio, ticker, "End of Session (EOS)")
                        if msg: bot.send_message(msg)
                        time.sleep(1)
                else:
                    logger.info("🏁 보유 포지션 없음. 안전하게 마감.")

                # 2. [핵심] 남은 시간 동안 매매 금지 (Sleep loop)
                # 16:00(장 마감)까지, 혹은 그 이후 애프터마켓을 건너뛰기 위해 긴 대기
                # 여기서는 간단하게 다음날 03:50분까지 자거나, 루프를 멈추는 방식을 제안합니다.
                
                bot.send_message("😴 [Sleep] 금일 매매를 종료하고 내일 프리마켓까지 대기합니다.")
                
                # 다음 날 프리마켓 시작(04:00) 직전까지 대기하는 로직이 이상적이나,
                # 단순하게는 '현재 루프 탈출' 후 10분 단위로 체크하거나, 긴 sleep을 줍니다.
                time.sleep(60 * 60 * 4) # 4시간 대기 (확실하게 애프터마켓 초반 매수 방지)
                
                # 밴 리스트 초기화는 다음 루프의 날짜 변경 로직에서 처리됨
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
                # 1. 실시간 현재가 강제 조회
                # (kis_api.get_current_price는 실시간 호가 데이터를 가져옴)
                real_time_price = kis.get_current_price(ticker)
                
                # API 에러 등으로 가격을 못 가져오면, 기존 가격 유지하고 다음 루프로
                if real_time_price is None or real_time_price <= 0:
                    continue
                
                # 2. 포트폴리오 상태 즉시 업데이트
                pos = portfolio.positions[ticker]
                pos['current_price'] = real_time_price # 가격 덮어쓰기
                
                entry_price = pos['entry_price']
                qty = pos['qty']
                
                # 수익률 재계산 (가장 최신 가격 기준)
                pnl_rate = (real_time_price - entry_price) / entry_price
                pos['pnl_pct'] = pnl_rate * 100 # 상태창 표시용 업데이트

                # ---------------------------------------------------------
                # 3. 매도 조건 판단
                # ---------------------------------------------------------
                sell_signal = False
                reason = ""
                
                # A. Target Profit (익절) - 10% 이상이면 즉시 발동
                if pnl_rate >= target_profit_rate:
                    sell_signal = True
                    reason = f"TAKE_PROFIT ({pnl_rate*100:.2f}% >= {target_profit_rate*100:.1f}%)"
                
                # B. Stop Loss (손절)
                elif pnl_rate <= sl_rate:
                    sell_signal = True
                    reason = f"STOP_LOSS ({pnl_rate*100:.2f}%)"

                # ---------------------------------------------------------
                # 4. 매도 실행
                # ---------------------------------------------------------
                if sell_signal:
                    limit_price = None
                    
                    # 익절인 경우: 현재가(real_time_price)로 지정가 주문
                    if "TAKE_PROFIT" in reason:
                        limit_price = real_time_price 
                    
                    # execute_sell 호출
                    result = order_manager.execute_sell(portfolio, ticker, reason, price=limit_price)
                    
                    if result:
                        bot.send_message(result['msg'])

            # ---------------------------------------------------------
            # 🔭 5. [Entry] 진입 로직 (Shadow Scanning 포함)
            # ---------------------------------------------------------
            # [기존 코드 삭제]
            # raw_targets = listener.scan_markets()
            # scanned_targets = [ ... ]

            # [변경 코드 시작] ==========================================
            # 1. 현재 순간의 급등주 스캔
            fresh_targets = listener.scan_markets()
            
            # 2. "한 번 해병은 영원한 해병" -> 감시 명단에 누적(Update)
            if fresh_targets:
                active_candidates.update(fresh_targets)
            
            # 3. 최종 감시 대상 선정 (누적된 active_candidates 사용)
            # 보유 중이거나, 밴 당한 종목은 제외
            scanned_targets = [
                sym for sym in list(active_candidates)
                if not portfolio.is_holding(sym) and not portfolio.is_banned(sym)
            ]
            # [변경 코드 끝] ============================================

            # 리스너에 '정제된' 감시 종목 업데이트 (상태창용)
            listener.current_watchlist = scanned_targets 

            # 감시할 종목이 없으면 대기 후 루프 처음으로
            if not scanned_targets:
                time.sleep(1)
                continue

            for sym in scanned_targets:
                # [수정] API 호출 제한 방지를 위한 0.5초 대기
                time.sleep(0.5)
                
                # -------------------------------------------------------
                # [삭제됨] 중복 체크 로직 제거
                # 위에서 이미 걸러냈으므로 여기서 다시 if portfolio... 할 필요 없음
                # -------------------------------------------------------
                
                # 1. 캔들 조회
                df = kis.get_minute_candles("NASD", sym)
                
                if df.empty: continue

                # 2. 전략 판정
                signal = strategy.check_buy_signal(df, ticker=sym)
                
                if signal:
                    signal['ticker'] = sym
                    
                    # [Core Logic] 슬롯 확인
                    if portfolio.has_open_slot():
                        # A. 자리가 있으면 -> 매수 시도
                        result = order_manager.execute_buy(portfolio, signal)
                        
                        if result and result.get('msg'):
                            # 매수 성공 시
                            bot.send_message(result['msg'])
                            if result['status'] == 'success':
                                if not portfolio.has_open_slot():
                                    break
                        else:
                            # 매수 시도했으나 거절된 경우 (자금부족 등)
                            logger.warning(f"🚌 [Missed Bus] {sym} 진입 실패(자금부족/조건미달). 금일 제외.")
                            portfolio.ban_list.add(sym) 

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
