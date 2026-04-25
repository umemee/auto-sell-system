# main.py
import time
import datetime
import pytz 
import json 
import os   
import threading
import random 
from config import Config
from infra.utils import get_logger
from infra.kis_api import KisApi
from infra.kis_auth import KisAuth
from infra.telegram_bot import TelegramBot
from infra.real_portfolio import RealPortfolio
from infra.real_order_manager import RealOrderManager
from infra.live_candle_exporter import LiveCandleExporter
from data.market_listener import MarketListener
from strategy import get_strategy

logger = get_logger("Main")
STATE_FILE = "system_state.json"

# =========================================================
# 💾 [상태 저장/로드] 시스템 재부팅 대비
# =========================================================
def save_state(ban_list, active_candidates):
    """
    [설명] 밴 리스트와 감시 중인 종목(발견 시간 포함)을 파일로 저장합니다.
    """
    try:
        # active_candidates가 dict라면 그대로, set/list라면 dict로 변환하여 저장
        candidates_data = {}
        if isinstance(active_candidates, dict):
            candidates_data = active_candidates
        else:
            # 혹시 모를 호환성 대비 (현재 시간으로 채움)
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            candidates_data = {sym: now_str for sym in active_candidates}

        state = {
            "ban_list": list(ban_list),
            "active_candidates": candidates_data, # 시간 정보가 포함된 딕셔너리 저장
            "date": datetime.datetime.now().strftime("%Y-%m-%d")
        }
        
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=4) # 보기 좋게 indent 추가
            
    except Exception as e:
        logger.error(f"⚠️ 상태 저장 실패: {e}")

def load_state():
    """[설명] 저장된 상태 파일이 있다면 불러옵니다 (재부팅 시 유용)."""
    if not os.path.exists(STATE_FILE):
        return set(), {} # 빈 딕셔너리 반환
    
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
            
        # 날짜 변경 체크
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        if state.get("date") != today:
            logger.info("📅 날짜 변경으로 저장된 상태를 초기화합니다.")
            return set(), {} # 빈 딕셔너리 반환
            
        loaded_ban = set(state.get("ban_list", []))
        raw_candidates = state.get("active_candidates", {})
        
        # [CRITICAL FIX] 어떤 형태(list, set, dict)든 무조건 dict로 변환
        loaded_candidates = {}
        
        if isinstance(raw_candidates, dict):
            loaded_candidates = raw_candidates
        elif isinstance(raw_candidates, (list, set)): # 리스트나 셋이면 변환
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            loaded_candidates = {sym: now_str for sym in raw_candidates}
        else:
            loaded_candidates = {} # 알 수 없는 형식이면 초기화
            
        return loaded_ban, loaded_candidates
    
    except Exception as e:
        logger.error(f"⚠️ 상태 로드 실패: {e}")
        return set(), {}

# =========================================================
# 🕒 [시간 체크] 한국 시간 vs 미국 시간
# =========================================================
ACTIVE_START_HOUR = getattr(Config, 'ACTIVE_START_HOUR', 4) 
ACTIVE_END_HOUR = getattr(Config, 'ACTIVE_END_HOUR', 20)    

def is_active_market_time():
    """
    [설명] 현재 미국 시간이 매매 가능한 시간인지 확인합니다.
    """
    tz_et = pytz.timezone('US/Eastern')
    now_et = datetime.datetime.now(tz_et)
    
    tz_kst = pytz.timezone('Asia/Seoul')
    now_kst = datetime.datetime.now(tz_kst)

    # 주말 체크
    if now_et.weekday() >= 5: 
        return False, f"주말 (Weekend) - KST: {now_kst.strftime('%H:%M')}"

    # 휴장일 체크 (2026년 기준)
    holidays = [
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", 
        "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07", 
        "2026-11-26", "2026-12-25"
    ]
    if now_et.strftime("%Y-%m-%d") in holidays:
        return False, "미국 증시 휴장일 (Holiday)"

    current_hour = now_et.hour
    if ACTIVE_START_HOUR <= current_hour < ACTIVE_END_HOUR:
        return True, f"Active Market (NY: {now_et.strftime('%H:%M')} | KR: {now_kst.strftime('%H:%M')})"
    
    return False, f"After Market / Night (NY: {now_et.strftime('%H:%M')} | KR: {now_kst.strftime('%H:%M')})"

# =========================================================
# 🚀 [메인 시스템]
# =========================================================
def main():
    logger.info("🚀 GapZone System v5.3 (Final Edition) Starting...")
    
    tz_kst = pytz.timezone('Asia/Seoul')
    tz_et = pytz.timezone('US/Eastern')
    now_kst_start = datetime.datetime.now(tz_kst)
    now_et_start = datetime.datetime.now(tz_et)
    
    logger.info(f"⏰ [Time Check] Korea: {now_kst_start.strftime('%Y-%m-%d %H:%M:%S')} | NY: {now_et_start.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"⚙️ [Config] 활동 시간: NY {ACTIVE_START_HOUR}:00 ~ {ACTIVE_END_HOUR}:00")

    last_heartbeat_time = time.time()
    HEARTBEAT_INTERVAL = getattr(Config, 'HEARTBEAT_INTERVAL_SEC', 1800)
    was_sleeping = False
    
    # [수정] 중복 실행 방지를 위한 변수 추가
    last_processed_minute = None
    eod_processed = False  # 👈 [추가] 장 마감 처리 완료 여부 플래그
    current_date_str = now_et_start.strftime("%Y-%m-%d")

    try:
        # 1. 인프라 초기화
        token_manager = KisAuth()
        kis = KisApi(token_manager)
        bot = TelegramBot()
        listener = MarketListener(kis)
        candle_exporter = LiveCandleExporter(kis, bot, base_dir=os.getcwd())
        
        # 2. 포트폴리오 및 주문 관리자
        portfolio = RealPortfolio(kis)
        order_manager = RealOrderManager(kis)
        strategy = get_strategy() 
        
        target_profit_rate = getattr(Config, 'TP_PCT', 0.10)
        sl_rate = -abs(getattr(Config, 'SL_PCT', 0.40))

        # 3. 서버 동기화 및 상태 복구
        logger.info("📡 증권사 서버와 동기화 중...")
        portfolio.sync_with_kis()
        
        loaded_ban, loaded_candidates = load_state()
        portfolio.ban_list.update(loaded_ban)
        
        # [안전장치] 혹시라도 set으로 왔다면 다시 dict로 변환
        if isinstance(loaded_candidates, (set, list)):
             active_candidates = {sym: datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") for sym in loaded_candidates}
        else:
             active_candidates = loaded_candidates

        for sym in active_candidates:
            candle_exporter.register_candidate(sym)
        
        logger.info(f"💾 [Memory] 복구 완료 | 🚫Ban: {len(portfolio.ban_list)}개, 👁️Watch: {len(active_candidates)}개")
        
        start_msg = (
            f"⚔️ [시스템 가동 v5.3]\n"
            f"⏰ 시간: KR {now_kst_start.strftime('%H:%M')} / NY {now_et_start.strftime('%H:%M')}\n"
            f"💰 자산: ${portfolio.total_equity:,.0f}\n"
            f"🎰 슬롯: {len(portfolio.positions)} / {portfolio.MAX_SLOTS}"
        )
        bot.send_message(start_msg)
        
        # 상태 조회 함수 (Telegram 연동)
        def get_status_data():
            return {
                'cash': portfolio.balance,
                'total_equity': portfolio.total_equity,
                'positions': portfolio.positions,
                'targets': getattr(listener, 'current_watchlist', []),
                'ban_list': list(portfolio.ban_list),
                'loss': 0.0,
                'loss_limit': getattr(Config, 'MAX_DAILY_LOSS_PCT', 0.0)
            }
        bot.set_status_provider(get_status_data)

        def run_live_candle_export(export_date=None, reason="manual"):
            try:
                result = candle_exporter.export_zip_and_send(export_date)
                manifest_rows = result.get("manifest_rows", [])
                saved_count = sum(1 for row in manifest_rows if row.get("status") == "saved")
                zip_path = result.get("zip_path", "")
                telegram_sent = result.get("telegram_sent", False)

                if zip_path:
                    delivery = "Telegram sent" if telegram_sent else "Local only"
                    logger.info(f"?? [Live Export] {reason} | files={saved_count} | zip={zip_path} | {delivery}")
                    bot.send_message(
                        f"?? [Live Candle Export]\nReason: {reason}\nFiles: {saved_count}\nZip: {zip_path}\nDelivery: {delivery}"
                    )
                else:
                    logger.warning(f"?? [Live Export] {reason} | no files exported")

                return result
            except Exception as export_error:
                logger.error(f"? [Live Export] {reason} failed: {export_error}")
                return {"date": export_date or current_date_str, "files": [], "zip_path": "", "telegram_sent": False, "manifest_rows": []}
        
        # 텔레그램 봇 스레드 실행
        def run_bot_thread():
            bot.start()
            
        t = threading.Thread(target=run_bot_thread)
        t.daemon = True 
        t.start()
        logger.info("🤖 텔레그램 봇 시작됨")

    except Exception as e:
        logger.critical(f"❌ 초기화 실패: {e}")
        return

    # =========================================================
    # 🧠 [메모리 캐싱 엔진] 800봉 데이터 임시 저장소
    # =========================================================
    candle_cache = {}

    # ---------------------------------------------------------
    # [메인 루프] 무한 반복 (Final Optimized Version)
    # ---------------------------------------------------------
    while True:
        try:
            # 미국 현지 시간 기준
            now = datetime.datetime.now(pytz.timezone('America/New_York'))
            current_minute_str = now.strftime("%H:%M")

            # =========================================================
            # 🚀 [초고속 매도 전용 차선] 보유 종목 실시간 1초 감시 (트레일링 스탑용)
            # =========================================================
            # 신규 매수를 위한 분봉 완성 대기(55초 수면)와 무관하게, 보유 종목은 매 초마다 
            # 가장 가벼운 현재가 API 1번만 호출하여 손절선을 터치하는 즉시 탈출합니다.
            if portfolio.positions:
                for ticker in list(portfolio.positions.keys()):
                    real_time_price = kis.get_current_price(ticker, exchange="NAS")
                    
                    if real_time_price and real_time_price > 0:
                        pos = portfolio.positions[ticker]
                        exit_signal = strategy.check_exit(
                            ticker=ticker, position=pos, 
                            current_price=real_time_price, now_time=now
                        )
                        
                        if exit_signal:
                            reason = exit_signal['reason']
                            if reason != 'TAKE_PROFIT': # 익절은 이미 지정가 주문 대기 중이므로 무시
                                result = order_manager.execute_sell(portfolio, ticker, reason, price=real_time_price)
                                if result:
                                    bot.send_message(result['msg'])
                                    save_state(portfolio.ban_list, active_candidates)
                    
                    # API 초당 2건 제한 준수 (종목당 0.5초 대기)
                    time.sleep(0.5)

            # =========================================================
            # 🕒 [Time Sync] 캔들 완성형 (00초~05초 진입) - 신규 매수 전용
            # =========================================================
            # [핵심 수정] 0초~5초 사이(매분 시작)에만 로직 실행 (캔들 마감 확인용)
            if now.second > 5:
                # CPU 낭비 방지를 위해 적당히 쉽니다 (0.5초)
                time.sleep(0.5)
                continue
            
            # [핵심 수정] 이번 분에 이미 실행했다면 건너뜀 (중복 실행 방지)
            if last_processed_minute == current_minute_str:
                time.sleep(0.5)
                continue
                
            # --- 여기서부터는 매 분의 00초~05초 사이에 "딱 한 번"만 실행됩니다 ---
            last_processed_minute = current_minute_str
            
            # =========================================================
            # 💤 [Sleep Mode] 활동 시간 체크 (위치 이동: 주말 오작동 방지)
            # =========================================================
            # [수정] EOD 체크보다 먼저 수행하여 주말에 강제 청산 로직이 도는 것을 막습니다.
            is_active, reason = is_active_market_time()
            
            if not is_active:
                if not was_sleeping:
                    logger.warning(f"💤 Sleep Mode: {reason}")
                    bot.send_message(f"💤 [대기] {reason}")
                    was_sleeping = True
                    save_state(portfolio.ban_list, active_candidates) # 자기 전 상태 저장
                
                # 활동 시간이 아니면 1분 통째로 대기
                time.sleep(30)
                continue
            
            # [기상] 잠에서 깨어난 경우
            if was_sleeping:
                bot.send_message(f"🌅 [기상] 시장 감시 시작 ({reason})")
                was_sleeping = False
                portfolio.sync_with_kis() # 자고 일어나면 잔고 동기화

            # ---------------------------------------------------------
            # 🛑 [EOD] 장 마감 강제 청산 (안전장치 강화판)
            # ---------------------------------------------------------
            cutoff_time_str = getattr(Config, 'TIME_HARD_CUTOFF', "15:55")
            cutoff_h, cutoff_m = map(int, cutoff_time_str.split(':'))
            
            # 현재 시각이 설정된 컷오프 시간 '이후'인지 확인 (== 대신 >= 사용)
            is_after_cutoff = (now.hour > cutoff_h) or (now.hour == cutoff_h and now.minute >= cutoff_m)
            
            if is_after_cutoff and not eod_processed:
                logger.warning(f"⏰ [장 마감] 강제 청산 실행 (Current: {now.strftime('%H:%M')} >= Cutoff: {cutoff_time_str})")
                bot.send_message(f"🚨 [장 마감] 강제 청산 실행")
                
                # [수정] positions 딕셔너리 직접 확인
                if portfolio.positions:
                    for ticker in list(portfolio.positions.keys()):
                        # 강제 청산 시에도 '시장가'로 확실하게 탈출
                        order_manager.execute_sell(portfolio, ticker, "FORCE_EOD_EXIT", price=0)
                        time.sleep(0.2) # 주문 간격
                
                # 상태 저장 후 루프 종료 (다음 날 재실행 필요)
                save_state(portfolio.ban_list, active_candidates)
                run_live_candle_export(current_date_str, reason="eod")
                logger.info("👋 [System] 장 마감으로 시스템을 종료합니다.")
                
                eod_processed = True # 오늘 처리가 끝났음을 표시
                time.sleep(300) 
                continue
            
            # 날짜가 바뀌거나 장 시간이 지나지 않았으면 플래그 초기화
            if not is_after_cutoff:
                eod_processed = False

            # =========================================================
            # 💓 [Heartbeat] 생존 신고 (상세 정보 추가)
            # =========================================================
            if time.time() - last_heartbeat_time > HEARTBEAT_INTERVAL:
                eq = portfolio.total_equity
                pos_cnt = len(portfolio.positions)
                cur_k = datetime.datetime.now(tz_kst).strftime("%H:%M")
                cur_n = datetime.datetime.now(tz_et).strftime("%H:%M")
                
                # [NEW] 감시 및 밴 리스트 현황 파악
                watching_list = list(active_candidates)
                banned_list = list(portfolio.ban_list)
                
                # 메시지가 너무 길어지는 것 방지
                watch_str = ", ".join(watching_list[:5]) + ("..." if len(watching_list) > 5 else "")
                ban_str = ", ".join(banned_list[:5]) + ("..." if len(banned_list) > 5 else "")
                
                msg = (
                    f"💓 [생존] KR {cur_k} / NY {cur_n}\n"
                    f"💰 자산 ${eq:,.0f} | 보유 {pos_cnt}개\n"
                    f"👁️ 감시({len(watching_list)}): {watch_str}\n"
                    f"🚫 제외({len(banned_list)}): {ban_str}"
                )
                
                bot.send_message(msg)
                last_heartbeat_time = time.time()

            # =========================================================
            # 📅 [Daily Reset] 날짜 변경 체크
            # =========================================================
            new_date_str = now.strftime("%Y-%m-%d")
            if new_date_str != current_date_str:
                logger.info(f"📅 [New Day] {current_date_str} -> {new_date_str}")
                portfolio.ban_list.clear()
                active_candidates.clear()
                candle_cache.clear() # 👈 [신규] 다음 날을 위해 캐시 비우기
                candle_exporter.reset_session()
                save_state(portfolio.ban_list, active_candidates)
                logger.info("✨ 데이터 초기화 완료")
                current_date_str = new_date_str

            # =========================================================
            # 🧠 [Logic] 매매 로직 시작 (매 분 1회 실행)
            # =========================================================
            
            # 1. 동기화 전, 현재 보유 종목 명단 기억
            prev_holdings = set(portfolio.positions.keys())
            
            # 2. 증권사 서버와 싱크 (여기서 익절된 종목은 positions에서 사라짐)
            portfolio.sync_with_kis()
            
            # 3. 동기화 후, 명단 확인
            current_holdings = set(portfolio.positions.keys())
            
            # 4. [핵심] 사라진 종목 찾기 (내가 판 게 아닌데 사라졌으면 -> 익절 체결임)
            sold_tickers = prev_holdings - current_holdings
            
            for ticker in sold_tickers:
                # 이미 밴 리스트에 있다면(손절/타임컷 등) 중복 알림 방지
                if ticker in portfolio.ban_list:
                    continue
                    
                # 익절 알림 전송
                logger.info(f"🎉 [익절 감지] {ticker} 목표가 도달 확인!")
                msg = (
                    f"🎉 <b>[익절 체결 확인]</b>\n"
                    f"📦 종목: {ticker}\n"
                    f"💰 결과: 목표가(+10%) 달성 추정\n"
                    f"✅ 잔고에서 자동으로 청산되었습니다."
                )
                bot.send_message(msg)
                
                # 익절한 종목도 오늘 재진입 금지 (Ban)
                portfolio.ban_list.add(ticker)
                
                # [Fix] 이미 졸업한 종목이니 감시 목록에서도 삭제 (로그 정리)
                if ticker in active_candidates:
                    del active_candidates[ticker]
                    
                save_state(portfolio.ban_list, active_candidates)

            # ---------------------------------------------------------
            # B. [매도] 보유 종목 관리 (Check Exit)# (기존 B. 매도 관리 블록은 최상단 초고속 차선으로 이동되었으므로 이 자리는 완벽히 비워둡니다)
            # ---------------------------------------------------------
            #for ticker in list(portfolio.positions.keys()):
                
                # [수정] 단순 현재가 ❌ -> 분봉 데이터 ✅
                #df = kis.get_minute_candles("NAS", ticker, limit=60)

                #if df.empty or len(df) < 1: 
                    #continue
                
                # [전략] 현재가(Tick)보다는 '방금 확정된 종가' 혹은 '현재 시가'를 기준으로 판단
                #real_time_price = df.iloc[-1]['close'] # 현재 진행중인 봉의 현재가
                
                #pos = portfolio.positions[ticker]
                #entry_price = pos['entry_price']
                #entry_time = pos.get('entry_time')

                # 전략에 매도 문의
                #exit_signal = strategy.check_exit(
                    #ticker=ticker,
                    #position=pos,
                    #current_price=real_time_price, 
                    #now_time=datetime.datetime.now(pytz.timezone('US/Eastern'))
                #)
                
                #if exit_signal:
                    #reason = exit_signal['reason']
                    
                    # 🛑 [핵심 수정] 익절(TAKE_PROFIT)은 이미 진입 시점에 지정가 주문을 걸어두었으므로 무시
                    #if reason == 'TAKE_PROFIT':
                        #continue
                        
                    # 🚨 손절(STOP_LOSS) 또는 타임컷(TIME_CUT)일 때만 비상 탈출
                    # real_order_manager가 기존 익절 대기 주문을 알아서 취소하고 95% 시장가로 던짐
                    # [중요] price=real_time_price 필수 (0원이면 주문 거부됨)
                    #result = order_manager.execute_sell(portfolio, ticker, reason, price=real_time_price)
                    #if result:
                        #bot.send_message(result['msg'])
                        #save_state(portfolio.ban_list, active_candidates)
            
            # ---------------------------------------------------------
            # C. [스캔] 신규 급등주 포착
            # ---------------------------------------------------------
            fresh_targets = listener.scan_markets(
                ban_list=portfolio.ban_list,
                active_candidates=active_candidates
            )
            
            if fresh_targets:
                for sym in fresh_targets:
                    candle_exporter.register_candidate(sym, exchange=listener.get_candidate_exchange(sym))
                    if sym not in active_candidates:
                        # 현재 시간을 문자열로 저장 (JSON 저장 호환성 위함)
                        active_candidates[sym] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                save_state(portfolio.ban_list, active_candidates)
            # ---------------------------------------------------------
            # D. [매수] 진입 타점 확인 (핵심 수정: 히스토리 로딩)
            # ---------------------------------------------------------
            buy_candidates = [
                sym for sym in list(active_candidates)
                if not portfolio.is_holding(sym) and not portfolio.is_banned(sym)
            ]

            # [Random Shuffle] 좀비 리스트 방지
            random.shuffle(buy_candidates)
            
            # API 제한 고려 상위 15개만 체크
            targets_to_check = buy_candidates[:15]
            listener.current_watchlist = targets_to_check 

            for sym in targets_to_check:
                # -----------------------------------------------------
                # 🕒 [Time Cut] 60분 경과 시 감시 해제 (좀비 방지)
                # -----------------------------------------------------
                #try:
                    #found_time_str = active_candidates.get(sym)
                    #if found_time_str:
                        # 문자열 -> datetime 변환
                        #found_time = datetime.datetime.strptime(found_time_str, "%Y-%m-%d %H:%M:%S")
                        #elapsed_minutes = (datetime.datetime.now() - found_time).total_seconds() / 60
                        
                        #if elapsed_minutes > 120: # 120분 초과
                            #logger.info(f"🗑️ [Timeout] {sym} {int(elapsed_minutes)}분 경과 -> 감시 해제")
                            #if sym in active_candidates:
                                #del active_candidates[sym]
                            #continue # 다음 종목으로 넘어감
                #except Exception:
                    #pass # 시간 포맷 에러 시엔 일단 패스

                try:
                    # =========================================================
                    # 🚀 [메가 패치] 메모리 캐싱 + 거래소 자동 탐색 엔진
                    # =========================================================
                    import pandas as pd
                    df = None
                    selected_exchange = None
                    
                    if sym not in candle_cache:
                        # [CASE A: 처음 보는 종목] 800봉 전체 다운로드 및 거래소 탐색 (약 9초 소요)
                        for exch in ["NAS", "NYS", "AMS"]:
                            temp_df = kis.get_minute_candles(exch, sym, limit=1200)
                            if not temp_df.empty and len(temp_df) >= 26:
                                df = temp_df
                                selected_exchange = exch
                                # 성공한 거래소와 데이터를 메모리에 캐싱
                                candle_cache[sym] = {'df': df, 'exch': exch}
                                break
                    else:
                        # [CASE B: 아는 종목] 해당 거래소에서 최신 120봉만 초고속 다운로드 (약 0.6초)
                        cached_data = candle_cache[sym]
                        old_df = cached_data['df']
                        exch = cached_data['exch']
                        selected_exchange = exch
                        
                        new_df = kis.get_minute_candles(exch, sym, limit=120)
                        
                        if not new_df.empty:
                            # 파이썬 메모리에서 0.01초 만에 위아래로 병합
                            combined_df = pd.concat([old_df, new_df])
                            combined_df = combined_df.drop_duplicates(subset=['date', 'time'], keep='last')
                            combined_df = combined_df.sort_values(['date', 'time']).reset_index(drop=True)
                            
                            # 최신 1200개만 유지
                            if len(combined_df) > 1200:
                                combined_df = combined_df.iloc[-1200:].reset_index(drop=True)
                                
                            candle_cache[sym]['df'] = combined_df
                            df = combined_df
                        else:
                            df = old_df # 통신 지연 시 기존 데이터 안전하게 재활용

                    if df is None or df.empty or len(df) < 26:
                        strategy._log_rejection(sym, "데이터 부족 (NAS/NYS/AMS 전체 탐색 실패)")
                        # 탐색 실패 시 캐시가 꼬이는 것 방지
                        candle_cache.pop(sym, None)
                        continue

                    candle_exporter.update_runtime_candles(sym, df, exchange=selected_exchange)

                    # =========================================================
                    # 🧠 [Strategy] 전략 엔진 호출
                    # =========================================================
                    signal = strategy.check_entry(sym, df)

                    if signal:
                        # [CASE 1] 매수 신호 (BUY)
                        if signal['type'] == 'BUY':
                            
                            # -----------------------------------------------------
                            # 🚌 [Missed Bus] 자리 없으면 -> 영구 제외 (Ban)
                            # -----------------------------------------------------
                            if not portfolio.has_open_slot():
                                logger.warning(f"🚌 [Missed Bus] {sym} 진입 신호 왔으나 자리 없음 -> 영구 제외")
                                portfolio.ban_list.add(sym)      
                                if sym in active_candidates:
                                    del active_candidates[sym]
                                candle_cache.pop(sym, None) # 👈 신규 추가
                                save_state(portfolio.ban_list, active_candidates)
                                continue
                            
                            # [Double Check] 호가 확인
                            ask, bid, ask_vol, bid_vol = kis.get_market_spread(sym)
                            
                            if ask > 0 and bid > 0:
                                spread = (ask - bid) / ask * 100
                                if spread > 3.0:
                                    logger.warning(f"⚠️ [Spread] {sym}: 괴리율 과다 ({spread:.2f}%). 진입 보류.")
                                    continue
                            
                            signal['price'] = ask if ask > 0 else signal['price']
                            signal['ticker'] = sym

                            # =========================================================
                            # ⚡ [Execution] 주문 집행
                            # =========================================================
                            if portfolio.has_open_slot():
                                result = order_manager.execute_buy(portfolio, signal)
                                
                                if result:
                                    if result.get('msg'):
                                        bot.send_message(result['msg'])
                                    
                                    if result['status'] == 'success':
                                        candle_cache.pop(sym, None)
                                        save_state(portfolio.ban_list, active_candidates)
                                        
                                        # ==========================================
                                        # 💡 [핵심 수정] 실제 체결가 확인 후 익절 주문
                                        # ==========================================
                                        
                                        # 1. 증권사 서버에 체결 내역이 반영될 때까지 1.5초 대기
                                        time.sleep(1.5) 
                                        
                                        # 2. 잔고를 동기화하여 '진짜 체결 평단가'를 가져옴
                                        portfolio.sync_with_kis() 
                                        
                                        try:
                                            # 3. 동기화된 포트폴리오에서 실제 평단가 추출
                                            actual_pos = portfolio.get_position(sym)
                                            if actual_pos and actual_pos.get('entry_price', 0) > 0:
                                                buy_price = actual_pos['entry_price']
                                            else:
                                                # 혹시 동기화가 지연되면 기존 방식 사용 (백업)
                                                buy_price = result.get('avg_price', signal['price']) 
                                            
                                            if buy_price > 0:
                                                # 4. '진짜 평단가' 기반으로 7% 익절가 계산
                                                target_profit_pct = getattr(Config, 'TARGET_PROFIT_PCT', 0.07)
                                                target_price = buy_price * (1.0 + target_profit_pct)
                                                target_price = round(target_price, 2)
                                                
                                                qty = result.get('qty', 0)
                                                
                                                if qty > 0:
                                                    logger.info(f"⚡ [Pre-Order] {sym} 실제 평단가(${buy_price}) 기반 익절 주문 전송: ${target_price}")
                                                    kis.send_order(sym, "SELL", qty, target_price, "00")
                                                    bot.send_message(f"🔒 [잠금] {sym} 익절 주문 완료 (평단가: ${buy_price:.3f} -> 목표가: ${target_price})")
                                        except Exception as e:
                                            logger.error(f"❌ 익절 주문 중 에러: {e}")

                                        if not portfolio.has_open_slot():
                                            break
                                    else:
                                        logger.warning(f"🚌 [실패] {sym} 매수 실패. 금일 제외.")
                                        portfolio.ban_list.add(sym)
                                        candle_cache.pop(sym, None) # 👈 신규 추가 (실패하면 더 이상 분봉 감시 안함)
                                        save_state(portfolio.ban_list, active_candidates)

                        # [CASE 2] 추세 붕괴 (DROP) - 👈 [신규] 좀비 종목 제거 로직
                        elif signal['type'] == 'DROP':
                            logger.info(f"🗑️ [DROP] {sym} 추세 붕괴 확인 -> 감시 해제")
                            try:
                                del active_candidates[sym]
                            except KeyError:
                                pass
                            candle_cache.pop(sym, None) # 👈 신규 추가 (추세 붕괴하면 더 이상 분봉 감시 안함)
                            save_state(portfolio.ban_list, active_candidates)

                    # [Rate Limit] API 호출 간격 조절 (초당 2건 제한 준수)
                    time.sleep(0.55)

                except Exception as e:
                    logger.error(f"❌ 매수 로직 에러({sym}): {e}")
                    bot.send_message(f"⚠️ [System Error] 매수 로직 중 오류 발생\n종목: {sym}\n내용: {str(e)}")
                    continue
            
            # =========================================================
            # 💰 [Sync] 매도 후 잔고 최신화
            # =========================================================
            if not portfolio.positions and portfolio.balance < 10:
                logger.info("🔄 [Sync] 매도 후 잔고 재동기화 수행...")
                portfolio.sync_balance() 

            # ---------------------------------------------------------
            # 루프 종료 후 대기
            # ---------------------------------------------------------
            time.sleep(0.1)

        except KeyboardInterrupt:
            logger.info("🛑 관리자에 의한 수동 종료")
            bot.send_message("🛑 시스템을 종료합니다.")
            save_state(portfolio.ban_list, active_candidates)
            run_live_candle_export(current_date_str, reason="manual_shutdown")
            break
            
        except Exception as e:
            error_msg = f"⚠️ [ERROR] 시스템 오류: {e}\n👉 10초 후 재시도..."
            logger.error(error_msg)
            time.sleep(10)

if __name__ == "__main__":

    main()


