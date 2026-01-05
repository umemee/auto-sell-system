import requests
import json
import time
import pandas as pd
from config import Config
from infra.utils import get_logger, log_api_call

logger = get_logger()

class KisApi:
    def __init__(self, token_manager):
        self.tm = token_manager
        self.base_url = Config.URL_BASE
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
        """[시세조회용] 3자리 코드 변환 (NAS, NYS, AMS)"""
        excd_map = {
            "NASD": "NAS", "NAS": "NAS",
            "NYSE": "NYS", "NYS": "NYS",
            "AMEX": "AMS", "AMS": "AMS"
        }
        return excd_map.get(exchange, "NAS")

    def _get_order_excd(self, exchange):
        """[주문용] 4자리 코드 변환 (NASD, NYSE, AMEX)"""
        excd_map = {
            "NAS": "NASD", "NASD": "NASD",
            "NYS": "NYSE", "NYSE": "NYSE",
            "AMS": "AMEX", "AMEX": "AMEX"
        }
        return excd_map.get(exchange, "NASD")

    @log_api_call("예수금 조회")
    def get_buyable_cash(self) -> float:
        path = "/uapi/overseas-stock/v1/trading/inquire-present-balance"
        tr_id = "VTRP6504R" if "vts" in self.base_url else "CTRP6504R"
        self._update_headers(tr_id)
        
        params = {
            "CANO": Config.CANO,
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "WCRC_FRCR_DVSN_CD": "02",
            "NATN_CD": "840",
            "TR_MKET_CD": "00",
            "INQR_DVSN_CD": "00"
        }
        
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
            data = res.json()
            if data['rt_cd'] == '0':
                output2 = data.get('output2', [])
                if output2:
                    cash = output2[0].get('frcr_dncl_amt_2') or output2[0].get('frcr_drwg_psbl_amt_1')
                    return float(cash) if cash else 0.0
            return 0.0
        except Exception as e:
            logger.error(f"예수금 조회 실패: {e}")
            return 0.0

    @log_api_call("랭킹 조회")
    def get_ranking(self, sort_type="vol"):
        path = "/uapi/overseas-stock/v1/ranking/trade-vol"
        self._update_headers("HHDFS76310010") 
        params = {"AUTH": "", "EXCD": "NAS", "NDAY": "0", "PRC1": "", "PRC2": "", "VOL_RANG": "0", "KEYB": ""}
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
            data = res.json()
            if data.get('rt_cd') == '0':
                return data.get('output2') or data.get('output', [])
            return []
        except: return []

    @log_api_call("현재가 조회")
    def get_current_price(self, exchange, symbol):
        path = "/uapi/overseas-price/v1/quotations/price"
        self._update_headers("HHDFS00000300")
        lookup_excd = self._get_lookup_excd(exchange)
        
        params = {"AUTH": "", "EXCD": lookup_excd, "SYMB": symbol}
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
            data = res.json()
            
            if data['rt_cd'] == '0': 
                output = data['output']
                last = float(output.get('last') or 0)
                base = float(output.get('base') or 0)
                open_p = float(output.get('open') or output.get('popen') or base) 
                vol = int(output.get('tvol') or 0)
                
                return dict(last=last, open=open_p, volume=vol)
            else:
                logger.error(f"현재가 조회 실패 ({symbol}): {data.get('msg1')}")
            return None
        except Exception as e:
            logger.error(f"현재가 조회 예외: {e}")
            return None

    def get_current_price_simple(self, symbol):
        return self.get_current_price("NASD", symbol)

    @log_api_call("주문 전송")
    def place_order_final(self, exchange, symbol, side, qty, price, trade_id=None):
        path = "/uapi/overseas-stock/v1/trading/order"
        is_buy = (side == "BUY")
        
        if "vts" in self.base_url:
            tr_id = "VTTT1002U" if is_buy else "VTTT1001U"
        else:
            tr_id = "TTTT1002U" if is_buy else "TTTT1006U"

        self._update_headers(tr_id)
        
        if float(price) >= 1.0: final_price = f"{float(price):.2f}"
        else: final_price = f"{float(price):.4f}"
        
        # [Fix] 주문용 4자리 코드 사용
        order_excd = self._get_order_excd(exchange)
        
        body = {
            "CANO": Config.CANO,
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "OVRS_EXCG_CD": order_excd,
            "PDNO": symbol.upper(), # [Safety] 대문자 강제 변환
            "ORD_QTY": str(int(qty)),
            "OVRS_ORD_UNPR": final_price,
            "ORD_SVR_DVSN_CD": "0", "ORD_DVSN": "00"
        }
        
        try:
            res = requests.post(f"{self.base_url}{path}", headers=self.headers, json=body)
            data = res.json()
            if data['rt_cd'] == '0':
                return data['output'].get('ODNO')
            else:
                logger.error(f"주문 실패: {data.get('msg1')}")
                return None
        except Exception as e:
            logger.error(f"주문 전송 중 에러: {e}")
            return None

    def buy_limit(self, symbol, price, qty):
        return self.place_order_final("NASD", symbol, "BUY", qty, price)

    def sell_market(self, symbol, qty):
        curr = self.get_current_price("NASD", symbol)
        price = "0"
        if curr:
            price = curr['last'] * 0.95
        return self.place_order_final("NASD", symbol, "SELL", qty, price)

    def get_minute_candles(self, exchange, symbol, limit=100):
        path = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
        self._update_headers("HHDFS76950200")
        lookup_excd = self._get_lookup_excd(exchange)
        
        params = {
            "AUTH": "", "EXCD": lookup_excd, "SYMB": symbol,
            "NMIN": "1", "PINC": "1", "NEXT": "", "NREC": str(limit), "KEYB": ""
        }
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
            if res.status_code == 200:
                data = res.json()
                if data['rt_cd'] == '0':
                    items = data['output2']
                    if not items: return pd.DataFrame()
                    df = pd.DataFrame(items)
                    df = df[['kymd', 'khms', 'open', 'high', 'low', 'last', 'evol']]
                    df.columns = ['date', 'time', 'open', 'high', 'low', 'close', 'volume']
                    df = df.astype({'open':float, 'high':float, 'low':float, 'close':float, 'volume':int})
                    df = df.sort_values(by=['date', 'time']).reset_index(drop=True)
                    return df
            return pd.DataFrame()
        except: return pd.DataFrame()

    def check_order_filled(self, order_no):
        path = "/uapi/overseas-stock/v1/trading/inquire-lcc-order-res"
        self._update_headers("TTTS3035R")
        params = {
            "CANO": Config.CANO, "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "ODNO": order_no, "PRCS_DVSN": "00", 
            "CTX_AREA_FK200": "", "CTX_AREA_NK200": ""
        }
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
            if res.status_code == 200:
                data = res.json()
                output = data.get('output', [])
                if output:
                    ord_qty = int(output[0].get('ord_qty', 0))
                    ccld_qty = int(output[0].get('tot_ccld_qty', 0))
                    return ccld_qty >= ord_qty and ord_qty > 0
            return False
        except: return False

    def wait_for_fill(self, order_no, timeout=30):
        start = time.time()
        while time.time() - start < timeout:
            if self.check_order_filled(order_no): return True
            time.sleep(2)
        return False