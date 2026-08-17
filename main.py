# main.py
import time
import datetime
import pytz 
import json 
import os   
import threading
import random 
from pathlib import Path
from config import Config
from infra.utils import get_logger
from infra.kis_api import KisApi
from infra.kis_auth import KisAuth
from infra.telegram_bot import TelegramBot
from infra.real_portfolio import RealPortfolio
from infra.real_order_manager import RealOrderManager
from infra.live_candle_exporter import LiveCandleExporter
from infra.risk_filter import TradeRiskFilter  # 👈 [추가] 3중 리스크 필터
from data.market_listener import MarketListener
from strategy import get_strategy

logger = get_logger("Main")
STATE_FILE = "system_state.json"

def save_state(ban_list, active_candidates, loss_blacklist=None):
    """
    [설명] 밴 리스트, 감시 중인 종목, 손절 블랙리스트를 파일로 저장합니다.
    """
    try:
        candidates_data = {}
        if isinstance(active_candidates, dict):
            candidates_data = active_candidates
        else:
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            candidates_data = {sym: now_str for sym in active_candidates}

        state = {
            "ban_list": list(ban_list),
            "loss_blacklist": list(loss_blacklist) if loss_blacklist is not None else [],
            "active_candidates": candidates_data,
            "date": datetime.datetime.now().strftime("%Y-%m-%d")
        }
        
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)
            
    except Exception as e:
        logger.error(f"⚠️ 상태 저장 실패: {e}")

def load_state():
    """[설명] 저장된 상태 파일이 있다면 불러옵니다."""
    if not os.path.exists(STATE_FILE):
        return set(), {}, set()
    
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
            
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        if state.get("date") != today:
            logger.info("📅 날짜 변경으로 저장된 상태를 초기화합니다.")
            return set(), {}, set()
            
        loaded_ban = set(state.get("ban_list", []))
        loaded_loss = set(state.get("loss_blacklist", []))
        raw_candidates = state.get("active_candidates", {})
        
        loaded_candidates = {}
        if isinstance(raw_candidates, dict):
            loaded_candidates = raw_candidates
        elif isinstance(raw_candidates, (list, set)):
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            loaded_candidates = {sym: now_str for sym in raw_candidates}
        else:
            loaded_candidates = {}
            
        return loaded_ban, loaded_candidates, loaded_loss
    
    except Exception as e:
        logger.error(f"⚠️ 상태 로드 실패: {e}")
        return set(), {}, set()

ACTIVE_START_HOUR = getattr(Config, 'ACTIVE_START_HOUR', 4) 
ACTIVE_END_HOUR = getattr(Config, 'ACTIVE_END_HOUR', 20)    

def is_active_market_time():
    tz_et = pytz.timezone('US/Eastern')
    now_et = datetime.datetime.now(tz_et)
    
    tz_kst = pytz.timezone('Asia/Seoul')
    now_kst = datetime.datetime.now(tz_kst)

    if now_et.weekday() >= 5: 
        return False, f"주말 (Weekend) - KST: {now_kst.strftime('%H:%M')}"

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

def main():
    logger.info("🚀 GapZone System v5.4 (3-Tier Risk Filter Edition) Starting...")
    
    tz_kst = pytz.timezone('Asia/Seoul')
    tz_et = pytz.timezone('US/Eastern')
    now_kst_start = datetime.datetime.now(tz_kst)
    now_et_start = datetime.datetime.now(tz_et)
    
    logger.info(f"⏰ [Time Check] Korea: {now_kst_start.strftime('%Y-%m-%d %H:%M:%S')} | NY: {now_et_start.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"⚙️ [Config] 활동 시간: NY {ACTIVE_START_HOUR}:00 ~ {ACTIVE_END_HOUR}:00")

    last_heartbeat_time = time.time()
    HEARTBEAT_INTERVAL = getattr(Config, 'HEARTBEAT_INTERVAL_SEC', 40000)
    was_sleeping = False
    
    last_processed_minute = None
    eod_processed = False  
    current_date_str = now_et_start.strftime("%Y-%m-%d")

    try:
        # 1. 인프라 초기화
        token_manager = KisAuth()
        kis = KisApi(token_manager)
        bot = TelegramBot()
        listener = MarketListener(kis)
        candle_exporter = LiveCandleExporter(kis, bot, base_dir=os.getcwd())
        
        # 🛡️ [추가] 3중 리스크 필터 초기화
        risk_filter = TradeRiskFilter()

        # 2. 포트폴리오 및 주문 관리자
        portfolio = RealPortfolio(kis)
        order_manager = RealOrderManager(kis)
        strategy = get_strategy() 

        # 3. 서버 동기화 및 상태 복구
        logger.info("📡 증권사 서버와 동기화 중...")
        portfolio.sync_with_kis()
        
        loaded_ban, loaded_candidates, loaded_loss = load_state()
        portfolio.ban_list.update(loaded_ban)
        risk_filter.loss_blacklist.update(loaded_loss)
        
        if isinstance(loaded_candidates, (set, list)):
             active_candidates = {sym: datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") for sym in loaded_candidates}
        else:
             active_candidates = loaded_candidates

        for sym in active_candidates:
            candle_exporter.register_candidate(sym)
        
        logger.info(f"💾 [Memory] 복구 완료 | 🚫Ban: {len(portfolio.ban_list)}개, 🛑Loss-Blacklist: {len(risk_filter.loss_blacklist)}개, 👁️Watch: {len(active_candidates)}개")
        
        start_msg = (
            f"⚔️ [시스템 가동 v5.4 - 3중 리스크 필터 탑재]\n"
            f"⏰ 시간: KR {now_kst_start.strftime('%H:%M')} / NY {now_et_start.strftime('%H:%M')}\n"
            f"💰 자산: ${portfolio.total_equity:,.0f}\n"
            f"🎰 슬롯: {len(portfolio.positions)} / {portfolio.MAX_SLOTS}\n"
            f"🛡️ 손절 차단 종목 수: {len(risk_filter.loss_blacklist)}개"
        )
        bot.send_message(start_msg)
        
        def get_status_data():
            return {
                'cash': portfolio.balance,
                'total_equity': portfolio.total_equity,
                'positions': portfolio.positions,
                'targets': getattr(listener, 'current_watchlist', []),
                'ban_list': list(portfolio.ban_list),
                'loss_blacklist': list(risk_filter.loss_blacklist),
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
                    logger.info(f"📦 [Live Export] {reason} | files={saved_count} | zip={zip_path} | {delivery}")
                    bot.send_message(
                        f"📦 [Live Candle Export]\nReason: {reason}\nFiles: {saved_count}\nZip: {zip_path}\nDelivery: {delivery}"
                    )
                return result
            except Exception as export_error:
                logger.error(f"❌ [Live Export] {reason} failed: {export_error}")
                return {"date": export_date or current_date_str, "files": [], "zip_path": "", "telegram_sent": False, "manifest_rows": []}

        def send_spread_analysis_log(export_date=None):
            try:
                date_target = export_date or current_date_str
                date_clean = date_target.replace("-", "")
                spread_file = Path(f"logs/spread_analysis/signal_spreads_{date_clean}.csv")
                
                if spread_file.exists():
                    sent = bot.send_document(
                        str(spread_file), 
                        caption=f"📊 [Spread Analysis] {date_target} 호가 스냅샷 로그 (Ask/Bid/Volume)"
                    )
                    if sent:
                        logger.info(f"📤 [Spread Log] 텔레그램 전송 성공: {spread_file.name}")
            except Exception as e:
                logger.error(f"❌ [Spread Log] 텔레그램 전송 중 에러: {e}")
                
        def run_bot_thread():
            bot.start()
            
        t = threading.Thread(target=run_bot_thread)
        t.daemon = True 
        t.start()
        logger.info("🤖 텔레그램 봇 시작됨")

    except Exception as e:
        logger.critical(f"❌ 초기화 실패: {e}")
        return

    candle_cache = {}

    # ---------------------------------------------------------
    # [메인 루프]
    # ---------------------------------------------------------
    while True:
        try:
            now = datetime.datetime.now(pytz.timezone('America/New_York'))
            current_minute_str = now.strftime("%H:%M")

            # =========================================================
            # 🚀 [초고속 매도 전용 차선] 보유 종목 실시간 1초 감시
            # =========================================================
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
                            if reason != 'TAKE_PROFIT':
                                entry_p = pos.get('entry_price', real_time_price)
                                trade_pnl = (real_time_price - entry_p) / entry_p if entry_p > 0 else -0.01

                                result = order_manager.execute_sell(portfolio, ticker, reason, price=real_time_price)
                                if result:
                                    bot.send_message(result['msg'])
            
                                    # 🛑 손절 발생 즉시 3중 필터 블랙리스트에 추가
                                    risk_filter.register_trade_result(ticker, trade_pnl, reason=reason)
                                    save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist)
                    
                    time.sleep(0.5)

            # =========================================================
            # 🕒 [Time Sync] 캔들 완성형 (00초~05초 진입)
            # =========================================================
            current_kst = datetime.datetime.now(pytz.timezone('Asia/Seoul'))
            if not (current_kst.hour >= 17 or current_kst.hour < 5):
                if not was_sleeping:
                    logger.warning(f"💤 [AWS 정시 대기] 현재 한국 시간 {current_kst.strftime('%H:%M')}. 17시 정각까지 대기 루프 가동.")
                    was_sleeping = True
                time.sleep(10)
                continue

            if now.second > 5:
                time.sleep(0.5)
                continue
            
            if last_processed_minute == current_minute_str:
                time.sleep(0.5)
                continue
                
            last_processed_minute = current_minute_str
            
            # =========================================================
            # 💤 [Sleep Mode] 활동 시간 체크
            # =========================================================
            is_active, reason = is_active_market_time()
            
            if not is_active:
                if not was_sleeping:
                    logger.warning(f"💤 Sleep Mode: {reason}")
                    bot.send_message(f"💤 [대기] {reason}")
                    was_sleeping = True
                    save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist)
                
                time.sleep(30)
                continue
            
            if was_sleeping:
                bot.send_message(f"🌅 [기상] 시장 감시 시작 ({reason})")
                was_sleeping = False
                portfolio.sync_with_kis()

            # ---------------------------------------------------------
            # 🛑 [EOD] 장 마감 강제 청산
            # ---------------------------------------------------------
            cutoff_time_str = getattr(Config, 'TIME_HARD_CUTOFF', "15:54")
            cutoff_h, cutoff_m = map(int, cutoff_time_str.split(':'))
            
            is_after_cutoff = (now.hour > cutoff_h) or (now.hour == cutoff_h and now.minute >= cutoff_m)
            
            if is_after_cutoff and not eod_processed:
                logger.warning(f"⏰ [장 마감] 강제 청산 실행 (Current: {now.strftime('%H:%M')} >= Cutoff: {cutoff_time_str})")
                bot.send_message(f"🚨 [장 마감] 강제 청산 실행")
                
                if portfolio.positions:
                    for ticker in list(portfolio.positions.keys()):
                        order_manager.execute_sell(portfolio, ticker, "FORCE_EOD_EXIT", price=0)
                        time.sleep(0.2)
                
                save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist)
                run_live_candle_export(current_date_str, reason="eod")
                send_spread_analysis_log(current_date_str)
                logger.info("👋 [System] 장 마감으로 시스템을 종료합니다.")
                
                eod_processed = True
                time.sleep(300) 
                continue
            
            if not is_after_cutoff:
                eod_processed = False

            # =========================================================
            # 💓 [Heartbeat] 생존 신고
            # =========================================================
            if time.time() - last_heartbeat_time > HEARTBEAT_INTERVAL:
                eq = portfolio.total_equity
                pos_cnt = len(portfolio.positions)
                cur_k = datetime.datetime.now(tz_kst).strftime("%H:%M")
                cur_n = datetime.datetime.now(tz_et).strftime("%H:%M")
                
                watching_list = list(active_candidates)
                banned_list = list(portfolio.ban_list)
                loss_list = list(risk_filter.loss_blacklist)
                
                watch_str = ", ".join(watching_list[:5]) + ("..." if len(watching_list) > 5 else "")
                ban_str = ", ".join(banned_list[:5]) + ("..." if len(banned_list) > 5 else "")
                loss_str = ", ".join(loss_list[:5]) + ("..." if len(loss_list) > 5 else "")
                
                msg = (
                    f"💓 [생존] KR {cur_k} / NY {cur_n}\n"
                    f"💰 자산 ${eq:,.0f} | 보유 {pos_cnt}개\n"
                    f"👁️ 감시({len(watching_list)}): {watch_str}\n"
                    f"🚫 Ban({len(banned_list)}): {ban_str}\n"
                    f"🛑 손절차단({len(loss_list)}): {loss_str}"
                )
                
                bot.send_message(msg)
                last_heartbeat_time = time.time()

            # =========================================================
            # 📅 [Daily Reset] 날짜 변경 체크
            # =========================================================
            new_date_str = now.strftime("%Y-%m-%d")
            if new_date_str != current_date_str:
                logger.info(f"📅 [New Day] 날짜 변경 감지: {current_date_str} -> {new_date_str}")
                portfolio.ban_list.clear()
                risk_filter.reset_daily()  # 👈 [추가] 일일 리스크 필터 리셋
                active_candidates.clear()
                candle_cache.clear()
                candle_exporter.reset_session()
                save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist)
                logger.info("✨ [Reset] 금일 감시 종목 및 밴 리스트 초기화 완료")
                current_date_str = new_date_str

            # =========================================================
            # 🧠 [Logic] 매매 로직 시작 (매 분 1회 실행)
            # =========================================================
            prev_holdings = set(portfolio.positions.keys())
            portfolio.sync_with_kis()
            current_holdings = set(portfolio.positions.keys())
            
            sold_tickers = prev_holdings - current_holdings
            for ticker in sold_tickers:
                if ticker in portfolio.ban_list:
                    continue
                    
                logger.info(f"🎉 [익절 감지] {ticker} 목표가 도달 확인!")
                msg = (
                    f"🎉 <b>[익절 체결 확인]</b>\n"
                    f"📦 종목: {ticker}\n"
                    f"💰 결과: 목표가 달성 추정\n"
                    f"✅ 잔고에서 자동으로 청산되었습니다."
                )
                bot.send_message(msg)
                portfolio.ban_list.add(ticker)
                
                if ticker in active_candidates:
                    del active_candidates[ticker]
                    
                save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist)

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
                        active_candidates[sym] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist)

            # ---------------------------------------------------------
            # D. [매수] 진입 타점 확인 (3중 리스크 필터 적용)
            # ---------------------------------------------------------
            buy_candidates = [
                sym for sym in list(active_candidates)
                if not portfolio.is_holding(sym) and not portfolio.is_banned(sym)
            ]

            random.shuffle(buy_candidates)
            targets_to_check = buy_candidates[:15]
            listener.current_watchlist = targets_to_check 

            for sym in targets_to_check:
                try:
                    import pandas as pd
                    df = None
                    selected_exchange = None
                    
                    if sym not in candle_cache:
                        for exch in ["NAS", "NYS", "AMS"]:
                            temp_df = kis.get_minute_candles(exch, sym, limit=1200)
                            if not temp_df.empty and len(temp_df) >= 26:
                                df = temp_df
                                selected_exchange = exch
                                candle_cache[sym] = {'df': df, 'exch': exch}
                                break
                    else:
                        cached_data = candle_cache[sym]
                        old_df = cached_data['df']
                        exch = cached_data['exch']
                        selected_exchange = exch
                        
                        new_df = kis.get_minute_candles(exch, sym, limit=120)
                        if not new_df.empty:
                            combined_df = pd.concat([old_df, new_df])
                            combined_df = combined_df.drop_duplicates(subset=['date', 'time'], keep='last')
                            combined_df = combined_df.sort_values(['date', 'time']).reset_index(drop=True)
                            
                            if len(combined_df) > 1200:
                                combined_df = combined_df.iloc[-1200:].reset_index(drop=True)
                                
                            candle_cache[sym]['df'] = combined_df
                            df = combined_df
                        else:
                            df = old_df

                    if df is None or df.empty or len(df) < 26:
                        strategy._log_rejection(sym, "데이터 부족 (NAS/NYS/AMS 전체 탐색 실패)")
                        candle_cache.pop(sym, None)
                        continue

                    candle_exporter.update_runtime_candles(sym, df, exchange=selected_exchange)

                   # =========================================================
                    # 🧠 [Strategy] 전략 엔진 신호 확인
                    # =========================================================
                    signal = strategy.check_entry(sym, df)

                    if signal:
                        if signal['type'] == 'BUY':
                            # -----------------------------------------------------
                            # 🚌 [Missed Bus] 슬롯 여유 확인
                            # -----------------------------------------------------
                            if not portfolio.has_open_slot():
                                logger.warning(f"🚌 [Missed Bus] {sym} 진입 신호 왔으나 자리 없음 -> 영구 제외")
                                portfolio.ban_list.add(sym)      
                                if sym in active_candidates:
                                    del active_candidates[sym]
                                candle_cache.pop(sym, None)
                                save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist)
                                continue
                            
                            # 호가 조회 (선택된 거래소 코드 반영)
                            ask, bid, ask_vol, bid_vol = kis.get_market_spread(sym, exchange=selected_exchange or "NAS")
                            
                            if ask > 0 and bid > 0:
                                spread = (ask - bid) / ask * 100
                                if spread > 3.0:
                                    logger.warning(f"⚠️ [Spread] {sym}: 괴리율 과다 ({spread:.2f}%). 진입 보류.")
                                    continue
                            
                            entry_price = ask if ask > 0 else signal['price']
                            signal['price'] = entry_price
                            signal['ticker'] = sym

                            # =========================================================
                            # 🛡️ [Pre-Trade Validation] 3중 리스크 차단 필터 검사
                            # =========================================================
                            is_blocked, block_reason = risk_filter.is_order_blocked(
                                ticker=sym, price=entry_price, current_time_et=now
                            )
                            
                            if is_blocked:
                                logger.warning(f"🛑 [Risk Filter Blocked] {sym}: {block_reason}")
                                continue

                            # =========================================================
                            # ⚡ [Execution] 정상 주문 집행
                            # =========================================================
                            if portfolio.has_open_slot():
                                result = order_manager.execute_buy(portfolio, signal)
                                
                                if result:
                                    if result.get('msg'):
                                        bot.send_message(result['msg'])
                                    
                                    if result['status'] == 'success':
                                        candle_cache.pop(sym, None)
                                        save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist)
                                        
                                        time.sleep(1.5) 
                                        portfolio.sync_with_kis() 
                                        
                                        try:
                                            actual_pos = portfolio.get_position(sym)
                                            if actual_pos and actual_pos.get('entry_price', 0) > 0:
                                                buy_price = actual_pos['entry_price']
                                            else:
                                                buy_price = result.get('avg_price', signal['price']) 
                                            
                                            if buy_price > 0:
                                                target_profit_pct = getattr(Config, 'TARGET_PROFIT_PCT', 0.07)
                                                target_price = buy_price * (1.0 + target_profit_pct)
                                                target_price = round(target_price, 2)
                                                
                                                qty = result.get('qty', 0)
                                                
                                                if qty > 0:
                                                    logger.info(f"⚡ [Pre-Order] {sym} 실제 평단가(${buy_price}) 기반 익절 주문 전송: ${target_price}")
                                                    kis.send_order(sym, "SELL", qty, target_price, "00", exchange=selected_exchange or "NAS")
                                                    bot.send_message(f"🔒 [잠금] {sym} 익절 주문 완료 (평단가: ${buy_price:.3f} -> 목표가: ${target_price})")
                                        except Exception as e:
                                            logger.error(f"❌ 익절 주문 중 에러: {e}")

                                        if not portfolio.has_open_slot():
                                            break
                                    else:
                                        logger.warning(f"🚌 [실패] {sym} 매수 실패. 금일 제외.")
                                        portfolio.ban_list.add(sym)
                                        candle_cache.pop(sym, None)
                                        save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist)

                        elif signal['type'] == 'DROP':
                            logger.info(f"🗑️ [DROP] {sym} 추세 붕괴 확인 -> 감시 해제")
                            try:
                                del active_candidates[sym]
                            except KeyError:
                                pass
                            candle_cache.pop(sym, None)
                            save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist)

                    time.sleep(0.55)

                except Exception as e:
                    logger.error(f"❌ 매수 로직 에러({sym}): {e}")
                    bot.send_message(f"⚠️ [System Error] 매수 로직 중 오류 발생\n종목: {sym}\n내용: {str(e)}")
                    continue
            
            if not portfolio.positions and portfolio.balance < 10:
                logger.info("🔄 [Sync] 매도 후 잔고 재동기화 수행...")
                portfolio.sync_balance() 

            time.sleep(0.1)

        except KeyboardInterrupt:
            logger.info("🛑 관리자에 의한 수동 종료")
            bot.send_message("🛑 시스템을 종료합니다.")
            save_state(portfolio.ban_list, active_candidates, risk_filter.loss_blacklist)
            run_live_candle_export(current_date_str, reason="manual_shutdown")
            send_spread_analysis_log(current_date_str)
            break
            
        except Exception as e:
            error_msg = f"⚠️ [ERROR] 시스템 오류: {e}\n👉 10초 후 재시도..."
            logger.error(error_msg)
            time.sleep(10)

if __name__ == "__main__":
    main()