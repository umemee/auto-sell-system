# order.py - 해외주식 자동매도 시스템 (v3.0 완전 자동매매 지원)

import requests
import json
import logging
import time
import threading
from datetime import datetime, time as dtime, timedelta
from typing import Dict, List, Any, Optional
from pytz import timezone

logger = logging.getLogger(__name__)

import functools

def log_api_call(api_name: str, tr_id: str):
    """API 호출을 예쁘게 기록해주는 도구입니다."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            logger.info(f"\n{'='*40}\n📤 API 호출 시작: {api_name}\nTR_ID: {tr_id}\n{'='*40}")
            try:
                result = func(*args, **kwargs)
                logger.info(f"✅ API 호출 성공: {api_name}")
                return result
            except Exception as e:
                logger.error(f"❌ API 호출 실패: {api_name}\n오류 내용: {str(e)}")
                return None
        return wrapper
    return decorator

# ============================================================================
# 시장 시간 판별 함수 (기획서 2.2, 2.3)
# ============================================================================

def is_market_hours(trading_timezone='US/Eastern'):
    """
    기획서 2.2: 시장 시간 상태 반환
    Returns: 'premarket', 'regular', 'closed'
    """
    try:
        tz = timezone(trading_timezone)
        now = datetime.now(tz).time()
        
        premarket_start = dtime(5, 0)
        regular_start = dtime(9, 30)
        system_end = dtime(12, 0)
        
        if premarket_start <= now < regular_start:
            return 'premarket'
        elif regular_start <= now < system_end:
            return 'regular'
        else:
            return 'closed'
    except Exception as e:
        logger.warning(f"⚠️ 시간 판별 오류: {e}")
        return 'unknown'


def is_extended_hours(trading_timezone='US/Eastern'):
    """
    기획서 2.3: 프리마켓 시간인지 판별
    Returns: bool
    """
    try:
        tz = timezone(trading_timezone)
        now = datetime.now(tz).time()
        
        regular_start = dtime(9, 30)
        system_end = dtime(12, 0)
        
        return not (regular_start <= now < system_end)
    except Exception as e:
        logger.warning(f"⚠️ 시간 판별 오류: {e}, 기본값(정규장) 사용")
        return False


def should_system_run(trading_timezone='US/Eastern'):
    """
    기획서 2.2: 시스템 운영 여부 확인
    Returns: bool
    """
    try:
        tz = timezone(trading_timezone)
        now_dt = datetime.now(tz)
        now_time = now_dt.time()
        weekday = now_dt.weekday()  # 0=월, 4=금, 5=토, 6=일

        # 주말 슬립 모드
        if weekday in [5, 6]:
            weekday_names = ['월', '화', '수', '목', '금', '토', '일']
            logger.debug(f"🌴 주말({weekday_names[weekday]}요일) - 슬립 모드 유지")
            return False

        # 월요일 새벽 슬립 모드 (05:00 이전)
        start_time_of_day = dtime(5, 0)
        if weekday == 0 and now_time < start_time_of_day:
            logger.debug(f"🌙 월요일 새벽 (ET {now_time.strftime('%H:%M')}) - 아직 시작 전")
            return False

        # 운영 시간 체크 (ET 05:00-12:00)
        start_time = start_time_of_day
        end_time = dtime(12, 0)

        is_running = start_time <= now_time < end_time

        if not is_running:
            logger.debug(f"🌙 운영 시간 외 (현재: ET {now_time.strftime('%H:%M')})")

        return is_running
    
    except Exception as e:
        logger.error(f"❌ 시스템 운영 시간 체크 오류: {e}")
        return False


# ============================================================================
# 보유 종목 조회 함수
# ============================================================================

@log_api_call('보유 종목 조회', 'TTTS3012R')
def get_holdings(config, token_manager, exchange_code='NASD', currency_code='USD'):
    """
    해외주식 잔고 조회 (토큰 갱신 로직 추가됨)
    """
    url = f"{config['api']['base_url']}/uapi/overseas-stock/v1/trading/inquire-balance"
    
    # [수정] 토큰 만료 대응을 위한 재시도 루프
    for attempt in range(2):
        try:
            # 재시도(attempt=1)인 경우 강제 갱신
            is_retry = (attempt > 0)
            token = token_manager.get_access_token(force_refresh=is_retry)
            
            if not token:
                logger.error("❌ 보유 종목 조회: 액세스 토큰 없음")
                if is_retry: return []
                continue
            
            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": config["api_key"],
                "appsecret": config["api_secret"],
                "tr_id": "TTTS3012R",
                "custtype": "P"
            }
            
            params = {
                "CANO": config["cano"],
                "ACNT_PRDT_CD": config["acnt_prdt_cd"],
                "OVRS_EXCG_CD": exchange_code,
                "TR_CRCY_CD": currency_code,
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": ""
            }
            
            masked_cano = config["cano"][:4] + "****" if config["cano"] else "Unknown"
            
            logger.info(f"""
    📤 API 호출 준비 (보유 종목 조회) - 시도 {attempt+1}
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    엔드포인트: /uapi/overseas-stock/v1/trading/inquire-balance
    TR_ID: TTTS3012R
    계좌: {masked_cano} / {config["acnt_prdt_cd"]}
    거래소: {exchange_code}
    통화: {currency_code}
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            """)
            
            response = requests.get(url, headers=headers, params=params, timeout=15)
            
            logger.info(f"""
    📥 API 응답 수신 (보유 종목 조회)
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    상태코드: {response.status_code}
    응답 길이: {len(response.text)} bytes
    응답 내용 (처음 300자):
    {response.text[:300]}
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            """)

            if response.status_code != 200:
                logger.error(f"""
    ❌ HTTP 오류 (보유 종목 조회)
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    상태코드: {response.status_code}
    응답: {response.text}
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                """)
                if attempt == 1: return []
                # HTTP 오류도 재시도해볼 가치는 있으나, 여기선 토큰 만료 위주로 처리
                continue 

            try:
                data = response.json()
            except json.JSONDecodeError as e:
                logger.error(f"❌ JSON 파싱 오류: {str(e)}")
                if attempt == 1: return []
                continue
            
            # [수정] 토큰 만료 체크
            if data.get('msg_cd') == 'EGW00123':
                logger.warning(f"⚠️ [보유종목] 토큰 만료(EGW00123) 감지. 갱신 후 재시도 (시도 {attempt+1})")
                continue

            rt_cd = data.get("rt_cd", "")
            
            if rt_cd != "0":
                msg1 = data.get("msg1", "알 수 없는 오류")
                msg2 = data.get("msg2", "")
                logger.error(f"""
    ❌ API 반환 오류 (보유 종목 조회)
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    rt_cd: {rt_cd}
    msg1: {msg1}
    msg2: {msg2}
    전체 응답: {json.dumps(data, ensure_ascii=False)}
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                """)
                return []

            output2 = data.get("output2", [])
            
            if not output2:
                logger.info(f"📋 조회 결과: 보유 종목 없음 (rt_cd={rt_cd})")
                return []
            
            holdings = []
            for item in output2:
                try:
                    ticker = item.get("ovrs_pdno", "").strip()
                    quantity_str = item.get("ovrs_cblc_qty", "0")
                    avg_price_str = item.get("pchs_avg_pric", "0")
                    current_price_str = item.get("now_pric2", "0")
                    profit_loss_str = item.get("frcr_evlu_pfls_amt", "0")
                    profit_rate_str = item.get("evlu_pfls_rt", "0")
                    eval_amt_str = item.get("ovrs_stck_evlu_amt", "0")
                    purchase_amt_str = item.get("frcr_pchs_amt1", "0")
                    
                    quantity = int(quantity_str) if quantity_str else 0
                    avg_price = float(avg_price_str) if avg_price_str else 0.0
                    current_price = float(current_price_str) if current_price_str else 0.0
                    profit_loss = float(profit_loss_str) if profit_loss_str else 0.0
                    profit_rate = float(profit_rate_str) if profit_rate_str else 0.0
                    eval_amt = float(eval_amt_str) if eval_amt_str else 0.0
                    purchase_amt = float(purchase_amt_str) if purchase_amt_str else 0.0
                    
                    if ticker and quantity > 0:
                        holding_info = {
                            'ticker': ticker,
                            'pdno': ticker,
                            'symbol': ticker,
                            'stock_code': ticker,
                            'quantity': quantity,
                            'avg_price': avg_price,
                            'current_price': current_price,
                            'profit_loss': profit_loss,
                            'profit_rate': profit_rate,
                            'eval_amt': eval_amt,
                            'purchase_amt': purchase_amt,
                            'exchange_code': exchange_code,
                            'currency_code': currency_code
                        }
                        holdings.append(holding_info)
                        logger.debug(f"📊 {ticker}: {quantity}주 @ ${avg_price:.2f}")
                
                except (ValueError, TypeError) as e:
                    logger.warning(f"⚠️ 종목 정보 파싱 오류: {e}, 항목: {item}")
                    continue
            
            logger.info(f"✅ 보유 종목 조회 완료: {len(holdings)}개")
            holdings.sort(key=lambda x: x.get('profit_rate', 0), reverse=True)
            return holdings
        
        except requests.exceptions.Timeout:
            logger.error("❌ 보유 종목 조회 타임아웃 (15초)")
            if attempt == 1: return []
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ 보유 종목 조회 네트워크 오류: {e}")
            if attempt == 1: return []
        except Exception as e:
            logger.error(f"❌ 보유 종목 조회 오류: {e}")
            import traceback
            traceback.print_exc()
            if attempt == 1: return []

    return []


# ============================================================================
# 주문체결내역 조회 함수
# ============================================================================
@log_api_call('체결내역 조회', 'TTTS3035R') 
def inquire_ccnl(config, token_manager, pdno="", ord_strt_dt="", ord_end_dt="", 
                 sll_buy_dvsn="00", ccld_nccs_dvsn="00", ovrs_excg_cd="%", 
                 sort_sqn="DS", ord_dt="", ord_gno_brno="", odno=""):
    """
    해외주식 주문체결내역 조회 (토큰 갱신 로직 추가됨)
    """
    try:
        import pandas as pd
        
        url = f"{config['api']['base_url']}/uapi/overseas-stock/v1/trading/inquire-ccnl"

        all_output = []
        ctx_area_fk200 = ""
        ctx_area_nk200 = ""

        logger.info(f"📋 체결내역 조회 시작: {ord_strt_dt} ~ {ord_end_dt} (연속 조회)")

        while True:
            # [수정] 페이지별 재시도 루프
            page_success = False
            for attempt in range(2):
                try:
                    is_retry = (attempt > 0)
                    token = token_manager.get_access_token(force_refresh=is_retry)
                    
                    if not token:
                        logger.error("❌ 체결내역 조회: 액세스 토큰 없음")
                        if is_retry: break
                        continue

                    headers = {
                        "Content-Type": "application/json",
                        "authorization": f"Bearer {token}",
                        "appkey": config["api_key"],
                        "appsecret": config["api_secret"],
                        "tr_id": "TTTS3035R",
                        "custtype": "P"
                    }

                    params = {
                        "CANO": config["cano"],
                        "ACNT_PRDT_CD": config["acnt_prdt_cd"],
                        "PDNO": pdno,
                        "ORD_STRT_DT": ord_strt_dt,
                        "ORD_END_DT": ord_end_dt,
                        "SLL_BUY_DVSN": sll_buy_dvsn,
                        "CCLD_NCCS_DVSN": ccld_nccs_dvsn,
                        "OVRS_EXCG_CD": ovrs_excg_cd,
                        "SORT_SQN": sort_sqn,
                        "ORD_DT": ord_dt,
                        "ORD_GNO_BRNO": ord_gno_brno,
                        "ODNO": odno,
                        "CTX_AREA_NK200": ctx_area_nk200,
                        "CTX_AREA_FK200": ctx_area_fk200
                    }

                    response = requests.get(url, headers=headers, params=params, timeout=15)

                    if response.status_code != 200:
                        logger.error(f"❌ 체결내역 조회 HTTP 오류: {response.status_code}")
                        if attempt == 1: break
                        continue

                    data = response.json()
                    
                    # [수정] 토큰 만료 체크
                    if data.get('msg_cd') == 'EGW00123':
                        logger.warning(f"⚠️ [체결내역] 토큰 만료(EGW00123). 재시도 (시도 {attempt+1})")
                        continue

                    rt_cd = data.get("rt_cd", "")
                    if rt_cd != "0":
                        msg1 = data.get("msg1", "알 수 없는 오류")
                        logger.warning(f"⚠️ 체결내역 조회 실패: {msg1} (rt_cd={rt_cd})")
                        page_success = False # 루프 탈출용 플래그
                        break

                    output_page = data.get("output", [])
                    if output_page:
                        all_output.extend(output_page)

                    tr_cont = data.get("tr_cont", "")
                    ctx_area_fk200 = data.get("ctx_area_fk200", "")
                    ctx_area_nk200 = data.get("ctx_area_nk200", "")

                    if tr_cont in ["F", "M"]:
                        logger.debug(f"... 체결내역 연속 조회 중 (tr_cont={tr_cont})")
                        time.sleep(0.1)
                        page_success = True
                        break # 성공 -> attempt 루프 탈출, while 루프 계속
                    else:
                        logger.debug("... 체결내역 연속 조회 완료 (마지막 페이지)")
                        page_success = True
                        return pd.DataFrame(all_output) # 전체 완료

                except Exception as e:
                    logger.error(f"❌ 체결내역 조회 페이지 오류: {e}")
                    if attempt == 1: break
            
            # 페이지 조회 실패 시 중단
            if not page_success and not all_output:
                return None
            if not page_success: # 일부만 성공했으면 그거라도 리턴
                return pd.DataFrame(all_output)

    except ImportError:
        logger.error("❌ 'pandas' 라이브러리가 필요합니다.")
        return None
    except Exception as e:
        logger.error(f"❌ 체결내역 조회 오류: {e}")
        return None


# ============================================================================
# HashKey 생성 함수 (한국투자증권 공식)
# ============================================================================
@log_api_call('HashKey 생성', 'hashkey')
def get_hash_key(config, token_manager, order_data):
    """
    한국투자증권 주문용 HashKey 생성 (토큰 갱신 추가)
    """
    url = f"{config['api']['base_url']}/uapi/hashkey"
    
    for attempt in range(2):
        try:
            is_retry = (attempt > 0)
            token = token_manager.get_access_token(force_refresh=is_retry)
            
            if not token:
                logger.error("❌ HashKey 생성: 토큰 없음")
                continue
            
            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": config['api_key'],
                "appsecret": config['api_secret']
            }
            
            response = requests.post(url, headers=headers, json=order_data, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                hashkey = data.get("HASH")
                if hashkey:
                    logger.debug(f"✅ HashKey 생성 성공: {hashkey[:20]}...")
                    return hashkey
                else:
                    logger.error(f"❌ HashKey 응답에 HASH 없음: {data}")
            else:
                logger.error(f"❌ HashKey HTTP 오류 {response.status_code}: {response.text}")
                
        except Exception as e:
            logger.error(f"❌ HashKey 생성 오류: {e}")
            
    return None


# ============================================================================
# 자동 매도 주문 실행 함수 (핵심!)
# ============================================================================
@log_api_call('자동 매도 주문', 'TTTT1006U')
def place_sell_order(config, token_manager, execution_data, telegram_bot=None):
    """
    기획서 4장: 자동 매도 주문 실행 함수 (토큰 갱신 추가)
    """
    try:
        buy_price = execution_data['price']
        target_profit_rate = config.get('order_settings', {}).get('target_profit_rate', 6.0)
        profit_margin = target_profit_rate / 100
        raw_sell_price = buy_price * (1 + profit_margin)
        
        sell_price = round(raw_sell_price, 2) if raw_sell_price >= 1.0 else round(raw_sell_price, 4)
            
        logger.info(f"🎯 매도 주문 준비: {execution_data['ticker']} {execution_data['quantity']}주 @ ${sell_price}")
        
        exchange_code = config.get('order_settings', {}).get('exchange_code', 'NASD')
        ticker = execution_data['ticker']
        if ticker in ['AAPL', 'MSFT', 'GOOGL', 'TSLA', 'NVDA', 'AMZN']:
            exchange_code = 'NASD'
        
        order_data = {
            "CANO": config['cano'],
            "ACNT_PRDT_CD": config['acnt_prdt_cd'],
            "OVRS_EXCG_CD": exchange_code,
            "PDNO": ticker,
            "ORD_QTY": str(execution_data['quantity']),
            "OVRS_ORD_UNPR": str(sell_price),
            "CTAC_TLNO": "",
            "MGCO_APTM_ODNO": "",
            "SLL_TYPE": "00",
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00"
        }

        # HashKey는 별도 함수에서 토큰 관리
        hashkey = get_hash_key(config, token_manager, order_data)
        if not hashkey:
            if telegram_bot: telegram_bot.send_error_notification("매도 주문 실패: HashKey 생성 불가")
            return False
        
        sell_url = f"{config['api']['base_url']}/uapi/overseas-stock/v1/trading/order"
        
        # [수정] 주문 전송 재시도 루프
        for attempt in range(2):
            try:
                is_retry = (attempt > 0)
                token = token_manager.get_access_token(force_refresh=is_retry)
                
                if not token:
                    logger.error("❌ 매도 주문: 토큰 없음")
                    if is_retry: return False
                    continue
                
                headers = {
                    "Content-Type": "application/json; charset=utf-8",
                    "authorization": f"Bearer {token}",
                    "appkey": config['api_key'],
                    "appsecret": config['api_secret'],
                    "tr_id": "TTTT1006U",
                    "custtype": "P",
                    "hashkey": hashkey
                }
                
                logger.info(f"""
    📤 [매도 주문 전송] 호출 준비 - 시도 {attempt+1}
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    종목: {ticker}
    수량: {execution_data['quantity']}주
    가격: ${sell_price}
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                """)
                
                response = requests.post(sell_url, headers=headers, json=order_data, timeout=10)
                logger.info(f"📥 [매도 주문 응답] 수신 | 상태코드: {response.status_code}")
                
                if response.status_code == 200:
                    try:
                        result = response.json()
                    except:
                        if attempt == 1: return False
                        continue

                    # [수정] 토큰 만료 체크
                    if result.get('msg_cd') == 'EGW00123':
                        logger.warning("⚠️ [매도주문] 토큰 만료. 갱신 후 재시도")
                        continue

                    rt_cd = result.get("rt_cd", "")
                    if rt_cd == "0":
                        order_no = result.get("output", {}).get("ODNO", "")
                        logger.info(f"✅ 매도 주문 접수 성공: {ticker} (주문번호: {order_no})")
            
                        if telegram_bot:
                            try:
                                message = f"📝 매도 주문 접수\n\n📊 종목: {ticker}\n💰 주문가: ${sell_price}\n📝 주문번호: {order_no}"
                                telegram_bot.send_message(message)
                            except: pass
            
                        return {'success': True, 'order_no': order_no, 'ticker': ticker, 'quantity': execution_data['quantity'], 'price': sell_price}

                    else:
                        msg1 = result.get("msg1", "알 수 없는 오류")
                        logger.error(f"❌ 매도 주문 실패: {msg1} (rt_cd={rt_cd})")
                        if "가능수량" in msg1: return False
                        if attempt == 1:
                            if telegram_bot: telegram_bot.send_error_notification(f"매도 주문 실패: {msg1}")
                            return False
                        # 그 외 에러는 재시도 없이 종료할지 고민되지만, 일단 continue 말고 종료
                        return False
                else:
                    logger.error(f"❌ 매도 주문 HTTP 오류: {response.status_code}")
                    if attempt == 1: return False

            except Exception as e:
                logger.error(f"❌ 매도 주문 실행 중 오류: {e}")
                if attempt == 1: return False
    
    except Exception as e:
        logger.error(f"❌ 매도 주문 준비 중 오류: {e}")
        return False
    
    return False


# ============================================================================
# 🆕 [v2.0] 텔레그램 주문용 헬퍼 함수
# ============================================================================

def get_current_price(config, token_manager, ticker):
    """
    실시간 시세 조회 (토큰 갱신 추가)
    """
    url = f"{config['api']['base_url']}/uapi/overseas-price/v1/quotations/price"
    
    for attempt in range(2):
        try:
            is_retry = (attempt > 0)
            token = token_manager.get_access_token(force_refresh=is_retry)
            if not token: continue
            
            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": config["api_key"],
                "appsecret": config["api_secret"],
                "tr_id": "HHDFS00000300",
                "custtype": "P"
            }
            
            params = {"AUTH": "", "EXCD": "NAS", "SYMB": ticker}
            
            response = requests.get(url, headers=headers, params=params, timeout=10)
            
            if response.status_code != 200:
                continue
            
            data = response.json()
            
            if data.get('msg_cd') == 'EGW00123':
                logger.warning("⚠️ [시세조회] 토큰 만료. 재시도")
                continue
            
            rt_cd = data.get("rt_cd", "")
            if rt_cd == "0":
                price = float(data.get("output", {}).get("last", "0"))
                if price > 0: return price
            else:
                logger.warning(f"⚠️ 시세 조회 실패: {data.get('msg1')}")
                
        except Exception as e:
            logger.error(f"❌ 시세 조회 오류: {e}")
            
    return None

@log_api_call('가용 자금 조회', 'TTTS3007R')
def get_available_funds(config, token_manager):
    """
    가용 매수 금액 조회 (토큰 갱신 추가)
    """
    url = f"{config['api']['base_url']}/uapi/overseas-stock/v1/trading/inquire-balance"
    
    for attempt in range(2):
        try:
            is_retry = (attempt > 0)
            token = token_manager.get_access_token(force_refresh=is_retry)
            if not token: continue
            
            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": config["api_key"],
                "appsecret": config["api_secret"],
                "tr_id": "TTTS3007R",
                "custtype": "P"
            }
            
            params = {
                "CANO": config["cano"],
                "ACNT_PRDT_CD": config["acnt_prdt_cd"],
                "OVRS_EXCG_CD": "NASD",
                "TR_CRCY_CD": "USD",
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": ""
            }
            
            logger.info(f"💰 [가용 자금 조회] 요청 전송 중... (시도 {attempt+1})")
            
            response = requests.get(url, headers=headers, params=params, timeout=15)
            logger.info(f"📥 [가용 자금 조회] 응답 수신 | 상태코드: {response.status_code}")
            
            if response.status_code != 200:
                continue
            
            data = response.json()
            
            if data.get('msg_cd') == 'EGW00123':
                logger.warning("⚠️ [자금조회] 토큰 만료. 갱신 후 재시도")
                continue
            
            if data.get("rt_cd") == "0":
                val = float(data.get("output1", {}).get("frcr_dncl_amt_2", "0"))
                logger.info(f"✅ 가용 자금: ${val:.2f}")
                return val
            
        except Exception as e:
            logger.error(f"❌ 잔고 조회 오류: {e}")
            
    return 0.0

@log_api_call('매수 주문', 'TTTT1002U')
def place_buy_order(config, token_manager, ticker, quantity, price):
    """
    매수 주문 실행 (토큰 갱신 추가)
    """
    try:
        logger.info(f"🎯 매수 주문 준비: {ticker} {quantity}주 @ ${price:.2f}")
        
        exchange_code = 'NASD'
        if ticker in ['AAPL', 'MSFT', 'GOOGL', 'TSLA', 'NVDA', 'AMZN', 'META']:
            exchange_code = 'NASD'
        
        order_data = {
            "CANO": config['cano'],
            "ACNT_PRDT_CD": config['acnt_prdt_cd'],
            "OVRS_EXCG_CD": exchange_code,
            "PDNO": ticker,
            "ORD_QTY": str(quantity),
            "OVRS_ORD_UNPR": str(price),
            "CTAC_TLNO": "",
            "MGCO_APTM_ODNO": "",
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00"
        }
        
        hashkey = get_hash_key(config, token_manager, order_data)
        if not hashkey: return {'success': False, 'error': 'HashKey 생성 실패'}
        
        buy_url = f"{config['api']['base_url']}/uapi/overseas-stock/v1/trading/order"
        
        for attempt in range(2):
            try:
                is_retry = (attempt > 0)
                token = token_manager.get_access_token(force_refresh=is_retry)
                if not token: continue
                
                headers = {
                    "Content-Type": "application/json; charset=utf-8",
                    "authorization": f"Bearer {token}",
                    "appkey": config['api_key'],
                    "appsecret": config['api_secret'],
                    "tr_id": "TTTT1002U",
                    "custtype": "P",
                    "hashkey": hashkey
                }
                
                logger.info(f"""
    📤 [매수 주문 전송] 호출 준비 - 시도 {attempt+1}
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    종목: {ticker}
    수량: {quantity}주
    가격: ${price:.2f}
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                """)
                
                response = requests.post(buy_url, headers=headers, json=order_data, timeout=10)
                logger.info(f"📥 [매수 주문 응답] 수신 | 상태코드: {response.status_code}")

                if response.status_code == 200:
                    data = response.json()
                    
                    if data.get('msg_cd') == 'EGW00123':
                        logger.warning("⚠️ [매수주문] 토큰 만료. 갱신 후 재시도")
                        continue
                    
                    if data.get("rt_cd") == "0":
                        order_no = data.get("output", {}).get("ODNO", "")
                        logger.info(f"✅ 매수 주문 접수: {ticker} (주문번호: {order_no})")
                        return {'success': True, 'order_no': order_no, 'ticker': ticker, 'quantity': quantity, 'price': price}
                    else:
                        msg1 = data.get("msg1", "알 수 없는 오류")
                        logger.error(f"❌ 매수 주문 실패: {msg1}")
                        return {'success': False, 'error': msg1}
                
            except Exception as e:
                logger.error(f"❌ 매수 주문 시도 중 오류: {e}")
                if attempt == 1: return {'success': False, 'error': str(e)}

    except Exception as e:
        logger.error(f"❌ 매수 주문 실행 중 오류: {e}")
        return {'success': False, 'error': str(e)}
    
    return {'success': False, 'error': 'Unknown'}


# ============================================================================
# 주문 모니터링 클래스 (프리마켓용 REST 폴링)
# ============================================================================

class OrderMonitor:
    """
    기획서 3장: 프리마켓 REST 폴링 모니터
    """
    
    def __init__(self, config, token_manager, telegram_bot=None):
        self.config = config
        self.token_manager = token_manager
        self.telegram_bot = telegram_bot
        self.monitoring_orders = {}
        self.is_running = False
        self.monitor_thread = None

    def add_order_to_monitor(self, order_no, ticker, quantity, buy_price):
        order_info = {
            'ticker': ticker, 'quantity': quantity, 'buy_price': buy_price,
            'created_at': datetime.now(), 'attempts': 0, 'max_attempts': 360
        }
        self.monitoring_orders[order_no] = order_info
        logger.info(f"📝 주문 모니터링 등록: {order_no} ({ticker} {quantity}주 @ ${buy_price})")

    def check_order_status(self, order_no):
        """
        주문체결 확인 (토큰 갱신 추가)
        """
        try:
            url = f"{self.config['api']['base_url']}/uapi/overseas-stock/v1/trading/inquire-ccnl"
            today = datetime.now().strftime("%Y%m%d")
            
            for attempt in range(2):
                try:
                    is_retry = (attempt > 0)
                    token = self.token_manager.get_access_token(force_refresh=is_retry)
                    if not token: continue
        
                    headers = {
                        "Content-Type": "application/json",
                        "authorization": f"Bearer {token}",
                        "appkey": self.config["api_key"],
                        "appsecret": self.config["api_secret"],
                        "tr_id": "TTTS3035R",
                        "custtype": "P"
                    }
                
                    params = {
                        "CANO": self.config["cano"], "ACNT_PRDT_CD": self.config["acnt_prdt_cd"],
                        "PDNO": "", "ORD_STRT_DT": today, "ORD_END_DT": today,
                        "SLL_BUY_DVSN": "02", "CCLD_NCCS_DVSN": "01", "OVRS_EXCG_CD": "NASD",
                        "SORT_SQN": "DS", "ORD_DT": "", "ORD_GNO_BRNO": "", "ODNO": "",
                        "CTX_AREA_NK200": "", "CTX_AREA_FK200": ""
                    }
                
                    response = requests.get(url, headers=headers, params=params, timeout=10)
                    if response.status_code != 200: continue
                
                    data = response.json()
                    
                    if data.get('msg_cd') == 'EGW00123':
                        logger.warning("⚠️ [주문체결확인] 토큰 만료. 재시도")
                        continue
                
                    if data.get("rt_cd") != "0": continue
                
                    orders = data.get("output", [])
                    for order in orders:
                        if order.get("odno") == order_no:
                            ccld_qty = order.get("ft_ccld_qty", "0")
                            ccld_unpr = order.get("ft_ccld_unpr3", "0")
                            logger.debug(f"🔍 주문 발견: {order_no} - 체결량: {ccld_qty}")
                            return {
                                'status': '02',
                                'filled_qty': int(ccld_qty) if ccld_qty else 0,
                                'filled_price': float(ccld_unpr) if ccld_unpr else 0.0,
                                'order_data': order
                            }
                    return None

                except Exception:
                    if attempt == 1: return None
            
            return None
    
        except Exception as e:
            logger.error(f"❌ check_order_status() 오류: {e}")
            return None
            
    def execute_auto_sell(self, order_info, filled_price):
        try:
            execution_data = {'ticker': order_info['ticker'], 'quantity': order_info['quantity'], 'price': filled_price}
            logger.info(f"🎯 체결 감지: {execution_data['ticker']} ${filled_price}")
            result = place_sell_order(self.config, self.token_manager, execution_data, self.telegram_bot)
            return result.get('success', False) if isinstance(result, dict) else False
        except Exception as e:
            logger.error(f"❌ 자동 매도 실행 중 오류: {e}")
            return False

    def monitor_orders(self):
        logger.info("🔍 주문 모니터링 시작 (프리마켓 REST 폴링)")
        while self.is_running:
            try:
                if not should_system_run():
                    logger.info("🌙 시스템 운영 시간 종료, 모니터링 중지")
                    self.stop()
                    break
                
                orders_to_check = dict(self.monitoring_orders)
                completed_orders = []
                
                for order_no, order_info in orders_to_check.items():
                    if not self.is_running: break
                    
                    order_info['attempts'] += 1
                    if order_info['attempts'] > order_info['max_attempts']:
                        logger.warning(f"⏰ 주문 모니터링 시간 초과: {order_no}")
                        completed_orders.append(order_no)
                        continue
                    
                    status_info = self.check_order_status(order_no)
                    
                    if status_info and status_info['filled_qty'] > 0:
                        logger.info(f"🎉 체결 완료: {order_no}")
                        self.execute_auto_sell(order_info, status_info['filled_price'])
                        completed_orders.append(order_no)
                
                for order_no in completed_orders:
                    self.monitoring_orders.pop(order_no, None)
                
                if self.is_running: time.sleep(5)
            
            except Exception as e:
                logger.error(f"❌ 주문 모니터링 루프 오류: {e}")
                time.sleep(10)

    def start(self):
        if self.is_running: return
        self.is_running = True
        self.monitor_thread = threading.Thread(target=self.monitor_orders, daemon=True)
        self.monitor_thread.start()
        logger.info("🚀 주문 모니터링 시작됨")

    def stop(self):
        if not self.is_running: return
        self.is_running = False
        if self.monitor_thread: self.monitor_thread.join(timeout=5)
        logger.info("🛑 주문 모니터링 중지됨")

    def get_monitoring_count(self): return len(self.monitoring_orders)
    def get_active_orders(self): return list(self.monitoring_orders.values())
    def clear_old_orders(self): pass


# ============================================================================
# 🆕 [v3.0] OrderExecutor 클래스 - 완전 자동매매 지원
# ============================================================================

class OrderExecutor:
    """
    주문 실행 및 관리 (v3.0 완전 자동매매 지원)
    """
    
    def __init__(self, config, token_manager, telegram_bot, auto_trader=None):
        self.config = config
        self.token_manager = token_manager
        self.telegram_bot = telegram_bot
        self.auto_trader = auto_trader
        self.base_url = config['api']['base_url']
        self.timeout = config['api'].get('request_timeout', 10)
        
        if 'auto_trader' in config:
            self.stop_loss = config['auto_trader'].get('stop_loss', -2.0)
            self.take_profit = config['auto_trader'].get('take_profit', 6.0)
        else:
            self.stop_loss = -2.0
            self.take_profit = 6.0
            
        logger.info("🤖 OrderExecutor 초기화 완료")
    
    def place_fullsize_buy(self, ticker: str) -> Dict[str, Any]:
        """전액 매수"""
        try:
            logger.info(f"💰 {ticker} 전액 매수 시작")
            current_price = self.get_current_price(ticker)
            if not current_price: return {'success': False, 'reason': 'price_fetch_failed'}
            
            available_cash = self.get_available_cash()
            if available_cash < 100: return {'success': False, 'reason': 'insufficient_funds'}
            
            quantity = int(available_cash / current_price)
            if quantity < 1: return {'success': False, 'reason': 'insufficient_quantity'}
            
            logger.info(f"💰 전액 매수 준비: 가용자금 ${available_cash:.2f}, 수량 {quantity}주")
            result = self._place_market_buy_order(ticker, quantity)
            
            if result['success']:
                logger.info(f"✅ 전액 매수 성공: {ticker} {quantity}주")
                self.telegram_bot.send_message(f"💰 전액 매수 체결\n종목: {ticker}\n수량: {quantity}주\n가격: ${current_price:.2f}")
                return {'success': True, 'order_no': result['order_no'], 'quantity': quantity, 'price': current_price}
            else:
                return result
        except Exception as e:
            logger.error(f"❌ 전액 매수 오류: {e}")
            return {'success': False, 'reason': str(e)}
        
    @log_api_call('Executor 가용 자금 조회', 'TTTS3012R')
    def get_available_cash(self) -> float:
        return get_available_funds(self.config, self.token_manager)
    
    def _place_market_buy_order(self, ticker: str, quantity: int) -> Dict[str, Any]:
        """시장가 매수 (토큰 갱신 추가)"""
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/order"
        
        for attempt in range(2):
            try:
                is_retry = (attempt > 0)
                token = self.token_manager.get_access_token(force_refresh=is_retry)
                if not token: continue
                
                headers = {
                    'content-type': 'application/json; charset=utf-8',
                    'authorization': f'Bearer {token}',
                    'appkey': self.config['api_key'],
                    'appsecret': self.config['api_secret'],
                    'tr_id': 'JTTT1002U',
                    'custtype': 'P'
                }
                
                body = {
                    'CANO': self.config['cano'],
                    'ACNT_PRDT_CD': self.config['acnt_prdt_cd'],
                    'OVRS_EXCG_CD': 'NASD',
                    'PDNO': ticker,
                    'ORD_DVSN': '00',
                    'ORD_QTY': str(quantity),
                    'OVRS_ORD_UNPR': '0'
                }
                
                response = requests.post(url, headers=headers, json=body, timeout=self.timeout)
                data = response.json()
                
                if data.get('msg_cd') == 'EGW00123':
                    continue
                
                if data.get('rt_cd') == '0':
                    return {'success': True, 'order_no': data.get('output', {}).get('ODNO', '')}
                else:
                    logger.error(f"❌ 매수 실패: {data.get('msg1')}")
                    return {'success': False, 'reason': data.get('msg1')}
                    
            except Exception as e:
                logger.error(f"❌ 매수 오류: {e}")
                if attempt == 1: return {'success': False, 'reason': str(e)}
                
        return {'success': False, 'reason': 'Unknown'}
        
    def place_limit_buy_order(self, ticker: str, limit_price: float, quantity: Optional[int] = None) -> Dict[str, Any]:
        """지정가 매수 (토큰 갱신 추가)"""
        try:
            if quantity is None:
                available_cash = self.get_available_cash()
                quantity = int(available_cash / limit_price)
            
            price_str = f"{limit_price:.2f}" if limit_price >= 1 else f"{limit_price:.4f}"
            url = f"{self.base_url}/uapi/overseas-stock/v1/trading/order"
            
            for attempt in range(2):
                is_retry = (attempt > 0)
                token = self.token_manager.get_access_token(force_refresh=is_retry)
                if not token: continue
                
                headers = {
                    'content-type': 'application/json; charset=utf-8',
                    'authorization': f'Bearer {token}',
                    'appkey': self.config['api_key'],
                    'appsecret': self.config['api_secret'],
                    'tr_id': 'JTTT1002U',
                    'custtype': 'P'
                }
                
                body = {
                    'CANO': self.config['cano'], 'ACNT_PRDT_CD': self.config['acnt_prdt_cd'],
                    'OVRS_EXCG_CD': 'NASD', 'PDNO': ticker, 'ORD_DVSN': '00',
                    'ORD_QTY': str(quantity), 'OVRS_ORD_UNPR': price_str
                }
                
                response = requests.post(url, headers=headers, json=body, timeout=self.timeout)
                data = response.json()
                
                if data.get('msg_cd') == 'EGW00123': continue
                
                if data.get('rt_cd') == '0':
                    order_no = data.get('output', {}).get('ODNO', '')
                    logger.info(f"✅ 지정가 매수 성공: {order_no}")
                    return {'success': True, 'order_no': order_no, 'quantity': quantity, 'price': limit_price}
                
        except Exception as e:
            logger.error(f"❌ 지정가 매수 오류: {e}")
            
        return {'success': False, 'reason': 'Fail'}
        
    def get_1min_candles(self, ticker: str, count: int) -> List[Dict]:
        """1분봉 조회 (토큰 갱신 추가)"""
        url = f"{self.base_url}/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
        
        for attempt in range(2):
            try:
                is_retry = (attempt > 0)
                token = self.token_manager.get_access_token(force_refresh=is_retry)
                if not token: continue
                
                headers = {
                    'content-type': 'application/json; charset=utf-8',
                    'authorization': f'Bearer {token}',
                    'appkey': self.config['api_key'],
                    'appsecret': self.config['api_secret'],
                    'tr_id': 'HHDFS76950200', 'custtype': 'P'
                }
                
                params = {
                    'AUTH': '', 'EXCD': 'NAS', 'SYMB': ticker,
                    'NMIN': '1', 'PINC': '1', 'NEXT': '',
                    'NREC': str(min(count, 120)), 'FILL': '', 'KEYB': ''
                }
                
                response = requests.get(url, headers=headers, params=params, timeout=self.timeout)
                data = response.json()
                
                if data.get('msg_cd') == 'EGW00123':
                    logger.warning(f"⚠️ [1분봉] 토큰 만료. 갱신 후 재시도 ({ticker})")
                    continue
                
                if data.get('rt_cd') != '0': return []
                
                candles = []
                for item in data.get('output2', []):
                    candles.append({
                        'time': item['xhms'],
                        'open': float(item['open']),
                        'high': float(item['high']),
                        'low': float(item['low']),
                        'close': float(item['last']),
                        'volume': int(item['evol'])
                    })
                return candles
                
            except Exception:
                if attempt == 1: return []
        return []
    
    def get_current_price(self, ticker: str) -> Optional[float]:
        return get_current_price(self.config, self.token_manager, ticker)
    
    def check_exit_conditions(self, order: Dict[str, Any]) -> bool:
        """손절/익절 체크"""
        current_price = self.get_current_price(order['ticker'])
        if not current_price: return False
        
        buy_price = order['buy_price']
        pct = (current_price - buy_price) / buy_price * 100
        
        if pct <= self.stop_loss:
            logger.warning(f"🛑 손절: {order['ticker']} ({pct:.2f}%)")
            self.execute_sell(order, 'stop_loss')
            return True
            
        if pct >= self.take_profit:
            logger.info(f"🎯 익절: {order['ticker']} (+{pct:.2f}%)")
            self.execute_sell(order, 'take_profit')
            return True
        return False
    
    @log_api_call('자동 청산 매도', 'JTTT1006U')
    def execute_sell(self, order: Dict[str, Any], reason: str = 'take_profit') -> Dict[str, Any]:
        """매도 주문 실행 (토큰 갱신 추가)"""
        ticker = order['ticker']
        quantity = order['quantity']
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/order"
        
        for attempt in range(2):
            try:
                is_retry = (attempt > 0)
                token = self.token_manager.get_access_token(force_refresh=is_retry)
                if not token: continue
                
                headers = {
                    'content-type': 'application/json; charset=utf-8',
                    'authorization': f'Bearer {token}',
                    'appkey': self.config['api_key'],
                    'appsecret': self.config['api_secret'],
                    'tr_id': 'JTTT1006U', 'custtype': 'P'
                }
                
                body = {
                    'CANO': self.config['cano'], 'ACNT_PRDT_CD': self.config['acnt_prdt_cd'],
                    'OVRS_EXCG_CD': 'NASD', 'PDNO': ticker, 'ORD_DVSN': '00',
                    'ORD_QTY': str(quantity), 'OVRS_ORD_UNPR': '0'
                }
                
                response = requests.post(url, headers=headers, json=body, timeout=self.timeout)
                data = response.json()
                
                if data.get('msg_cd') == 'EGW00123': continue
                
                if data.get('rt_cd') == '0':
                    reason_text = '손절' if reason == 'stop_loss' else '익절'
                    self.telegram_bot.send_message(f"{'🛑' if reason == 'stop_loss' else '🎯'} {reason_text} 매도 체결\n종목: {ticker}\n수량: {quantity}주")
                    return {'success': True}
                else:
                    logger.error(f"❌ 매도 실패: {data.get('msg1')}")
                    
            except Exception as e:
                logger.error(f"❌ 매도 오류: {e}")
                
        return {'success': False}