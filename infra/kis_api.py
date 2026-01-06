import requests
import json
import pandas as pd
import time
from config import Config
from infra.utils import get_logger

class KisApi:
    def __init__(self, token_manager):
        self.logger = get_logger("KisApi")
        self.token_manager = token_manager
        # [Fix] Config 변수명 일치 (KIS_ 접두어 제거)
        self.base_url = Config.BASE_URL
        self.account_no = Config.CANO
        
    def _get_headers(self, tr_id):
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.token_manager.get_token()}",
            "appkey": Config.APP_KEY,
            "appsecret": Config.APP_SECRET,
            "tr_id": tr_id
        }

    def _send_request(self, method, path, headers, params=None, data=None):
        url = f"{self.base_url}{path}"
        try:
            if method == "GET":
                resp = requests.get(url, headers=headers, params=params)
            else:
                resp = requests.post(url, headers=headers, json=data)
            return resp.json()
        except Exception as e:
            self.logger.error(f"API Request Failed: {e}")
            return None

    def get_current_price(self, market, symbol):
        # [Fix] 공식 문서 '해외주식 현재가상세' 반영
        path = "/uapi/overseas-price/v1/quotations/price-detail"
        headers = self._get_headers("HHDFS76200200")
        params = {"AUTH": "", "EXCD": market, "SYMB": symbol}
        
        res = self._send_request("GET", path, headers, params)
        if res and res.get('output'):
            out = res['output']
            return {
                "symbol": symbol,
                "last": float(out.get('last', 0)),
                "open": float(out.get('open', 0)),
                "high": float(out.get('high', 0)),
                "low": float(out.get('low', 0)),
                "base": float(out.get('base', 0))
            }
        return None

    def get_minute_candles(self, market, symbol, limit=100):
        # [Fix] 공식 문서 '해외주식분봉조회' 반영
        path = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
        headers = self._get_headers("HHDFS76950200")
        
        params = {
            "AUTH": "", "EXCD": market, "SYMB": symbol,
            "NMIN": "1", "PINC": "1", "NEXT": "", "NREC": "120", "KEYB": ""
        }
        res = self._send_request("GET", path, headers, params)
        if res and res.get('output2'):
            df = pd.DataFrame(res['output2'])
            df = df.rename(columns={
                'kymd': 'date', 'khms': 'time',
                'open': 'open', 'high': 'high', 'low': 'low', 'last': 'close', 'vols': 'volume'
            })
            cols = ['open', 'high', 'low', 'close', 'volume']
            df[cols] = df[cols].apply(pd.to_numeric)
            return df.sort_values('time')
        return pd.DataFrame()

    def get_balance(self):
        path = "/uapi/overseas-stock/v1/trading/inquire-balance"
        headers = self._get_headers("TTTS3012R")
        params = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": "01",
            "OVRS_EXCG_CD": "NASD",
            "TR_CRCY_CD": "USD",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""
        }
        res = self._send_request("GET", path, headers, params)
        holdings = []
        if res and 'output1' in res:
            for item in res['output1']:
                qty = float(item.get('ovrs_cblc_qty', 0))
                if qty > 0:
                    holdings.append({"symbol": item['ovrs_pdno'], "qty": int(qty)})
        return holdings

    def get_buyable_cash(self):
        path = "/uapi/overseas-stock/v1/trading/inquire-balance"
        headers = self._get_headers("TTTS3012R")
        params = {
            "CANO": self.account_no, "ACNT_PRDT_CD": "01", 
            "OVRS_EXCG_CD": "NASD", "TR_CRCY_CD": "USD",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""
        }
        res = self._send_request("GET", path, headers, params)
        if res and 'output2' in res:
            return float(res['output2'].get('ovrs_ord_psbl_amt', 0))
        return 0.0

    def buy_limit(self, symbol, price, qty):
        # [Fix] 미국 매수 TR ID (TTTT1002U)
        path = "/uapi/overseas-stock/v1/trading/order"
        headers = self._get_headers("TTTT1002U") 
        
        data = {
            "CANO": self.account_no, "ACNT_PRDT_CD": "01", "OVRS_EXCG_CD": "NASD",
            "PDNO": symbol, "ORD_DVSN": "00", "ORD_QTY": str(qty),
            "ORD_UNPR": str(price), "ORD_SVR_DVSN_CD": "0"
        }
        res = self._send_request("POST", path, headers, data=data)
        if res and res.get('rt_cd') == '0':
            return res['output']['ODNO']
        self.logger.error(f"Buy Failed: {res}")
        return None

    def sell_market(self, symbol, qty):
        # [Fix] 미국 매도 TR ID (TTTT1006U)
        path = "/uapi/overseas-stock/v1/trading/order"
        headers = self._get_headers("TTTT1006U")
        
        data = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": "01",
            "OVRS_EXCG_CD": "NASD",
            "PDNO": symbol,
            "ORD_DVSN": "00", 
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0",
            "ORD_SVR_DVSN_CD": "0"
        }
        res = self._send_request("POST", path, headers, data=data)
        if res and res.get('rt_cd') == '0':
            return res['output']['ODNO']
        self.logger.error(f"Sell Failed: {res}")
        return None