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
        self.base_url = Config.URL_BASE # [Fix] Config().BASE_URL -> Config.URL_BASE
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

    @log_api_call("예수금 조회")
    def get_buyable_cash(self) -> float:
        """예수금 조회 (통합 증거금 확인)"""
        path = "/uapi/overseas-stock/v1/trading/inquire-present-balance"
        tr_id = "VTRP6504R" if "vts" in self.base_url else "CTRP6504R"
        self._update_headers(tr_id)
        
        params = {
            "CANO": Config.CANO,
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "WCRC_FRCR_DVSN_CD": "02",
            "NATN_CD": "840",
            "TR_MK": "00",
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
        except:
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
    def get_current_price(self, exchange, symbol): # [Fix] exchange 인자 추가됨
        path = "/uapi/overseas-price/v1/quotations/price"
        self._update_headers("HHDFS00000300")
        lookup_excd = self._get_lookup_excd(exchange) # 거래소 코드 변환
        
        params = {"AUTH": "", "EXCD": lookup_excd, "SYMB": symbol}
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
            data = res.json()
            if data['rt_cd'] == '0': 
                return dict(
                    last=float(data['output']['last']),
                    open=float(data['output']['open']),
                    volume=int(data['output']['tvol'])
                )
            return None
        except: return None

    # [Fix] 기존 코드에 없던 '주문 전송' 관련 메서드 복구
    def get_current_price_simple(self, symbol):
        """거래소 인자 없이 NAS 기본 조회 (호환성용)"""
        return self.get_current_price("NAS", symbol)

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
        
        body = {
            "CANO": Config.CANO,
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "OVRS_EXCG_CD": exchange,
            "PDNO": symbol,
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
        # 호환성을 위해 NASD 기본값 사용
        return self.place_order_final("NASD", symbol, "BUY", qty, price)

    def sell_market(self, symbol, qty):
        # 안전 매도: 현재가 -5% 지정가
        curr = self.get_current_price("NASD", symbol)
        price = "0"
        if curr:
            price = curr['last'] * 0.95
        return self.place_order_final("NASD", symbol, "SELL", qty, price)

    def get_minute_candles(self, exchange, symbol, limit=100):
        """분봉 조회 -> DataFrame 변환"""
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

    # === [누락된 메서드 복구] ===
    def check_order_filled(self, order_no):
        """체결 확인"""
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
        """체결 대기"""
        start = time.time()
        while time.time() - start < timeout:
            if self.check_order_filled(order_no): return True
            time.sleep(2)
        return False