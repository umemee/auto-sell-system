import time
import datetime
from infra.utils import get_logger
from infra.kis_api import KisApi
from infra.telegram_bot import TelegramBot
from data.market_listener import MarketListener
from strategy import GapZoneStrategy  # 👈 우리가 만든 레고 박스 임포트

logger = get_logger("Main")

def main():
    logger.info("🚀 GapZone System Starting (Zone 1: Survival Mode)")
    
    # 1. 인프라 연결
    try:
        kis = KisApi()
        bot = TelegramBot()
        listener = MarketListener(kis) # 스캐너 연결
        engine = GapZoneStrategy()     # 전략 엔진 연결
        
        # 활성 전략 확인
        active_strats = [k for k,v in engine.strategies.items() if v['enabled']]
        msg = f"⚔️ [시스템 가동] 적용 전략: {active_strats}"
        logger.info(msg)
        bot.send_message(msg)
        
    except Exception as e:
        logger.error(f"❌ 초기화 실패: {e}")
        return

    # 2. Zone 1 자금 관리: 예수금의 98% (All-in)
    def get_order_qty(price):
        try:
            cash = kis.get_buyable_cash()
            if cash < 100: return 0 # $100 미만이면 매매 포기
            amount = cash * 0.98
            return int(amount / price)
        except:
            return 0

    # 3. 메인 루프 (무한 반복)
    while True:
        try:
            now = datetime.datetime.now()
            # (옵션) 장 운영 시간 체크: if not (09:30 < now < 16:00): sleep...
            
            # A. 스캐닝 (10분마다 급등주 찾기)
            # market_listener.py의 scan_markets()가 40% 급등주 리스트를 줍니다.
            targets = listener.scan_markets() 
            
            if not targets:
                # 타겟 없으면 잠시 대기
                time.sleep(60)
                continue

            # B. 타겟 종목 분석
            for sym in targets:
                # 이미 보유 중이면 패스 (단일 종목 원칙)
                balances = kis.get_balance()
                if balances and len(balances) > 0:
                    logger.info("🛑 보유 종목 존재. 추가 진입 금지.")
                    break # 루프 탈출
                
                # 1분봉 조회
                df = kis.get_minute_candles("NASD", sym)
                if df.empty: continue
                
                # C. 전략 엔진에게 물어보기 ("살까?")
                signal = engine.get_buy_signal(df, sym)
                
                if signal:
                    # D. 매수 신호 발생!
                    price = signal['price']
                    qty = get_order_qty(price)
                    
                    if qty > 0:
                        log_txt = f"⚡ [{signal['strategy']}] 매수 신호! {sym} @ ${price} (Qty: {qty})"
                        logger.info(log_txt)
                        bot.send_message(log_txt)
                        
                        # 실제 주문 (지정가)
                        ord_no = kis.buy_limit(sym, price, qty)
                        if ord_no:
                            bot.send_message(f"✅ 주문 전송 완료: {ord_no}")
                            # Zone 1 원칙: 하나 샀으면 오늘은 끝 (또는 청산 때까지 대기)
                            time.sleep(60) 
                            break 

            # API 호출 제한 고려 대기
            time.sleep(10)

        except KeyboardInterrupt:
            logger.info("시스템 종료 요청.")
            break
        except Exception as e:
            logger.error(f"Loop Error: {e}")
            bot.send_message(f"⚠️ 에러 발생: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()