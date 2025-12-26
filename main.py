# main.py
import time
import os
from kis_auth import TokenManager
from kis_api import KisApi
from strategy import GapZoneScalper
from utils import get_logger
from config import Config
from telegram_bot import TelegramBot # [NEW]

logger = get_logger()

def main():
    logger.info("=== KIS US Scalper System Started ===")
    
    token_manager = TokenManager()
    api = KisApi(token_manager)
    
    # 전략 초기화
    try:
        strategy = GapZoneScalper(api)
    except Exception as e:
        logger.error(f"전략 초기화 실패: {e}")
        return

    # [NEW] 텔레그램 봇 초기화
    bot = TelegramBot()
    bot.send_message(f"🚀 <b>시스템 시작!</b>\nTarget: {Config.TARGET_SYMBOL}\nBudget: ${Config.TOTAL_BUDGET_USD}")

    last_heartbeat = 0
    last_telegram_report = 0 

    while True:
        try:
            if os.path.exists("STOP.txt"):
                msg = "⛔ Kill Switch 작동. 시스템을 종료합니다."
                logger.info(msg)
                bot.send_message(msg)
                break

            # 데이터 갱신 및 로직 수행
            if strategy.update_market_data():
                
                # [매수 로직]
                if strategy.check_entry_signal():
                    if strategy.execute_buy():
                        # 매수 성공 알림
                        bot.send_message(f"⚡ <b>[매수 체결]</b> {strategy.symbol}\n가격: ${strategy.current_price}")
                
                # [매도 로직]
                exit_msg = strategy.check_exit_signal()
                if exit_msg:
                    # 매도 성공 알림
                    bot.send_message(f"💰 <b>[매도 체결]</b> {strategy.symbol}\n결과: {exit_msg}")

            # ------------------------------------------------
            # [시각화 & 디버깅] 자네가 원하던 기능!
            # ------------------------------------------------
            current_ts = time.time()
            
            # 1. 로그 심박동 (10초마다)
            if current_ts - last_heartbeat > 10:
                debug = strategy.debug_info
                price = getattr(strategy, 'current_price', 0)
                target = debug.get('target_price', 0)
                reason = debug.get('reason', 'N/A')
                
                # 로그창에 목표가와 이유를 함께 출력
                logger.info(f"💓 [감시중] 현재: ${price} | 목표: ${target:.2f} | 상태: {reason}")
                last_heartbeat = current_ts

            # 2. 텔레그램 정기 보고 (30분마다)
            if current_ts - last_telegram_report > 1800:
                debug = strategy.debug_info
                price = getattr(strategy, 'current_price', 0)
                target = debug.get('target_price', 0)
                
                report = (
                    f"📊 <b>[정기 생존신고]</b>\n"
                    f"종목: {Config.TARGET_SYMBOL}\n"
                    f"현재가: ${price}\n"
                    f"목표가: ${target:.2f}\n"
                    f"상태: {debug.get('reason', 'N/A')}"
                )
                bot.send_message(report)
                last_telegram_report = current_ts

            time.sleep(Config.RATE_LIMIT_DELAY)

        except Exception as e:
            logger.error(f"Critical Error: {e}")
            bot.send_message(f"⚠️ 시스템 에러: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()