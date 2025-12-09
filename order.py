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


# ============================================================================
# 시장 시간 판별 함수 (기획서 2.2, 2.3)
# ============================================================================

def is_market_hours(trading_timezone='US/Eastern'):
    """
    기획서 2.2: 시장 시간 상태 반환
    
    운영 시간 (ET 기준):
    - 프리마켓: 05:00-09:30
    - 정규장: 09:30-12:00
    - 수면 모드: 12:00-05:00 (다음날)
    
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
    정규장(09:30-12:00 ET) 외 시간이면 True 반환
    
    Returns:
        bool: 프리마켓 시간이면 True
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
    
    주말 자동 슬립 모드:
    - 금요일 ET 12:00 종료 → 월요일 ET 05:00 재시작
    - 주말(토, 일) 내내 슬립 모드 유지

    Returns:
        bool: 운영 시간이면 True, 아니면 False
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

def get_holdings(config, token_manager, exchange_code='NASD', currency_code='USD'):
    """
    해외주식 잔고 조회
    
    한국투자증권 공식 API:
    - TR_ID: TTTS3012R (실전) / VTTS3012R (모의)
    - 엔드포인트: /uapi/overseas-stock/v1/trading/inquire-balance
    
    Args:
        config: 설정 딕셔너리
        token_manager: TokenManager 인스턴스
        exchange_code: 거래소 코드 (기본값: 'NASD' 미국전체)
        currency_code: 통화 코드 (기본값: 'USD')
    
    Returns:
        list: 보유 종목 리스트
    """
    try:
        url = f"{config['api']['base_url']}/uapi/overseas-stock/v1/trading/inquire-balance"
        
        token = token_manager.get_access_token()
        if not token:
            logger.error("❌ 보유 종목 조회: 액세스 토큰 없음")
            return []
        
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
        
        logger.info(f"📋 보유 종목 조회 시작: {exchange_code} ({currency_code})")
        
        response = requests.get(url, headers=headers, params=params, timeout=15)
        
        if response.status_code != 200:
            logger.error(f"❌ 보유 종목 조회 HTTP 오류: {response.status_code}")
            logger.error(f"응답: {response.text}")
            return []
        
        data = response.json()
        
        rt_cd = data.get("rt_cd", "")
        if rt_cd != "0":
            msg1 = data.get("msg1", "알 수 없는 오류")
            logger.warning(f"⚠️ 보유 종목 조회 실패: {msg1} (rt_cd={rt_cd})")
            return []
        
        output2 = data.get("output2", [])
        
        if not output2:
            logger.info("📋 보유 종목 없음")
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
                    
                    logger.debug(f"📊 {ticker}: {quantity}주 @ ${avg_price:.2f} "
                               f"(현재가: ${current_price:.2f}, 손익: {profit_rate:+.2f}%)")
            
            except (ValueError, TypeError) as e:
                logger.warning(f"⚠️ 종목 정보 파싱 오류: {e}, 항목: {item}")
                continue
        
        logger.info(f"✅ 보유 종목 조회 완료: {len(holdings)}개")
        
        holdings.sort(key=lambda x: x.get('profit_rate', 0), reverse=True)
        
        return holdings
    
    except requests.exceptions.Timeout:
        logger.error("❌ 보유 종목 조회 타임아웃 (15초)")
        return []
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ 보유 종목 조회 네트워크 오류: {e}")
        return []
    except Exception as e:
        logger.error(f"❌ 보유 종목 조회 오류: {e}")
        import traceback
        traceback.print_exc()
        return []


# ============================================================================
# 주문체결내역 조회 함수
# ============================================================================

def inquire_ccnl(
        config,
        token_manager,
        pdno="",
        ord_strt_dt="",
        ord_end_dt="",
        sll_buy_dvsn="00",
        ccld_nccs_dvsn="00",
        ovrs_excg_cd="%",
        sort_sqn="DS",
        ord_dt="",
        ord_gno_brno="",
        odno=""):
    """
    해외주식 주문체결내역 조회 (한국투자증권 공식 API)

    TR_ID: TTTS3035R (실전투자)
    엔드포인트: /uapi/overseas-stock/v1/trading/inquire-ccnl

    Returns:
        pandas.DataFrame: 체결 내역 또는 None
    """
    try:
        import pandas as pd
        
        url = f"{config['api']['base_url']}/uapi/overseas-stock/v1/trading/inquire-ccnl"

        token = token_manager.get_access_token()
        if not token:
            logger.error("❌ 체결내역 조회: 액세스 토큰 없음")
            return None

        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": config["api_key"],
            "appsecret": config["api_secret"],
            "tr_id": "TTTS3035R",
            "custtype": "P"
        }

        all_output = []
        ctx_area_fk200 = ""
        ctx_area_nk200 = ""

        logger.info(f"📋 체결내역 조회 시작: {ord_strt_dt} ~ {ord_end_dt} (연속 조회)")

        while True:
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
                logger.error(f"응답: {response.text}")
                return None

            data = response.json()

            rt_cd = data.get("rt_cd", "")
            if rt_cd != "0":
                msg1 = data.get("msg1", "알 수 없는 오류")
                logger.warning(f"⚠️ 체결내역 조회 실패: {msg1} (rt_cd={rt_cd})")
                return None

            output_page = data.get("output", [])
            if output_page:
                all_output.extend(output_page)

            tr_cont = data.get("tr_cont", "")
            ctx_area_fk200 = data.get("ctx_area_fk200", "")
            ctx_area_nk200 = data.get("ctx_area_nk200", "")

            if tr_cont in ["F", "M"]:
                logger.debug(f"... 체결내역 연속 조회 중 (tr_cont={tr_cont})")
                time.sleep(0.1)
            else:
                logger.debug("... 체결내역 연속 조회 완료 (마지막 페이지)")
                break

        if not all_output:
            logger.info("📋 체결 내역 없음")
            return pd.DataFrame()

        df = pd.DataFrame(all_output)
        logger.info(f"✅ 체결내역 조회 완료: 총 {len(df)}건 (연속 조회 포함)")

        return df
    
    except ImportError:
        logger.error("❌ 'pandas' 라이브러리가 필요합니다. 'pip install pandas'로 설치해주세요.")
        return None
    except requests.exceptions.Timeout:
        logger.error("❌ 체결내역 조회 타임아웃 (15초)")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ 체결내역 조회 네트워크 오류: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ 체결내역 조회 오류: {e}")
        import traceback
        traceback.print_exc()
        return None


# ============================================================================
# HashKey 생성 함수 (한국투자증권 공식)
# ============================================================================

def get_hash_key(config, token_manager, order_data):
    """
    한국투자증권 주문용 HashKey 생성
    
    Args:
        config: 설정 딕셔너리
        token_manager: TokenManager 인스턴스
        order_data: 주문 데이터 딕셔너리
    
    Returns:
        str: HashKey 또는 None (실패 시)
    """
    try:
        url = f"{config['api']['base_url']}/uapi/hashkey"
        token = token_manager.get_access_token()
        
        if not token:
            logger.error("❌ HashKey 생성: 토큰 없음")
            return None
        
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
                return None
        else:
            logger.error(f"❌ HashKey HTTP 오류 {response.status_code}: {response.text}")
            return None
    
    except Exception as e:
        logger.error(f"❌ HashKey 생성 오류: {e}")
        return None


# ============================================================================
# 자동 매도 주문 실행 함수 (핵심!)
# ============================================================================

def place_sell_order(config, token_manager, execution_data, telegram_bot=None):
    """
    기획서 4장: 자동 매도 주문 실행 함수
    
    Args:
        config: 설정 딕셔너리
        token_manager: TokenManager 인스턴스
        execution_data: 체결 데이터 {'ticker', 'quantity', 'price'}
        telegram_bot: TelegramBot 인스턴스 (선택)
    
    Returns:
        dict: 매도 주문 성공 시 {'success': True, 'order_no': ...}
        bool: 실패 시 False
    """
    try:
        buy_price = execution_data['price']
        
        target_profit_rate = config.get('order_settings', {}).get('target_profit_rate', 6.0)
        profit_margin = target_profit_rate / 100
        
        raw_sell_price = buy_price * (1 + profit_margin)
        
        if raw_sell_price >= 1.0:
            sell_price = round(raw_sell_price, 2)
        else:
            sell_price = round(raw_sell_price, 4)
            
        logger.info(f"🎯 매도 주문 준비: {execution_data['ticker']} "
                   f"{execution_data['quantity']}주 @ ${sell_price} "
                   f"(매수가: ${buy_price}, 목표 수익: +{target_profit_rate}%)")
        
        exchange_code = config.get('order_settings', {}).get('exchange_code', 'NASD')
        
        ticker = execution_data['ticker']
        if ticker in ['AAPL', 'MSFT', 'GOOGL', 'TSLA', 'NVDA', 'AMZN']:
            exchange_code = 'NASD'
        
        logger.debug(f"📊 거래소 코드: {exchange_code}")
        
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

        safe_order_data = order_data.copy()
        if 'CANO' in safe_order_data:
            cano = safe_order_data['CANO']
            safe_order_data['CANO'] = cano[:4] + '****'

        logger.debug(f"📤 주문 데이터: {json.dumps(safe_order_data, ensure_ascii=False)}")
        
        hashkey = get_hash_key(config, token_manager, order_data)

        if not hashkey:
            logger.error("❌ HashKey 생성 실패, 주문 중단")
            if telegram_bot:
                telegram_bot.send_error_notification("매도 주문 실패: HashKey 생성 불가")
            return False
        
        token = token_manager.get_access_token()
        if not token:
            logger.error("❌ 유효한 토큰을 가져올 수 없습니다.")
            if telegram_bot:
                telegram_bot.send_error_notification("매도 주문 실패: 토큰 없음")
            return False
        
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": config['api_key'],
            "appsecret": config['api_secret'],
            "tr_id": "TTTT1006U",
            "custtype": "P",
            "hashkey": hashkey
        }
        
        sell_url = f"{config['api']['base_url']}/uapi/overseas-stock/v1/trading/order"
        
        logger.info(f"📤 매도 주문 전송 중: {ticker} {execution_data['quantity']}주 @ ${sell_price}")
        
        response = requests.post(sell_url, headers=headers, json=order_data, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            rt_cd = result.get("rt_cd", "")
            
            if rt_cd == "0":
                order_no = result.get("output", {}).get("ODNO", "")
                
                logger.info(f"✅ 매도 주문 접수: {ticker} (주문번호: {order_no})")
                
                if telegram_bot:
                    message = f"""📝 매도 주문 접수 (체결 감시 중)

📊 종목: {ticker}
📈 수량: {execution_data['quantity']}주
💰 주문가: ${sell_price}
🎯 목표 수익률: +{target_profit_rate}%
📝 주문번호: {order_no}

⏳ 체결 확인 중... (최대 30분)"""
                    telegram_bot.send_message(message)
                
                return {
                    'success': True,
                    'order_no': order_no,
                    'ticker': ticker,
                    'quantity': execution_data['quantity'],
                    'price': sell_price
                }
            else:
                msg1 = result.get("msg1", "알 수 없는 오류")
                logger.error(f"❌ 매도 주문 실패: {msg1} (rt_cd={rt_cd})")
                
                if "가능수량" in msg1 or "주문수량" in msg1:
                    logger.warning(f"⚠️ 이미 매도된 주식으로 판단, 재시도 없이 무시")
                    return False
                
                if telegram_bot:
                    telegram_bot.send_error_notification(f"매도 주문 실패: {msg1}")
                
                return False
        else:
            logger.error(f"❌ 매도 주문 HTTP 오류: {response.status_code}")
            logger.error(f"응답: {response.text}")
            
            if telegram_bot:
                telegram_bot.send_error_notification(f"매도 주문 HTTP 오류: {response.status_code}")
            
            return False
    
    except Exception as e:
        logger.error(f"❌ 매도 주문 실행 중 오류: {e}")
        
        if telegram_bot:
            telegram_bot.send_error_notification(f"매도 주문 오류: {str(e)}")
        
        return False


# ============================================================================
# 🆕 [v2.0] 텔레그램 주문용 헬퍼 함수
# ============================================================================

def get_current_price(config, token_manager, ticker):
    """
    실시간 시세 조회
    
    Args:
        config: 설정 딕셔너리
        token_manager: TokenManager 인스턴스
        ticker: 종목코드 (예: 'AAPL')
    
    Returns:
        float: 현재가 (USD) 또는 None (실패 시)
    """
    try:
        url = f"{config['api']['base_url']}/uapi/overseas-price/v1/quotations/price"
        
        token = token_manager.get_access_token()
        if not token:
            logger.error("❌ 시세 조회: 액세스 토큰 없음")
            return None
        
        exchange_code = 'NAS'
        if ticker in ['AAPL', 'MSFT', 'GOOGL', 'TSLA', 'NVDA', 'AMZN', 'META']:
            exchange_code = 'NAS'
        
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": config["api_key"],
            "appsecret": config["api_secret"],
            "tr_id": "HHDFS00000300",
            "custtype": "P"
        }
        
        params = {
            "AUTH": "",
            "EXCD": exchange_code,
            "SYMB": ticker
        }
        
        logger.debug(f"📊 시세 조회: {ticker} ({exchange_code})")
        
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        if response.status_code != 200:
            logger.error(f"❌ 시세 조회 HTTP 오류: {response.status_code}")
            return None
        
        data = response.json()
        
        rt_cd = data.get("rt_cd", "")
        if rt_cd != "0":
            msg1 = data.get("msg1", "알 수 없는 오류")
            logger.warning(f"⚠️ 시세 조회 실패: {msg1} (rt_cd={rt_cd})")
            return None
        
        output = data.get("output", {})
        current_price_str = output.get("last", "0")
        
        try:
            current_price = float(current_price_str)
            
            if current_price <= 0:
                logger.warning(f"⚠️ 비정상적인 시세: {ticker} = ${current_price}")
                return None
            
            logger.debug(f"✅ {ticker} 현재가: ${current_price:.2f}")
            return current_price
        
        except (ValueError, TypeError):
            logger.error(f"❌ 시세 파싱 오류: {current_price_str}")
            return None
    
    except requests.exceptions.Timeout:
        logger.error("❌ 시세 조회 타임아웃 (10초)")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ 시세 조회 네트워크 오류: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ 시세 조회 오류: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_available_funds(config, token_manager):
    """
    가용 매수 금액 조회
    
    Args:
        config: 설정 딕셔너리
        token_manager: TokenManager 인스턴스
    
    Returns:
        float: 가용 자금 (USD)
    """
    try:
        url = f"{config['api']['base_url']}/uapi/overseas-stock/v1/trading/inquire-balance"
        
        token = token_manager.get_access_token()
        if not token:
            logger.error("❌ 잔고 조회: 액세스 토큰 없음")
            return 0.0
        
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
        
        logger.debug("💰 가용 자금 조회 중...")
        
        response = requests.get(url, headers=headers, params=params, timeout=15)
        
        if response.status_code != 200:
            logger.error(f"❌ 잔고 조회 HTTP 오류: {response.status_code}")
            return 0.0
        
        data = response.json()
        
        rt_cd = data.get("rt_cd", "")
        if rt_cd != "0":
            msg1 = data.get("msg1", "알 수 없는 오류")
            logger.warning(f"⚠️ 잔고 조회 실패: {msg1} (rt_cd={rt_cd})")
            return 0.0
        
        output1 = data.get("output1", {})
        available_cash_str = output1.get("frcr_dncl_amt_2", "0")
        
        try:
            available_cash = float(available_cash_str)
            
            logger.info(f"✅ 가용 자금: ${available_cash:.2f}")
            return available_cash
        
        except (ValueError, TypeError):
            logger.error(f"❌ 가용 자금 파싱 오류: {available_cash_str}")
            return 0.0
    
    except requests.exceptions.Timeout:
        logger.error("❌ 잔고 조회 타임아웃 (15초)")
        return 0.0
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ 잔고 조회 네트워크 오류: {e}")
        return 0.0
    except Exception as e:
        logger.error(f"❌ 잔고 조회 오류: {e}")
        import traceback
        traceback.print_exc()
        return 0.0


def place_buy_order(config, token_manager, ticker, quantity, price):
    """
    매수 주문 실행
    
    Args:
        config: 설정 딕셔너리
        token_manager: TokenManager 인스턴스
        ticker: 종목코드 (예: 'AAPL')
        quantity: 수량
        price: 지정가 (USD)
    
    Returns:
        dict: {'success': True, 'order_no': '주문번호'}
              또는 {'success': False, 'error': '오류메시지'}
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
        
        logger.debug(f"📤 주문 데이터: {ticker} {quantity}주")
        
        hashkey = get_hash_key(config, token_manager, order_data)
        
        if not hashkey:
            logger.error("❌ HashKey 생성 실패, 주문 중단")
            return {'success': False, 'error': 'HashKey 생성 실패'}
        
        token = token_manager.get_access_token()
        if not token:
            logger.error("❌ 유효한 토큰을 가져올 수 없습니다.")
            return {'success': False, 'error': '토큰 없음'}
        
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": config['api_key'],
            "appsecret": config['api_secret'],
            "tr_id": "TTTT1002U",
            "custtype": "P",
            "hashkey": hashkey
        }
        
        buy_url = f"{config['api']['base_url']}/uapi/overseas-stock/v1/trading/order"
        
        logger.info(f"📤 매수 주문 전송 중: {ticker} {quantity}주 @ ${price:.2f}")
        
        response = requests.post(buy_url, headers=headers, json=order_data, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            rt_cd = result.get("rt_cd", "")
            
            if rt_cd == "0":
                order_no = result.get("output", {}).get("ODNO", "")
                
                logger.info(f"✅ 매수 주문 접수: {ticker} (주문번호: {order_no})")
                
                return {
                    'success': True,
                    'order_no': order_no,
                    'ticker': ticker,
                    'quantity': quantity,
                    'price': price
                }
            else:
                msg1 = result.get("msg1", "알 수 없는 오류")
                logger.error(f"❌ 매수 주문 실패: {msg1} (rt_cd={rt_cd})")
                
                return {
                    'success': False,
                    'error': msg1
                }
        else:
            logger.error(f"❌ 매수 주문 HTTP 오류: {response.status_code}")
            logger.error(f"응답: {response.text}")
            
            return {
                'success': False,
                'error': f"HTTP {response.status_code}"
            }
    
    except Exception as e:
        logger.error(f"❌ 매수 주문 실행 중 오류: {e}")
        import traceback
        traceback.print_exc()
        
        return {
            'success': False,
            'error': str(e)
        }


# ============================================================================
# 주문 모니터링 클래스 (프리마켓용 REST 폴링)
# ============================================================================

class OrderMonitor:
    """
    기획서 3장: 프리마켓 REST 폴링 모니터
    
    정규장은 WebSocket 사용, 프리마켓은 REST 폴링
    """
    
    def __init__(self, config, token_manager, telegram_bot=None):
        self.config = config
        self.token_manager = token_manager
        self.telegram_bot = telegram_bot
        self.monitoring_orders = {}
        self.is_running = False
        self.monitor_thread = None

    def add_order_to_monitor(self, order_no, ticker, quantity, buy_price):
        """모니터링할 주문 추가"""
        order_info = {
            'ticker': ticker,
            'quantity': quantity,
            'buy_price': buy_price,
            'created_at': datetime.now(),
            'attempts': 0,
            'max_attempts': 360
        }
        
        self.monitoring_orders[order_no] = order_info
        logger.info(f"📝 주문 모니터링 등록: {order_no} ({ticker} {quantity}주 @ ${buy_price})")

    def check_order_status(self, order_no):
        """
        기획서 3장: 해외주식 주문/체결내역 조회 (REST 폴링)
        
        Returns:
            dict: 체결 정보 또는 None
        """
        try:
            url = f"{self.config['api']['base_url']}/uapi/overseas-stock/v1/trading/inquire-ccnl"
        
            token = self.token_manager.get_access_token()
            if not token:
                logger.error("❌ 액세스 토큰 없음")
                return None
    
            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": self.config["api_key"],
                "appsecret": self.config["api_secret"],
                "tr_id": "TTTS3035R",
                "custtype": "P"
            }
        
            today = datetime.now().strftime("%Y%m%d")
        
            params = {
                "CANO": self.config["cano"],
                "ACNT_PRDT_CD": self.config["acnt_prdt_cd"],
                "PDNO": "",
                "ORD_STRT_DT": today,
                "ORD_END_DT": today,
                "SLL_BUY_DVSN": "02",
                "CCLD_NCCS_DVSN": "01",
                "OVRS_EXCG_CD": "NASD",
                "SORT_SQN": "DS",
                "ORD_DT": "",
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "CTX_AREA_NK200": "",
                "CTX_AREA_FK200": ""
            }
        
            response = requests.get(url, headers=headers, params=params, timeout=10)
        
            if response.status_code != 200:
                logger.error(f"❌ 주문조회 HTTP 오류: {response.status_code}")
                return None
        
            data = response.json()
        
            if data.get("rt_cd") != "0":
                logger.warning(f"⚠️ 주문조회 실패: {data.get('msg1', '')}")
                return None
        
            orders = data.get("output", [])
            if not orders:
                return None
        
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
    
        except Exception as e:
            logger.error(f"❌ check_order_status() 오류: {e}")
            return None
            
    def execute_auto_sell(self, order_info, filled_price):
        """기획서 4장: 자동 매도 주문 실행"""
        try:
            execution_data = {
                'ticker': order_info['ticker'],
                'quantity': order_info['quantity'],
                'price': filled_price
            }
            
            logger.info(f"🎯 체결 감지: {execution_data['ticker']} ${filled_price}")
            
            result = place_sell_order(
                self.config,
                self.token_manager,
                execution_data,
                self.telegram_bot
            )
            
            success = False
            if isinstance(result, dict):
                success = result.get('success', False)
            elif isinstance(result, bool):
                success = result
                
            return success
        
        except Exception as e:
            logger.error(f"❌ 자동 매도 실행 중 오류: {e}")
            return False

    def monitor_orders(self):
        """주문 모니터링 메인 루프"""
        logger.info("🔍 주문 모니터링 시작 (프리마켓 REST 폴링)")
        
        while self.is_running:
            try:
                if not should_system_run():
                    logger.info("🌙 시스템 운영 시간 종료 (ET 12:00), 모니터링 중지")
                    self.stop()
                    break
                
                orders_to_check = dict(self.monitoring_orders)
                completed_orders = []
                
                for order_no, order_info in orders_to_check.items():
                    if not self.is_running:
                        break
                    
                    order_info['attempts'] += 1
                    if order_info['attempts'] > order_info['max_attempts']:
                        logger.warning(f"⏰ 주문 모니터링 시간 초과: {order_no}")
                        completed_orders.append(order_no)
                        continue
                    
                    status_info = self.check_order_status(order_no)
                    
                    if status_info is None:
                        continue
                    
                    if status_info['filled_qty'] > 0 and status_info['filled_price'] > 0:
                        logger.info(
                            f"🎉 체결 완료: {order_no} "
                            f"(체결가: ${status_info['filled_price']}, "
                            f"체결량: {status_info['filled_qty']})"
                        )
                        
                        self.execute_auto_sell(order_info, status_info['filled_price'])
                        completed_orders.append(order_no)
                
                for order_no in completed_orders:
                    self.monitoring_orders.pop(order_no, None)
                
                if self.is_running:
                    time.sleep(5)
            
            except Exception as e:
                logger.error(f"❌ 주문 모니터링 루프 오류: {e}")
                time.sleep(10)
        
        logger.info("🛑 주문 모니터링 종료")

    def start(self):
        """모니터링 시작"""
        if self.is_running:
            logger.warning("⚠️ 이미 주문 모니터링이 실행 중입니다.")
            return
        
        self.is_running = True
        self.monitor_thread = threading.Thread(target=self.monitor_orders, daemon=True)
        self.monitor_thread.start()
        logger.info("🚀 주문 모니터링 시작됨 (프리마켓 REST 폴링)")

    def stop(self):
        """모니터링 중지"""
        if not self.is_running:
            return
        
        self.is_running = False
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
        logger.info("🛑 주문 모니터링 중지됨")

    def get_monitoring_count(self):
        """현재 모니터링 중인 주문 수"""
        return len(self.monitoring_orders)

    def get_active_orders(self):
        """활성 주문 목록"""
        return list(self.monitoring_orders.values())

    def clear_old_orders(self):
        """24시간 이상된 주문 정리"""
        cutoff_time = datetime.now() - timedelta(hours=24)
        old_orders = [
            order_no for order_no, order_info in self.monitoring_orders.items()
            if order_info['created_at'] < cutoff_time
        ]
        
        for order_no in old_orders:
            self.monitoring_orders.pop(order_no, None)
            logger.info(f"🗑️ 오래된 주문 제거: {order_no}")


# ============================================================================
# 🆕 [v3.0] OrderExecutor 클래스 - 완전 자동매매 지원
# ============================================================================

class OrderExecutor:
    """
    주문 실행 및 관리 (v3.0 완전 자동매매 지원)
    
    기획서 v3.0 섹션 6.4
    """
    
    def __init__(self, config, token_manager, telegram_bot, auto_trader=None):
        """
        초기화
        
        Args:
            config: 설정 딕셔너리
            token_manager: TokenManager 인스턴스
            telegram_bot: TelegramBot 인스턴스
            auto_trader: AutoTrader 인스턴스 (선택)
        """
        self.config = config
        self.token_manager = token_manager
        self.telegram_bot = telegram_bot
        self.auto_trader = auto_trader
        
        self.base_url = config['api']['base_url']
        self.timeout = config['api'].get('request_timeout', 10)
        
        # v3.0 손절/익절 설정
        if 'auto_trader' in config and config['auto_trader'].get('enabled', False):
            self.stop_loss = config['auto_trader']['stop_loss']
            self.take_profit = config['auto_trader']['take_profit']
        else:
            self.stop_loss = -2.0
            self.take_profit = config['order_settings']['target_profit_rate']
        
        logger.info("🤖 OrderExecutor 초기화 완료")
    
    def place_fullsize_buy(self, ticker: str) -> Dict[str, Any]:
        """
        100% 전액 매수 (완전 자동매매용)
        
        Args:
            ticker: 종목코드 (예: 'AAPL')
        
        Returns:
            dict: {
                'success': bool,
                'order_no': str,
                'quantity': int,
                'price': float,
                'reason': str
            }
        """
        try:
            logger.info(f"💰 {ticker} 전액 매수 시작")
            
            # 1. 현재가 조회
            current_price = self.get_current_price(ticker)
            
            if not current_price or current_price <= 0:
                return {
                    'success': False,
                    'reason': 'price_fetch_failed'
                }
            
            # 2. 전체 가용 자금 조회
            available_cash = self.get_available_cash()
            
            if available_cash < 100:
                logger.error(f"❌ 자금 부족: ${available_cash:.2f}")
                return {
                    'success': False,
                    'reason': 'insufficient_funds',
                    'available': available_cash
                }
            
            # 3. 매수 가능 수량 계산
            quantity = int(available_cash / current_price)
            
            if quantity < 1:
                logger.error(f"❌ 수량 부족: {quantity}주")
                return {
                    'success': False,
                    'reason': 'insufficient_quantity',
                    'quantity': quantity
                }
            
            logger.info(
                f"💰 전액 매수 준비:\n"
                f"  가용자금: ${available_cash:.2f}\n"
                f"  현재가: ${current_price:.2f}\n"
                f"  수량: {quantity}주\n"
                f"  총액: ${current_price * quantity:.2f}"
            )
            
            # 4. 시장가 매수 주문
            result = self._place_market_buy_order(ticker, quantity)
            
            if result['success']:
                logger.info(
                    f"✅ 전액 매수 성공: {ticker} {quantity}주 @ ${current_price:.2f}"
                )
                
                # 텔레그램 알림
                self.telegram_bot.send_message(
                    f"💰 전액 매수 체결\n\n"
                    f"종목: {ticker}\n"
                    f"수량: {quantity}주\n"
                    f"가격: ${current_price:.2f}\n"
                    f"총액: ${current_price * quantity:.2f}\n\n"
                    f"손절가: ${current_price * (1 + self.stop_loss/100):.2f} ({self.stop_loss}%)\n"
                    f"익절가: ${current_price * (1 + self.take_profit/100):.2f} (+{self.take_profit}%)"
                )
                
                return {
                    'success': True,
                    'order_no': result['order_no'],
                    'quantity': quantity,
                    'price': current_price
                }
            else:
                logger.error(f"❌ 매수 주문 실패: {result.get('reason')}")
                return result
        
        except Exception as e:
            logger.error(f"❌ 전액 매수 오류: {e}")
            return {
                'success': False,
                'reason': str(e)
            }
    
    def get_available_cash(self) -> float:
        """
        가용 현금 조회
        
        Returns:
            float: 가용 현금 (USD)
        """
        try:
            url = f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-balance"
            
            token = self.token_manager.get_access_token()
            if not token:
                logger.error("❌ 토큰 없음")
                return 0.0
            
            headers = {
                'content-type': 'application/json; charset=utf-8',
                'authorization': f'Bearer {token}',
                'appkey': self.config['api_key'],
                'appsecret': self.config['api_secret'],
                'tr_id': 'CTRP6548R',
                'custtype': 'P'
            }
            
            params = {
                'CANO': self.config['cano'],
                'ACNT_PRDT_CD': self.config['acnt_prdt_cd'],
                'OVRS_EXCG_CD': 'NASD',
                'TR_CRCY_CD': 'USD',
                'CTX_AREA_FK200': '',
                'CTX_AREA_NK200': ''
            }
            
            response = requests.get(url, headers=headers, params=params, timeout=self.timeout)
            data = response.json()
            
            if data.get('rt_cd') == '0':
                cash = float(data.get('output3', {}).get('frcr_dncl_amt_2', '0'))
                logger.debug(f"💵 가용 현금: ${cash:.2f}")
                return cash
            else:
                logger.error(f"❌ 잔고 조회 실패: {data.get('msg1')}")
                return 0.0
        
        except Exception as e:
            logger.error(f"❌ 가용 현금 조회 오류: {e}")
            return 0.0
    
    def _place_market_buy_order(self, ticker: str, quantity: int) -> Dict[str, Any]:
        """
        시장가 매수 주문 실행
        
        Args:
            ticker: 종목코드
            quantity: 수량
        
        Returns:
            dict: {'success': bool, 'order_no': str, ...}
        """
        try:
            url = f"{self.base_url}/uapi/overseas-stock/v1/trading/order"
            
            token = self.token_manager.get_access_token()
            if not token:
                return {'success': False, 'reason': 'no_token'}
            
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
            
            if data.get('rt_cd') == '0':
                order_no = data.get('output', {}).get('ODNO', '')
                logger.info(f"✅ 매수 주문 성공: {order_no}")
                
                return {
                    'success': True,
                    'order_no': order_no
                }
            else:
                logger.error(f"❌ 매수 주문 실패: {data.get('msg1')}")
                return {
                    'success': False,
                    'reason': data.get('msg1')
                }
        
        except Exception as e:
            logger.error(f"❌ 매수 주문 오류: {e}")
            return {
                'success': False,
                'reason': str(e)
            }
        
    def place_limit_buy_order(self, ticker: str, limit_price: float, quantity: Optional[int] = None) -> Dict[str, Any]:
        """
        지정가 매수 주문 (선행 주문용)
        
        Args:
            ticker: 종목코드
            limit_price: 지정가 (예: 174.50)
            quantity: 수량 (None이면 전액)
        
        Returns:
            dict: {'success': bool, 'order_no': str, 'reason': str}
        """
        try:
            logger.info(f"📝 {ticker} 지정가 매수: ${limit_price:.2f}")
            
            # 1. 수량 계산 (전액)
            if quantity is None:
                available_cash = self.get_available_cash()
                
                if available_cash < 100:
                    logger.error(f"❌ 자금 부족: ${available_cash:.2f}")
                    return {'success': False, 'reason': 'insufficient_funds'}
                
                quantity = int(available_cash / limit_price)
                
                if quantity < 1:
                    logger.error(f"❌ 수량 부족: {quantity}주")
                    return {'success': False, 'reason': 'insufficient_quantity'}
            
            # 2. 가격 포맷팅
            if limit_price >= 1.0:
                price_str = f"{limit_price:.2f}"
            else:
                price_str = f"{limit_price:.4f}"
            
            logger.info(
                f"📝 지정가 매수 준비:\n"
                f"  종목: {ticker}\n"
                f"  수량: {quantity}주\n"
                f"  지정가: ${price_str}"
            )
            
            # 3. API 호출
            url = f"{self.base_url}/uapi/overseas-stock/v1/trading/order"
            
            token = self.token_manager.get_access_token()
            if not token:
                return {'success': False, 'reason': 'no_token'}
            
            headers = {
                'content-type': 'application/json; charset=utf-8',
                'authorization': f'Bearer {token}',
                'appkey': self.config['api_key'],
                'appsecret': self.config['api_secret'],
                'tr_id': 'JTTT1002U',  # 실전 매수
                'custtype': 'P'
            }
            
            body = {
                'CANO': self.config['cano'],
                'ACNT_PRDT_CD': self.config['acnt_prdt_cd'],
                'OVRS_EXCG_CD': 'NASD',
                'PDNO': ticker,
                'ORD_DVSN': '00',              # 지정가
                'ORD_QTY': str(quantity),
                'OVRS_ORD_UNPR': price_str,    # 실제 가격
                'CTAC_TLNO': '',
                'MGCO_APTM_ODNO': '',
                'SLL_TYPE': '',
                'ORD_SVR_DVSN_CD': '0'
            }
            
            response = requests.post(url, headers=headers, json=body, timeout=self.timeout)
            data = response.json()
            
            if data.get('rt_cd') == '0':
                order_no = data.get('output', {}).get('ODNO', '')
                logger.info(f"✅ 지정가 매수 주문 성공: {order_no}")
                
                # 텔레그램 알림
                self.telegram_bot.send_message(
                    f"📝 지정가 매수 주문 접수\n\n"
                    f"종목: {ticker}\n"
                    f"수량: {quantity}주\n"
                    f"지정가: ${price_str}\n"
                    f"주문번호: {order_no}\n\n"
                    f"⏳ 체결 대기 중..."
                )
                
                return {
                    'success': True,
                    'order_no': order_no,
                    'quantity': quantity,
                    'price': limit_price
                }
            else:
                error_msg = data.get('msg1', 'Unknown error')
                logger.error(f"❌ 지정가 매수 실패: {error_msg}")
                return {
                    'success': False,
                    'reason': error_msg
                }
        
        except Exception as e:
            logger.error(f"❌ 지정가 매수 오류: {e}")
            return {
                'success': False,
                'reason': str(e)
            }
        
    def get_1min_candles(self, ticker: str, count: int) -> List[Dict]:
        """
        1분봉 조회
        
        Args:
            ticker: 종목코드
            count: 조회 개수 (최대 120)
        
        Returns:
            list: [{'time': '093000', 'open': 250.00, 'high': 250.50, 
                    'low': 249.80, 'close': 250.20, 'volume': 125000}, ...]
        """
        try:
            url = f"{self.base_url}/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
            
            token = self.token_manager.get_access_token()
            if not token:
                return []
            
            import os

            headers = {
                'content-type': 'application/json; charset=utf-8',
                'authorization': f'Bearer {token}',
                'appkey': self.config['api_key'],
                'appsecret': self.config['api_secret'],
                'tr_id': 'HHDFS76240000',
                'custtype': 'P'
            }
            
            params = {
                'AUTH': '',
                'EXCD': 'NAS',
                'SYMB': ticker,
                'NMIN': '1',
                'PINC': '1',
                'NEXT': '',
                'NREC': str(min(count, 120)),
                'BYMD': '',         
                'MODP': '1',       
                'FILL': '',
                'KEYB': '',
                'GUBN': '0'
            }
            
            response = requests.get(url, headers=headers, params=params, timeout=self.timeout)
            data = response.json()
            
            if data.get('rt_cd') != '0':
                logger.error(f"❌ 1분봉 조회 실패: {data.get('msg1')}")
                return []
            
            output2 = data.get('output2', [])
            
            candles = []
            for item in output2:
                try:
                    candles.append({
                        'time': item['xhms'],
                        'open': float(item['open']),
                        'high': float(item['high']),
                        'low': float(item['low']),
                        'close': float(item['last']),
                        'volume': int(item['evol'])
                    })
                except (KeyError, ValueError) as e:
                    logger.error(f"❌ 캔들 파싱 오류: {e}")
                    continue
            
            return candles
        
        except Exception as e:
            logger.error(f"❌ 1분봉 조회 오류: {e}")
            return []
    
    def get_current_price(self, ticker: str) -> Optional[float]:
        """
        실시간 현재가 조회
        
        Args:
            ticker: 종목코드
        
        Returns:
            float: 현재가 (실패 시 None)
        """
        try:
            url = f"{self.base_url}/uapi/overseas-price/v1/quotations/price"
            
            token = self.token_manager.get_access_token()
            if not token:
                return None
            
            headers = {
                'content-type': 'application/json; charset=utf-8',
                'authorization': f'Bearer {token}',
                'appkey': self.config['api_key'],
                'appsecret': self.config['api_secret'],
                'tr_id': 'HHDFS00000300',
                'custtype': 'P'
            }
            
            params = {
                'AUTH': '',
                'EXCD': 'NAS',
                'SYMB': ticker
            }
            
            response = requests.get(url, headers=headers, params=params, timeout=self.timeout)
            data = response.json()
            
            if data.get('rt_cd') == '0':
                price = float(data.get('output', {}).get('last', '0'))
                return price if price > 0 else None
            else:
                logger.error(f"❌ 현재가 조회 실패: {data.get('msg1')}")
                return None
        
        except Exception as e:
            logger.error(f"❌ 현재가 조회 오류: {e}")
            return None
    
    def check_exit_conditions(self, order: Dict[str, Any]) -> bool:
        """
        손절/익절 조건 체크
        
        ⚠️ v3.0 수정: 손절 우선 체크
        
        Args:
            order: 주문 정보
        
        Returns:
            bool: 청산 완료 시 True
        """
        current_price = self.get_current_price(order['ticker'])
        
        if not current_price:
            return False
        
        buy_price = order['buy_price']
        
        # 손절 체크 (우선)
        loss_pct = (current_price - buy_price) / buy_price * 100
        
        if loss_pct <= self.stop_loss:
            logger.warning(
                f"🛑 손절 조건 도달: {order['ticker']}\n"
                f"  매수가: ${buy_price:.2f}\n"
                f"  현재가: ${current_price:.2f}\n"
                f"  손실: {loss_pct:.2f}%"
            )
            
            result = self.place_sell_order(order, reason='stop_loss')
            
            if result['success']:
                if self.auto_trader:
                    self.auto_trader.on_exit_complete(order['ticker'], 'stop_loss')
                
                return True
            
            return False
        
        # 익절 체크
        profit_pct = (current_price - buy_price) / buy_price * 100
        
        if profit_pct >= self.take_profit:
            logger.info(
                f"🎯 익절 조건 도달: {order['ticker']}\n"
                f"  매수가: ${buy_price:.2f}\n"
                f"  현재가: ${current_price:.2f}\n"
                f"  수익: {profit_pct:.2f}%"
            )
            
            result = self.place_sell_order(order, reason='take_profit')
            
            if result['success']:
                if self.auto_trader:
                    self.auto_trader.on_exit_complete(order['ticker'], 'take_profit')
                
                return True
            
            return False
        
        return False
    
    def place_sell_order(self, order: Dict[str, Any], reason: str = 'take_profit') -> Dict[str, Any]:
        """
        매도 주문 실행
        
        Args:
            order: 주문 정보
            reason: 매도 사유 ('stop_loss' 또는 'take_profit')
        
        Returns:
            dict: {'success': bool, ...}
        """
        try:
            ticker = order['ticker']
            quantity = order['quantity']
            
            url = f"{self.base_url}/uapi/overseas-stock/v1/trading/order"
            
            token = self.token_manager.get_access_token()
            if not token:
                return {'success': False, 'reason': 'no_token'}
            
            headers = {
                'content-type': 'application/json; charset=utf-8',
                'authorization': f'Bearer {token}',
                'appkey': self.config['api_key'],
                'appsecret': self.config['api_secret'],
                'tr_id': 'JTTT1006U',
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
            
            if data.get('rt_cd') == '0':
                reason_text = '손절' if reason == 'stop_loss' else '익절'
                
                self.telegram_bot.send_message(
                    f"{'🛑' if reason == 'stop_loss' else '🎯'} {reason_text} 매도 체결\n\n"
                    f"종목: {ticker}\n"
                    f"수량: {quantity}주\n"
                    f"사유: {reason_text}"
                )
                
                return {'success': True}
            else:
                logger.error(f"❌ 매도 주문 실패: {data.get('msg1')}")
                return {'success': False, 'reason': data.get('msg1')}
        
        except Exception as e:
            logger.error(f"❌ 매도 주문 오류: {e}")
            return {'success': False, 'reason': str(e)}