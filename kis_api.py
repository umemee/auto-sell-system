import requests
import json
import time
import pandas as pd
from config import Config
from utils import get_logger

logger = get_logger()

class KisApi:
    def __init__(self, token_manager):
        self.tm = token_manager
        self.base_url = Config().BASE_URL
        self.headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": "",
            "appkey": Config.APP_KEY,
            "appsecret": Config.APP_SECRET,
            "tr_id": "",
            "custtype": "P"
        }

    def _update_headers(self, tr_id):
        self.headers["authorization"] = f"Bearer {self.tm.get_token()}"
        self.headers["tr_id"] = tr_id

    def _get_lookup_excd(self, exchange):
        """
        [거래소 코드 변환기]
        주문용 코드(4자리)를 시세조회용 코드(3자리)로 변환
        NASD -> NAS, NYSE -> NYS, AMEX -> AMS
        """
        excd_map = {
            "NASD": "NAS",
            "NYSE": "NYS",
            "AMEX": "AMS"
        }
        return excd_map.get(exchange, exchange)

    def get_candles(self, exchange, symbol, timeframe, limit=200):
        """차트 데이터(OHLCV) 조회"""
        path = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
        self._update_headers("HHDFS76950200")

        # [중요] 시세 조회용 코드로 변환 (NASD -> NAS)
        lookup_excd = self._get_lookup_excd(exchange)

        nmin = "1"
        if timeframe == "5M": nmin = "5"
        
        params = {
            "AUTH": "",
            "EXCD": lookup_excd, # 변환된 코드 사용
            "SYMB": symbol,
            "NMIN": nmin,
            "PINC": "1",
            "NEXT": "",
            "NREC": "120", 
            "KEYB": ""
        }

        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
            res.raise_for_status()
            data = res.json()
            
            if data['rt_cd'] != '0':
                logger.error(f"Candle Fetch Error: {data['msg1']}")
                return pd.DataFrame()

            items = data['output2']
            if not items:
                return pd.DataFrame()

            df = pd.DataFrame(items)
            df = df[['kymd', 'khms', 'open', 'high', 'low', 'last', 'evol']]
            df.columns = ['date', 'time', 'open', 'high', 'low', 'close', 'volume']
            df = df.astype({'open': float, 'high': float, 'low': float, 'close': float, 'volume': int})
            df = df.sort_values(by=['date', 'time']).reset_index(drop=True)
            
            return df

        except Exception as e:
            logger.error(f"Get Candles Failed: {e}")
            return pd.DataFrame()
        finally:
            time.sleep(0.1)

    def get_current_price(self, exchange, symbol):
        """현재가 조회"""
        path = "/uapi/overseas-price/v1/quotations/price"
        self._update_headers("HHDFS00000300")
        
        # [중요] 여기도 변환해야 합니다! (NASD -> NAS)
        lookup_excd = self._get_lookup_excd(exchange)
        
        params = {
            "AUTH": "",
            "EXCD": lookup_excd, # 변환된 코드 사용
            "SYMB": symbol
        }
        
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
            data = res.json()
            if data['rt_cd'] == '0':
                return float(data['output']['last'])
            return 0.0
        except Exception as e:
            logger.error(f"Price Fetch Error: {e}")
            return 0.0

    def place_order(self, exchange, symbol, side, price=0):
        """주문 실행 (매수/매도)"""
        # [중요] 주문 API는 'NASD'(4자리)를 그대로 써야 하므로 변환하지 않습니다.
        
        path = "/uapi/overseas-stock/v1/trading/order"
        
        is_buy = (side == "BUY")
        # 실전 투자용 TR_ID (미국 기준)
        tr_id = "TTTT1002U" if is_buy else "TTTT1006U"
            
        self._update_headers(tr_id)

        # 현재가 조회 (실패 시 0.0 반환)
        current_price = price if price > 0 else self.get_current_price(exchange, symbol)
        if current_price <= 0:
            logger.error("현재가 조회 실패로 주문 불가")
            return None

        qty = 0
        if is_buy:
            # 예산 범위 내 최대 수량 계산
            max_qty = int(Config.TOTAL_BUDGET_USD // current_price)
            if max_qty < 1:
                logger.warning(f"예산 부족: ${Config.TOTAL_BUDGET_USD} / 현재가 ${current_price}")
                return None
            qty = max_qty
        else:
            # 매도 로직은 Strategy에서 수량을 결정해서 호출해야 함
            pass 

        # 실제 주문 전송 (qty가 0이 아니면 전송)
        if qty > 0:
            return self.place_order_final(exchange, symbol, side, qty, current_price)
        return None

    def place_order_final(self, exchange, symbol, side, qty, price):
        """최종 주문 전송"""
        path = "/uapi/overseas-stock/v1/trading/order"
        
        is_buy = (side == "BUY")
        tr_id = "TTTT1002U" if is_buy else "TTTT1006U"
            
        self._update_headers(tr_id)
        
        body = {
            "CANO": Config.CANO,
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "OVRS_EXCG_CD": exchange, # 주문은 NASD 그대로 사용
            "PDNO": symbol,
            "ORD_QTY": str(qty),
            "OVRS_ORD_UNPR": str(price),
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00" # 지정가
        }
        
        try:
            res = requests.post(f"{self.base_url}{path}", headers=self.headers, data=json.dumps(body))
            data = res.json()
            if data['rt_cd'] == '0':
                logger.info(f"[{side}] 주문 성공: {qty}주 @ ${price}")
                return data
            else:
                logger.error(f"주문 실패: {data['msg1']}")
                return None
        except Exception as e:
            logger.error(f"Order Exception: {e}")
            return None