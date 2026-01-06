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
        self.base_url = Config.KIS_BASE_URL
        self.account_no = Config.KIS_ACCOUNT_NO
        
    def _get_headers(self, tr_id):
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.token_manager.get_token()}",
            "appkey": Config.KIS_APPKEY,
            "appsecret": Config.KIS_APPSECRET,
            "tr_id": tr_id
        }

    def _send_request(self, method, path, headers, params=None, data=None):
        url = f"{self.base_url}{path}"
        try:
            if method == "GET":
                resp = requests.get(url, headers=headers, params=params)
            else:
                resp = requests.post(url, headers=headers, json=data)
            
            if resp.status_code != 200:
                self.logger.error(f"API Error {resp.status_code}: {resp.text}")
                return None
            return resp.json()
        except Exception as e:
            self.logger.error(f"Request failed: {e}")
            return None

    def get_current_price(self, market, symbol):
        """현재가 조회 (프리마켓 고려)"""
        path = "/uapi/overseas-price/v1/quotations/price"
        headers = self._get_headers("HHDFS76200200")
        params = {"AUTH": "", "EXCD": market, "SYMB": symbol}
        
        res = self._send_request("GET", path, headers, params)
        if res and res.get('output'):
            out = res['output']
            # 프리마켓(popen) -> 정규장(open) -> 기준가(base) 순서로 시가 확보
            open_p = float(out.get('popen') or out.get('open') or out.get('base') or 0)
            return {
                "symbol": symbol,
                "last": float(out['last']),
                "open": open_p,
                "base": float(out['base']),
                "high": float(out['high']),
                "low": float(out['low'])
            }
        return None

    def get_minute_candles(self, market, symbol, limit=100):
        """분봉 데이터 조회 (DataFrame 반환)"""
        path = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
        headers = self._get_headers("HHDFS76240000")
        params = {
            "AUTH": "", "EXCD": market, "SYMB": symbol,
            "NMIN": "1", "PINC": "1",
            "NEXT": "", "NREC": "120", # 120개 요청
            "KEYB": ""
        }
        res = self._send_request("GET", path, headers, params)
        if res and res.get('output2'):
            df = pd.DataFrame(res['output2'])
            # 컬럼 이름 매핑 (KIS -> System)
            df = df.rename(columns={
                'kymd': 'date', 'khms': 'time',
                'open': 'open', 'high': 'high', 'low': 'low', 'last': 'close', 'vols': 'volume'
            })
            # 숫자 변환
            cols = ['open', 'high', 'low', 'close', 'volume']
            df[cols] = df[cols].apply(pd.to_numeric)
            return df.sort_values('time')
        return pd.DataFrame()

    def get_balance(self):
        """[핵심] 보유 종목 잔고 조회 (get_balance 오류 해결)"""
        path = "/uapi/overseas-stock/v1/trading/inquire-balance"
        headers = self._get_headers("TTTS3012R") # 실전용 TR 코드
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
                qty = float(item.get('ovrs_cblc_qty', 0)) # 잔고 수량
                if qty > 0:
                    holdings.append({
                        "symbol": item['ovrs_pdno'],
                        "qty": int(qty),
                        "price": float(item['ovrs_now_pric2'])
                    })
        return holdings

    def get_buyable_cash(self):
        """매수 가능 현금(예수금) 조회"""
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
        if res and 'output2' in res:
            # 해외주식 주문가능금액
            return float(res['output2'].get('ovrs_ord_psbl_amt', 0))
        return 0.0

    def buy_limit(self, symbol, price, qty):
        """지정가 매수 주문"""
        path = "/uapi/overseas-stock/v1/trading/order"
        headers = self._get_headers("TTTS3011R") # 실전 매수 TR
        data = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": "01",
            "OVRS_EXCG_CD": "NASD",
            "PDNO": symbol,
            "ORD_DVSN": "00", # 00: 지정가
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price),
            "ORD_SVR_DVSN_CD": "0"
        }
        res = self._send_request("POST", path, headers, data=data)
        if res and res.get('rt_cd') == '0':
            return res['output']['ODNO'] # 주문번호 반환
        return None