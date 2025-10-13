# order.py

import requests
import json
import logging
import time
from datetime import datetime, time as dtime
from pytz import timezone

logger = logging.getLogger(__name__)

def is_extended_hours(trading_timezone='US/Eastern'):
    """
    미국 동부시간 기준으로
    정규장(09:30–16:00 ET) 외 시간이면 True 반환.
    """
    tz = timezone(trading_timezone)
    now = datetime.now(tz).time()
    regular_start = dtime(9, 30)
    regular_end = dtime(16, 0)
    return not (regular_start <= now <= regular_end)

def place_sell_order_config(token_manager, ticker, quantity, price, config):
    """
    한국투자증권 해외주식 자동 매도 주문.
    - 프리/애프터마켓 자동 판별 → EXT_HOURS_YN 필드 설정
    - 거래소 코드, 주문유형(config) 반영
    """
    max_retries = config['system'].get('order_retry_attempts', 3)
    exch_code = config['trading']['exchange_code']        # e.g. 'NASD'
    default_ord_type = config['trading']['default_order_type']  # '00'(지정가) or '01'(시장가)

    extended = is_extended_hours(config.get('trading_timezone', 'US/Eastern'))

    for attempt in range(1, max_retries + 1):
        try:
            token = token_manager.get_access_token()
            if not token:
                logger.error("Access token unavailable")
                return False

            if not ticker or quantity <= 0 or price <= 0:
                logger.error(f"Invalid parameters: ticker={ticker}, qty={quantity}, price={price}")
                return False

            rounded_price = round(price, 2)

            headers = {
                'Content-Type': 'application/json',
                'authorization': f'Bearer {token}',
                'appKey': config['api']['app_key'],
                'appSecret': config['api']['app_secret'],
                'trId': 'JTTT1002U',
                'custType': config['api']['cust_type'],
            }

            body = {
                'CANO': config['api']['cano'],
                'ACNT_PRDT_CD': config['api']['acnt_prdt_cd'],
                'OVRS_EXCG_CD': exch_code,
                'PDNO': ticker.upper(),
                'ORD_DVSN': default_ord_type,
                'ORD_QTY': str(quantity),
                'OVRS_ORD_UNPR': str(rounded_price),
                'SLL_BUY_DVSN_CD': '01',        # 매도
                'EXT_HOURS_YN': 'Y' if extended else 'N'
            }

            logger.debug(f"sell order request body: {body}")
            resp = requests.post(
                config['api']['url_oversea_sell'],
                headers=headers,
                data=json.dumps(body),
                timeout=10
            )

            logger.debug(f"sell order response: {resp.status_code} {resp.text}")
            resp.raise_for_status()
            data = resp.json()

            if data.get('rt_cd') != '0':
                logger.error(f"Order rejected: {data.get('msg1')}({data.get('msg_cd')})")
                return False

            logger.info(f"Sell order placed: {ticker} qty={quantity} price={rounded_price} "
                        f"{'(extended)' if extended else '(regular)'}")
            return True

        except requests.RequestException as e:
            logger.warning(f"Attempt {attempt}/{max_retries}: request error: {e}")
            time.sleep(1)
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            return False

    logger.error("Exceeded max retries for sell order")
    return False
