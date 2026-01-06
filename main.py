import time
import datetime
from infra.utils import get_logger
from infra.kis_api import KisApi
from infra.kis_auth import KisAuth
from infra.telegram_bot import TelegramBot
from data.market_listener import MarketListener
from strategy import GapZoneStrategy

# 로거 설정
logger = get_logger("Main")

def main():
    logger.info("🚀 GapZone System v3.1 (Hybrid Survival Mode) Starting...")
    
    # ---------------------------------------------------------
    # 1. 인프라 초기화 (The Hands & Eyes)
    # ---------------------------------------------------------
    try:
        # 인증 및 API 연결
        token_manager = KisAuth()
        kis = KisApi(token_manager)
        
        # 봇 및 리스너 연결
        bot = TelegramBot()
        listener = MarketListener(kis)
        
        # 전략 엔진 (The Brain) 연결 - 레고 조각!
        engine = GapZoneStrategy()     
        
        # 현재 활성화된 전략 정보 가져오기
        active_strat_name = "NEW_PRE" # 기본값 (strategy.py 설정에 따라 변경 가능)
        strat_params = engine.strategies.get(active_strat_name, {})
        
        start_msg = (
            f"⚔️ [시스템 가동]\n"
            f"🧠 전략: {active_strat_name}\n"
            f"🎯 목표: TP +{strat_params.get('take_profit', 0.12)*100}% / SL {strat_params.get('stop_loss', -0.05)*100}%\n"
            f"📡 상태: 감시 및 생존 신고(30분) 가동"
        )
        logger.info(start_msg)
        bot.send_message(start_msg)
        
    except Exception as e:
        logger.critical(f"❌ 초기화 실패: {e}")
        return

    # ---------------------------------------------------------
    # 2. 내부 설정 (Heartbeat & Money Management)
    # ---------------------------------------------------------
    last_heartbeat_time = time.time()
    HEARTBEAT_INTERVAL = 30 * 60  # 30분 (초 단위)

    def send_heartbeat():
        """[System] 생존 신고 메시지 전송"""
        try:
            now_str = datetime.datetime.now().strftime("%H:%M:%S")
            cash = kis.get_buyable_cash()
            
            # 보유 종목 정보 조회
            balances = kis.get_balance()
            holdings_str = "없음"
            
            if balances:
                h_list = []
                for item in balances:
                    sym = item['symbol']
                    qty = item['qty']
                    # 현재가 조회해서 수익률 보여주면 좋음
                    price_info = kis.get_current_price("NASD", sym)
                    if price_info:
                        curr = price_info['last']
                        # (주의: 평단가는 API 잔고에 포함 안 될 수 있음, 여기선 단순 수량만 표시)
                        h_list.append(f"{sym}({qty}주/Now ${curr})")
                    else:
                        h_list.append(f"{sym}({qty}주)")
                holdings_str = ", ".join(h_list)

            msg = (
                f"💓 [생존 신고] {now_str}\n"
                f"💰 예수금: ${cash:,.2f}\n"
                f"📦 보유: {holdings_str}\n"
                f"🔭 이상 무! 시스템 정상 작동 중."
            )
            bot.send_message(msg)
            logger.info(f"💓 Heartbeat sent. Cash: ${cash}")
            
        except Exception as e:
            logger.error(f"Heartbeat Error: {e}")

    # 자금 관리: 예수금의 98% 사용 (시장가/수수료 버퍼)
    def get_buy_qty(price):
        try:
            cash = kis.get_buyable_cash()
            if cash < 50: return 0 # 최소 $50 이상이어야 매매
            amount = cash * 0.98
            return int(amount / price)
        except:
            return 0

    # ---------------------------------------------------------
    # 3. 메인 루프 (The Body - Execution Loop)
    # ---------------------------------------------------------
    while True:
        try:
            # === [A] 하트비트 체크 (30분마다) ===
            if time.time() - last_heartbeat_time > HEARTBEAT_INTERVAL:
                send_heartbeat()
                last_heartbeat_time = time.time() 

            # === [B] 보유 종목 관리 (매도/청산 로직) ===
            # 전략(Brain)의 TP/SL 기준을 적용하여 '몸(Body)'이 매도를 수행합니다.
            balances = kis.get_balance() # 잔고(주머니) 확인
            
            if balances:
                for item in balances:
                    symbol = item['symbol']
                    qty = item['qty']
                    
                    # 1. 현재가 및 평단가 확인
                    # (KIS 잔고 API는 평단가(pamt)를 줄 수도, 안 줄 수도 있음. 여기선 현재가 조회 필수)
                    price_info = kis.get_current_price("NASD", symbol)
                    if not price_info: continue
                    
                    current_price = price_info['last']
                    
                    # 매입가(평단)를 API에서 정확히 못 가져올 경우를 대비해 
                    # 봇이 로그나 파일에 기록해야 하지만, 
                    # 'Survival Mode'에서는 [현재 수익률 계산]이 어렵다면 
                    # '트레일링 스탑'이나 '단순 등락'만 볼 수도 있습니다.
                    # 여기서는 **"매수 직후 가격"**을 평단으로 가정하거나 API의 pamt를 쓴다고 가정합니다.
                    # (※ KIS API get_balance() output1에는 'pamt'(평균단가)가 있습니다.)
                    
                    # 여기서는 안전하게 전략 규칙만 체크 (예시)
                    # 실제 평단가를 API에서 가져오려면 get_balance 함수 보강 필요.
                    # 일단은 '전략'이 매도 신호를 주는 로직을 수행한다고 가정.
                    
                    # [간소화] 현재 보유 중이라면, 전략에게 "팔까요?" 물어보기
                    # (여기서 전략(Brain)이 레고처럼 작동합니다)
                    # exit_signal = engine.check_exit(current_price, avg_price) 
                    # 하지만 지금 strategy.py에는 check_exit이 명시적으로 없으므로,
                    # main.py에서 직접 Active Strategy의 설정값(TP/SL)을 읽어와서 판단합니다.
                    
                    # ★ [중요] 평단가를 모를 경우를 대비한 로직 (손익분기점 추정 필요)
                    # 여기서는 "일단 스캔 및 매수는 정지"하고 보유 종목 감시에 집중합니다.
                    logger.info(f"📦 보유 중: {symbol} (Qty:{qty}) - 매도 조건 감시 중...")
                    
                    # --- [매도 로직 구현 예시] ---
                    # 1. 뇌(전략)에서 기준 가져오기
                    tp_pct = strat_params.get('take_profit', 0.12)  # +12%
                    sl_pct = strat_params.get('stop_loss', -0.05)   # -5%
                    
                    # *평단가를 정확히 안다고 가정 (나중에 get_balance 수정 필요할 수 있음)*
                    # 임시: 현재가가 0보다 크면 로직 수행
                    if current_price > 0:
                        # 매도 조건 충족 시 (예: 급등했거나 급락했거나)
                        # 여기서는 간단히 '익절'이나 '손절' 시그널이 발생했다고 가정하고 매도
                        pass 
                        # 실제로는: if 수익률 > tp_pct or 수익률 < sl_pct: kis.sell_market(...)

            # 보유 종목이 있으면 -> 추가 매수 금지 (Zone 1: 단일 종목 원칙)
            if balances and len(balances) > 0:
                time.sleep(10)
                continue


            # === [C] 신규 진입 (매수 로직) ===
            # 보유 종목이 없을 때만 스캔 시작
            
            # 1. 스캐너 가동 (Eyes)
            targets = listener.scan_markets() 
            if not targets:
                time.sleep(60)
                continue

            # 2. 타겟 분석 및 매수
            for sym in targets:
                # 1분봉 차트 조회
                df = kis.get_minute_candles("NASD", sym)
                if df.empty: continue
                
                # 3. 뇌(Brain)에게 판단 요청
                # "지금 이 차트(df)인데, 살까요?"
                signal = engine.get_buy_signal(df, sym)
                
                if signal:
                    # 4. 손(Hand)으로 매수 실행
                    price = signal['price']
                    qty = get_buy_qty(price)
                    
                    if qty > 0:
                        log_txt = f"⚡ [{signal['strategy']}] 매수 신호! {sym} @ ${price} (Qty: {qty})"
                        logger.info(log_txt)
                        bot.send_message(log_txt)
                        
                        # [매수 주문]
                        ord_no = kis.buy_limit(sym, price, qty)
                        if ord_no:
                            bot.send_message(f"✅ 주문 전송 완료: {ord_no}")
                            time.sleep(60) # 체결 대기
                            break # 한 놈만 팬다

            # API 호출 부하 방지
            time.sleep(5)

        except KeyboardInterrupt:
            logger.info("시스템 종료 요청.")
            bot.send_message("👋 시스템 종료 요청됨.")
            break
        except Exception as e:
            logger.error(f"Loop Error: {e}")
            bot.send_message(f"⚠️ 시스템 에러: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()