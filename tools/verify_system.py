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

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("SystemVerifier")

def verify_system():
    logger.info("🚀 [System Verification] Starting diagnostics...")
    
    # ---------------------------------------------------------
    # 1. 인프라 초기화 점검
    # ---------------------------------------------------------
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

    # ---------------------------------------------------------
    # 2. API 연결 및 잔고 조회 점검
    # ---------------------------------------------------------
    try:
        logger.info("🔹 [Step 2] Checking API Connection & Balance...")
        cash = kis.get_buyable_cash()
        logger.info(f"✅ Balance Check Success. Buyable Cash: ${cash:,.2f}")
        
        if cash < 10:
            logger.warning("⚠️ Warning: Low balance. Real trade test might fail.")
    except Exception as e:
        logger.error(f"❌ Balance Check Failed: {e}")
        return

    # ---------------------------------------------------------
    # 3. 시장 데이터 수신 점검
    # ---------------------------------------------------------
    target_symbol = None
    target_price = 0
    try:
        logger.info("🔹 [Step 3] Checking Market Data (Ranking & Price)...")
        ranking_list = kis.get_ranking(sort_type="vol")
        
        if not ranking_list:
            logger.error("❌ Failed to fetch ranking list.")
            return
            
        logger.info(f"✅ Ranking List Fetched. Top 1: {ranking_list[0]['symb']}")
        
        # 테스트 대상 선정 ($1~$100, 유동성 보유)
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
            logger.error("❌ No suitable test target found (Price $1~$100).")
            return

        logger.info(f"🎯 Test Target Selected: {target_symbol} (Price: ${target_price})")
        
        # 분봉 데이터 점검 (DataFrame 변환 확인)
        df = kis.get_minute_candles(target_symbol)
        if df.empty:
            logger.error(f"❌ Failed to fetch minute candles for {target_symbol}.")
        else:
            logger.info(f"✅ Minute Candles Fetched. Rows: {len(df)}")

    except Exception as e:
        logger.error(f"❌ Market Data Check Failed: {e}")
        return

    # ---------------------------------------------------------
    # 3.5 [New] 스캐너 로직 무결성 점검 (유기성 확보)
    # ---------------------------------------------------------
    try:
        logger.info("🔹 [Step 3.5] Checking Scanner Logic Integrity...")
        # 실제로 급등주가 없더라도 코드가 에러 없이 도는지 확인 (Dry Run)
        # min_change를 0으로 낮춰서라도 하나라도 걸리는지 확인하면 더 좋음
        candidates = listener.scan_markets(min_change=40.0) 
        logger.info(f"✅ Scanner Logic Executed without Error. Candidates found: {len(candidates)}")
    except Exception as e:
        logger.error(f"❌ Scanner Logic Crash: {e} (Check market_listener.py)")
        return

    # ---------------------------------------------------------
    # 4. 텔레그램 발송 점검
    # ---------------------------------------------------------
    try:
        logger.info("🔹 [Step 4] Sending Test Message...")
        bot.send_message(f"🧪 [System Verify] Diagnostic Test Started.\nTarget: {target_symbol}")
        logger.info("✅ Telegram Message Sent.")
    except Exception as e:
        logger.error(f"❌ Telegram Failed: {e}")

    # ---------------------------------------------------------
    # 5. 실전 매매 점검 (매수 -> 체결대기 -> 매도)
    # ---------------------------------------------------------
    logger.info("🔹 [Step 5] Executing Real Trade Test (Buy 1 -> Sell 1)...")
    logger.warning("⚠️ This will execute REAL ORDERS. Press Ctrl+C within 5 seconds to cancel.")
    time.sleep(5)
    
    try:
        # A. 매수 주문 (현재가 + 0.5% 지정가로 즉시 체결 유도)
        buy_price = target_price * 1.005 
        
        logger.info(f"💸 Sending BUY Order: {target_symbol} @ ${buy_price:.2f} (1 qty)")
        buy_order_no = kis.buy_limit(target_symbol, buy_price, 1)
        
        if not buy_order_no:
            logger.error("❌ Buy Order Failed (No Order No returned).")
            return
        
        logger.info(f"⏳ Waiting for BUY fill (Order: {buy_order_no})...")
        if kis.wait_for_fill(buy_order_no, timeout=60):
            logger.info("✅ BUY Filled!")
            bot.send_message(f"🧪 [Buy Test] Success: {target_symbol} 1 qty")
        else:
            logger.error("❌ Buy Order Timed Out (Not Filled). Aborting Sell Test.")
            logger.warning("⚠️ Please check your open orders manually.")
            return

        # 잠시 대기
        time.sleep(2)

        # B. 매도 주문 (안전 매도: 현재가 -5% 지정가)
        logger.info(f"💸 Sending SELL Order: {target_symbol}")
        # kis_api.py의 sell_market은 내부적으로 안전한 지정가(-5%)를 사용하도록 수정됨
        sell_order_no = kis.sell_market(target_symbol, 1)
        
        if not sell_order_no:
            logger.error("❌ Sell Order Failed.")
            return
            
        logger.info(f"⏳ Waiting for SELL fill (Order: {sell_order_no})...")
        if kis.wait_for_fill(sell_order_no, timeout=60):
            logger.info("✅ SELL Filled!")
            bot.send_message(f"🧪 [Sell Test] Success: {target_symbol} 1 qty")
        else:
            logger.error("❌ Sell Order Timed Out.")
            logger.warning("⚠️ You may still hold the position. Check manually.")
            return

    except Exception as e:
        logger.error(f"❌ Trade Test Failed: {e}")
        return

    logger.info("🎉 [System Verification] ALL SYSTEMS NORMAL.")
    bot.send_message("✅ [System Verify] All Tests Passed. System is Ready.")

if __name__ == "__main__":
    if not os.getenv("KIS_APP_KEY"):
        print("❌ Error: .env variables not loaded. Run from project root.")
        sys.exit(1)
        
    verify_system()