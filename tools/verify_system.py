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
from infra.real_portfolio import RealPortfolio      # [NEW] 검증 대상 추가
from infra.real_order_manager import RealOrderManager # [NEW] 검증 대상 추가

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
        
        # [핵심] 실제 운영될 객체 생성
        portfolio = RealPortfolio(kis)
        order_manager = RealOrderManager(kis)
        
        logger.info("✅ Infrastructure initialized successfully.")
    except Exception as e:
        logger.error(f"❌ Infrastructure Init Failed: {e}")
        return

    # 2. 동기화 및 잔고 확인
    try:
        logger.info("🔹 [Step 2] Syncing Portfolio with KIS Server...")
        portfolio.sync_with_kis()
        
        print("\n" + "="*40)
        print(f"💰 [RealPortfolio State]")
        print(f"   - Cash (Buying Power): ${portfolio.balance:,.2f}")
        print(f"   - Total Equity: ${portfolio.total_equity:,.2f}")
        print(f"   - Active Slots: {len(portfolio.positions)} / {portfolio.MAX_SLOTS}")
        print("="*40 + "\n")
        
        if portfolio.balance < 10:
            logger.warning("⚠️ 잔고가 부족하여 주문 테스트를 스킵합니다.")
            return

    except Exception as e:
        logger.error(f"❌ Portfolio Sync Failed: {e}")
        return

    # 3. 주문 테스트 (선택 사항)
    # 안전을 위해 사용자 확인을 받습니다.
    print("⚠️ [WARNING] 실제 주문 테스트를 진행하시겠습니까? (종목: SOXL, 수량: 1주)")
    user_input = input("👉 진행하려면 'yes'를 입력하세요: ")
    
    if user_input.lower() != 'yes':
        logger.info("🛑 주문 테스트가 사용자에 의해 취소되었습니다.")
        return

    try:
        logger.info("🔹 [Step 3] Executing Test Order (Buy & Sell)...")
        target_symbol = "SOXL" # 테스트용 소액 종목 (TQQQ보다 저렴)
        
        # A. 현재가 조회
        price_info = kis.get_current_price("NASD", target_symbol)
        current_price = price_info['last']
        
        # B. 매수 시도 (RealOrderManager 사용)
        logger.info(f"buying 1 share of {target_symbol} @ ${current_price}")
        
        # 강제 신호 생성
        signal = {
            'ticker': target_symbol,
            'price': current_price,
            'type': 'BUY',
            'time': datetime.now()
        }
        
        # OrderManager에게 위임 (자금 관리 체크 포함됨)
        buy_ord_no = order_manager.execute_buy(portfolio, signal)
        
        if buy_ord_no:
            logger.info(f"✅ Buy Order Placed! (OrdNo: {buy_ord_no})")
            bot.send_message(f"🧪 [Test] Buy Order Placed: {target_symbol}")
            
            # 체결 대기 (실전에서는 체결 통보를 기다려야 하지만, 테스트니 잠시 대기)
            logger.info("⏳ Waiting 15s for execution...")
            time.sleep(15)
            
            # 포트폴리오 재동기화 (잔고 반영 확인)
            portfolio.sync_with_kis()
            
            if portfolio.is_holding(target_symbol):
                logger.info(f"✅ Position Confirmed: {target_symbol}")
                
                # C. 매도 시도 (즉시 청산)
                logger.info("🔹 [Step 4] Selling Test Position...")
                sell_ord_no = order_manager.execute_sell(portfolio, target_symbol, "System Verification Test")
                
                if sell_ord_no:
                    logger.info(f"✅ Sell Order Placed! (OrdNo: {sell_ord_no})")
                    bot.send_message(f"🧪 [Test] Sell Order Placed: {target_symbol}")
                else:
                    logger.error("❌ Sell Order Failed!")
            else:
                logger.warning("⚠️ Order placed but position not found (Not filled yet?)")
        else:
            logger.error("❌ Buy Order Rejected by Manager (Funds? Slot?)")

    except Exception as e:
        logger.error(f"❌ Order Test Error: {e}")

if __name__ == "__main__":
    verify_system()