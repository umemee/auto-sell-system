# main.py
import time
import os
from kis_auth import TokenManager
from kis_api import KisApi
from strategy import GapZoneScalper
from utils import get_logger
from config import Config
from telegram_bot import TelegramBot

logger = get_logger()

def main():
    logger.info("=== KIS Multi-Symbol Scalper Started ===")
    
    token_manager = TokenManager()
    api = KisApi(token_manager)
    strategy = GapZoneScalper(api)
    bot = TelegramBot()
    
    bot.send_message(f"🚀 시스템 시작! (감시 종목: {len(Config.TARGET_SYMBOLS)}개)")

    last_heartbeat = 0

    while True:
        try:
            if os.path.exists("STOP.txt"):
                msg = "⛔ Kill Switch 작동"
                logger.info(msg)
                bot.send_message(msg)
                break

            # [순찰 루프] 등록된 모든 종목을 하나씩 검사
            # 나중에 스캐너가 TARGET_SYMBOLS를 바꾸면, 봇은 바뀐 리스트를 돌게 됨 (자동화!)
            for symbol in Config.TARGET_SYMBOLS:
                
                # 1. 종목 처리 (전략 실행)
                strategy.process_symbol(symbol)
                
                # 2. 결과 확인 및 알림 (상태 변화 감지 등은 strategy 내부 로직 강화 필요하지만 일단 로그로 확인)
                # (API 호출 제한을 위해 종목 간 딜레이)
                time.sleep(Config.RATE_LIMIT_DELAY)

            # [생존 신고] (10초마다 대표 1개 혹은 전체 요약)
            current_ts = time.time()
            if current_ts - last_heartbeat > 10:
                # 감시 중인 종목들의 상태를 한 줄로 요약
                statuses = []
                for sym in Config.TARGET_SYMBOLS:
                    info = strategy.debug_info.get(sym, {})
                    reason = info.get("reason", "-")
                    statuses.append(f"{sym}:{reason}")
                
                log_msg = f"💓 [감시중] " + " | ".join(statuses)
                logger.info(log_msg)
                last_heartbeat = current_ts

        except Exception as e:
            logger.error(f"Main Loop Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()