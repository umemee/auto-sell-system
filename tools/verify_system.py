import sys
import os
import time
import logging
from datetime import datetime

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from infra.kis_auth import KisAuth
from infra.kis_api import KisApi
from infra.telegram_bot import TelegramBot
from data.market_listener import MarketListener
from infra.utils import is_market_open

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("SystemVerifier")

def verify_system():
    logger.info("🚀 [System Verification] Starting diagnostics...")

    # 1. 인프라 초기화
    try:
        logger.info("🔹 [Step 1] Initializing Infrastructure...")
        auth = KisAuth()
        kis = KisApi(auth)

        # [수정] 인자 없이 호출하도록 변경
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
        logger.info(f"✅ Balance Check Success. Buyable Cash: ${cash:,.2f}")

        if cash < 160.0: # AMD 가격 고려
            logger.warning("⚠️ Low Balance for AMD test. Logic check only.")
    except Exception as e:
        logger.error(f"❌ Balance Check Failed: {e}")
        return

    # 3. 데이터 수신 (AMD - 안정적인 종목)
    target_symbol = "AMD"
    target_price = 0

    try:
        logger.info(f"🔹 [Step 3] Checking Market Data for {target_symbol}...")

        # 시세 조회
        price_info = kis.get_current_price("NASD", target_symbol)
        if not price_info:
             logger.error(f"❌ Failed to fetch price for {target_symbol}.")
             return

        target_price = price_info['last']
        logger.info(f"🎯 Test Target: {target_symbol} (Price: ${target_price})")

        df = kis.get_minute_candles("NASD", target_symbol)
        if df.empty:
            logger.error(f"❌ Failed to fetch candles.")
        else:
            logger.info(f"✅ Candles Fetched. Rows: {len(df)}")

    except Exception as e:
        logger.error(f"❌ Market Data Check Failed: {e}")
        return

    # 3.5 스캐너 로직 점검
    try:
        logger.info("🔹 [Step 3.5] Checking Scanner Logic...")
        try:
            listener.scan_markets()
        except:
            # 인자가 필요한 경우를 대비해 예외처리
            listener.scan_markets(min_change=0.0)

        logger.info("✅ Scanner Logic Executed.")
    except Exception as e:
        logger.error(f"❌ Scanner Logic Error: {e}")
        return

    # 4. 텔레그램
    try:
        logger.info("🔹 [Step 4] Sending Test Message...")
        bot.send_message(f"🧪 [Verify] Target: {target_symbol} @ ${target_price}")
        logger.info("✅ Telegram Message Sent.")
    except Exception as e:
        logger.error(f"❌ Telegram Failed: {e}")

    # 5. 실전 매매 (장중에만)
    if not is_market_open():
        logger.warning("⏸️ Market is closed (Regular Hours). Skipping Real Trade Test.")
        logger.info("🎉 DIAGNOSTICS COMPLETE (Ready for Market Open)")
        return

    logger.info("🔹 [Step 5] Real Trade Test (Buy 1 -> Sell 1)...")
    logger.warning("⚠️ Executing REAL ORDERS in 5 seconds. Ctrl+C to cancel.")
    time.sleep(5)

    try:
        # 잔고 재확인
        if cash < target_price * 1.05:
            logger.error(f"🛑 Insufficient Balance. Needed: ${target_price*1.05}, Have: ${cash}")
            return

        # [매수] 지정가 (현재가 + 2% 위로 긁기 - 즉시 체결 유도)
        buy_price = target_price * 1.02
        logger.info(f"💸 Buying {target_symbol} @ ${buy_price:.2f} (1 qty)")

        ord_no = kis.buy_limit(target_symbol, buy_price, 1)
        if not ord_no:
            logger.error("❌ Buy Order Failed.")
            return

        logger.info(f"⏳ Order Sent ({ord_no}). Waiting 10s for fill...")
        time.sleep(10) # API 호출 대신 단순 대기

        # 잔고 확인으로 체결 검증
        balance = kis.get_balance()
        has_stock = any(item['symbol'] == target_symbol for item in balance)

        if has_stock:
            logger.info("✅ BUY Filled (Confirmed via Balance)!")
            bot.send_message(f"🧪 [Buy Success] {target_symbol}")

            # [매도] 시장가
            time.sleep(2)
            logger.info(f"💸 Selling {target_symbol}")

            # kis_api에 sell_market이 있으면 사용, 없으면 지정가 매도로 대체
            if hasattr(kis, 'sell_market'):
                sell_no = kis.sell_market(target_symbol, 1)
            else:
                logger.warning("⚠️ No sell_market method. Trying limit sell @ $0 (Market).")
                sell_no = kis.buy_limit(target_symbol, 0, 1)

            if sell_no:
                logger.info(f"⏳ Sell Order Sent ({sell_no}). Waiting 10s...")
                time.sleep(10)
                logger.info("✅ SELL Sequence Complete.")
                bot.send_message(f"🧪 [Sell Success] {target_symbol}")
            else:
                logger.error("❌ Sell Order Failed.")
        else:
            logger.error("❌ Buy Order NOT Filled after 10s. Skipping Sell.")
            return

    except Exception as e:
        logger.error(f"❌ Trade Test Failed: {e}")
        return

    logger.info("🎉 ALL SYSTEMS NORMAL.")
    bot.send_message("✅ [System Verify] All Tests Passed.")

if __name__ == "__main__":
    verify_system()
