# kis_api.py
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
        excd_map = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}
        return excd_map.get(exchange, exchange)

    def get_ranking(self, sort_type="vol"):
        """해외주식 순위 조회 (404 에러 수정됨)"""
        path = "/uapi/overseas-stock/v1/ranking/fluctuation"
        self._update_headers("HHDFS76410000") 
        
        rank_sort = "2" if sort_type == "vol" else "0"

        # [수정] AUTH 파라미터 제거
        params = {
            "EXCC": "NAS", 
            "GUBN": "0",   
            "QRY_DIV": "0",
            "RANK_SORT": rank_sort 
        }
        
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
            
            # 404 등 에러 발생 시 예외 처리 강화
            if res.status_code != 200:
                logger.warning(f"Ranking API Error: {res.status_code} {res.text[:50]}")
                return []

            data = res.json()
            if data['rt_cd'] != '0':
                logger.error(f"Ranking Fetch Error: {data['msg1']}")
                return []
            
            return data['output']

        except Exception as e:
            logger.error(f"Ranking Fetch Failed: {e}")
            return []

    def get_candles(self, exchange, symbol, timeframe, limit=200):
        path = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
        self._update_headers("HHDFS76950200")
        lookup_excd = self._get_lookup_excd(exchange)
        nmin = "1"
        if timeframe == "5M": nmin = "5"
        
        params = {
            "AUTH": "",
            "EXCD": lookup_excd,
            "SYMB": symbol,
            "NMIN": nmin,
            "PINC": "1",
            "NEXT": "",
            "NREC": "120", 
            "KEYB": ""
        }
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
            data = res.json()
            if data['rt_cd'] != '0': return pd.DataFrame()
            items = data['output2']
            if not items: return pd.DataFrame()
            
            df = pd.DataFrame(items)
            df = df[['kymd', 'khms', 'open', 'high', 'low', 'last', 'evol']]
            df.columns = ['date', 'time', 'open', 'high', 'low', 'close', 'volume']
            df = df.astype({'open': float, 'high': float, 'low': float, 'close': float, 'volume': int})
            df = df.sort_values(by=['date', 'time']).reset_index(drop=True)
            return df
        except:
            return pd.DataFrame()
        finally:
            time.sleep(0.1)

    def get_current_price(self, exchange, symbol):
        path = "/uapi/overseas-price/v1/quotations/price"
        self._update_headers("HHDFS00000300")
        lookup_excd = self._get_lookup_excd(exchange)
        params = {"AUTH": "", "EXCD": lookup_excd, "SYMB": symbol}
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
            data = res.json()
            if data['rt_cd'] == '0': return float(data['output']['last'])
            return 0.0
        except:
            return 0.0

    def place_order_final(self, exchange, symbol, side, qty, price):
        path = "/uapi/overseas-stock/v1/trading/order"
        is_buy = (side == "BUY")
        tr_id = "TTTT1002U" if is_buy else "TTTT1006U"
        self._update_headers(tr_id)
        
        body = {
            "CANO": Config.CANO,
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "OVRS_EXCG_CD": exchange,
            "PDNO": symbol,
            "ORD_QTY": str(qty),
            "OVRS_ORD_UNPR": str(price),
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00"
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