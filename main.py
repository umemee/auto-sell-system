# main.py
import time
import os
from kis_auth import TokenManager
from kis_api import KisApi
from strategy import GapZoneScalper
from scanner import MarketScanner
from utils import get_logger
from config import Config
from telegram_bot import TelegramBot

logger = get_logger()

def main():
    logger.info("=== KIS AI-Quant System Started (Auto-Scanning) ===")
    
    token_manager = TokenManager()
    api = KisApi(token_manager)
    strategy = GapZoneScalper(api)
    scanner = MarketScanner(api)
    bot = TelegramBot()
    
    bot.send_message("🚀 <b>시스템 가동</b>\n모드: 자동 스캐닝 & Gap-Zone 트레이딩")

    last_heartbeat = 0
    last_scan_time = 0
    SCAN_INTERVAL = 600

    while True:
        try:
            if os.path.exists("STOP.txt"):
                msg = "⛔ Kill Switch 작동. 시스템 종료."
                logger.info(msg)
                bot.send_message(msg)
                break

            current_ts = time.time()

            # 1. 스캐닝 (10분 주기)
            if current_ts - last_scan_time > SCAN_INTERVAL:
                logger.info("📡 스캐너 가동: 새로운 급등주를 탐색합니다...")
                new_targets = scanner.scan_and_filter()
                
                if new_targets:
                    old_targets = Config.TARGET_SYMBOLS
                    Config.TARGET_SYMBOLS = new_targets
                    msg = (f"🔄 <b>타겟 리스트 갱신</b>\n"
                           f"기존: {old_targets}\n"
                           f"신규: {new_targets}")
                    logger.info(f"Target Update: {new_targets}")
                    bot.send_message(msg)
                else:
                    logger.info("스캔 결과 없음 (기존 타겟 유지)")
                
                last_scan_time = current_ts

            # 2. 감시 리스트 병합
            holding_symbols = [
                sym for sym, state in strategy.states.items() 
                if state.get("has_position", False)
            ]
            monitoring_list = list(set(Config.TARGET_SYMBOLS + holding_symbols))
            
            # 3. 전략 실행
            for symbol in monitoring_list:
                strategy.process_symbol(symbol)
                time.sleep(Config.RATE_LIMIT_DELAY)

            # 4. 생존 신고 (안전장치 추가됨)
            if current_ts - last_heartbeat > 30:
                statuses = []
                for sym in monitoring_list:
                    # [수정] 안전하게 가져오기 (Type Check)
                    info = strategy.debug_info.get(sym)
                    
                    if isinstance(info, dict):
                        reason = info.get("reason", "N/A")
                    else:
                        reason = "Initializing..." # 데이터가 없거나 꼬였을 때
                        
                    statuses.append(f"{sym}:{reason}")
                
                status_str = " | ".join(statuses)
                if len(status_str) > 100: status_str = status_str[:100] + "..."
                
                log_msg = f"💓 [감시중({len(monitoring_list)})] {status_str}"
                logger.info(log_msg)
                last_heartbeat = current_ts

        except Exception as e:
            logger.error(f"Main Loop Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()