import sys
import os
import time
import logging

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from infra.kis_auth import KisAuth
from infra.kis_api import KisApi
from infra.telegram_bot import TelegramBot
from data.market_listener import MarketListener

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("SystemVerifier")

def verify_system():
    logger.info("🚀 [System Verification] Starting diagnostics...")
    
    # 1. 인프라 초기화
    try:
        logger.info("🔹 [Step 1] Initializing Infrastructure...")
        auth = KisAuth()
        kis = KisApi(auth)
        bot = TelegramBot()
        listener = MarketListener(kis)
        logger.info("✅ Infrastructure initialized successfully.")
    except Exception as e:
        logger.error(f"❌ Infrastructure Init Failed: {e}")
        return

    # 2. API & 잔고
    try:
        logger.info("🔹 [Step 2] Checking API Connection & Balance...")
        cash = kis.get_buyable_cash()
        if cash is None:
            logger.error("❌ Balance Check Returned None. Check API Keys or URL.")
            return
        logger.info(f"✅ Balance Check Success. Buyable Cash: ${cash:,.2f}")
    except Exception as e:
        logger.error(f"❌ Balance Check Failed: {e}")
        return

    # 3. 데이터 수신
    target_symbol = None
    target_price = 0
    try:
        logger.info("🔹 [Step 3] Checking Market Data...")
        ranking_list = kis.get_ranking(sort_type="vol")
        if not ranking_list:
            logger.error("❌ Failed to fetch ranking list.")
            return
        
        logger.info(f"✅ Ranking List Fetched. Top 1: {ranking_list[0]['symb']}")
        
        # 테스트 대상 ($1~$100)
        for item in ranking_list:
            try:
                price = float(item['last'])
                if 1.0 <= price <= 100.0:
                    target_symbol = item['symb']
                    target_price = price
                    break
            except:
                continue
        
        if not target_symbol:
            logger.error("❌ No suitable test target found.")
            return

        logger.info(f"🎯 Test Target: {target_symbol} (Price: ${target_price})")
        
        # 분봉 확인
        df = kis.get_minute_candles(target_symbol)
        if df.empty:
            logger.error(f"❌ Failed to fetch candles for {target_symbol}.")
        else:
            logger.info(f"✅ Candles Fetched. Rows: {len(df)}")

    except Exception as e:
        logger.error(f"❌ Market Data Check Failed: {e}")
        return

    # 3.5 스캐너 로직 점검
    try:
        logger.info("🔹 [Step 3.5] Checking Scanner Logic...")
        # 조건 없이 스캔 실행해보기 (Dry Run)
        listener.scan_markets(min_change=0.0) 
        logger.info("✅ Scanner Logic Executed.")
    except Exception as e:
        logger.error(f"❌ Scanner Logic Error: {e}")
        return

    # 4. 텔레그램
    try:
        logger.info("🔹 [Step 4] Sending Test Message...")
        bot.send_message(f"🧪 [Verify] Test Target: {target_symbol}")
        logger.info("✅ Telegram Message Sent.")
    except Exception as e:
        logger.error(f"❌ Telegram Failed: {e}")

    # 5. 실전 매매 (장중에만 동작)
    logger.info("🔹 [Step 5] Real Trade Test (Buy 1 -> Sell 1)...")
    logger.warning("⚠️ Executing REAL ORDERS in 5 seconds. Ctrl+C to cancel.")
    time.sleep(5)
    
    try:
        # 매수
        buy_price = target_price * 1.01 # 1% 위 지정가 (즉시 체결)
        logger.info(f"💸 Buying {target_symbol} @ ${buy_price:.2f} (1 qty)")
        
        ord_no = kis.buy_limit(target_symbol, buy_price, 1)
        if not ord_no:
            logger.error("❌ Buy Order Failed.")
            return
            
        logger.info(f"⏳ Waiting for fill (Order: {ord_no})...")
        if kis.wait_for_fill(ord_no, timeout=60):
            logger.info("✅ BUY Filled!")
            bot.send_message(f"🧪 [Buy Success] {target_symbol}")
        else:
            logger.error("❌ Buy Order Timed Out. Check manually.")
            return

        time.sleep(2)

        # 매도
        logger.info(f"💸 Selling {target_symbol}")
        sell_no = kis.sell_market(target_symbol, 1)
        if not sell_no:
            logger.error("❌ Sell Order Failed.")
            return
            
        logger.info(f"⏳ Waiting for sell (Order: {sell_no})...")
        if kis.wait_for_fill(sell_no, timeout=60):
            logger.info("✅ SELL Filled!")
            bot.send_message(f"🧪 [Sell Success] {target_symbol}")
        else:
            logger.error("❌ Sell Order Timed Out. Check manually.")
            return

    except Exception as e:
        logger.error(f"❌ Trade Test Failed: {e}")
        return

    logger.info("🎉 ALL SYSTEMS NORMAL.")
    bot.send_message("✅ [System Verify] All Tests Passed.")

if __name__ == "__main__":
    verify_system()
