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
        # [검증 완료] Config.URL_BASE 사용
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

    @log_api_call("예수금 조회")
    def get_buyable_cash(self) -> float:
        """예수금 조회 (달러)"""
        path = "/uapi/overseas-stock/v1/trading/inquire-present-balance"
        self._update_headers("CTRP6504R")
        
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
                    # 외화예수금 or 인출가능금액 확인
                    cash = output2[0].get('frcr_dncl_amt_2') or output2[0].get('frcr_drwg_psbl_amt_1')
                    return float(cash) if cash else 0.0
            return 0.0
        except Exception as e:
            logger.error(f"예수금 조회 실패: {e}")
            return 0.0

    @log_api_call("랭킹 조회")
    def get_ranking(self, sort_type="vol"):
        """거래량 상위 조회"""
        path = "/uapi/overseas-stock/v1/ranking/trade-vol"
        self._update_headers("HHDFS76310010") 
        
        params = {
            "AUTH": "", "EXCD": "NAS", "NDAY": "0",
            "PRC1": "", "PRC2": "", "VOL_RANG": "0", "KEYB": ""
        }
        
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
            data = res.json()
            if data.get('rt_cd') == '0':
                return data.get('output2') or data.get('output', [])
            return []
        except:
            return []

    @log_api_call("현재가 조회")
    def get_current_price(self, symbol):
        """현재가 조회"""
        path = "/uapi/overseas-price/v1/quotations/price"
        self._update_headers("HHDFS00000300")
        
        params = {"AUTH": "", "EXCD": "NAS", "SYMB": symbol}
        
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
        except:
            return None

    @log_api_call("주문 전송")
    def _place_order(self, symbol, side, qty, price="0"):
        """주문 공통 함수"""
        path = "/uapi/overseas-stock/v1/trading/order"
        is_buy = (side == "BUY")
        tr_id = "TTTT1002U" if is_buy else "TTTT1006U"
        self._update_headers(tr_id)
        
        final_price = "0"
        if float(price) > 0:
            final_price = f"{float(price):.2f}" if float(price) >= 1.0 else f"{float(price):.4f}"
            
        body = {
            "CANO": Config.CANO,
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "OVRS_EXCG_CD": "NAS",
            "PDNO": symbol,
            "ORD_QTY": str(int(qty)),
            "OVRS_ORD_UNPR": final_price,
            "ORD_SVR_DVSN_CD": "0", 
            "ORD_DVSN": "00" # 지정가 주문
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
        """지정가 매수"""
        return self._place_order(symbol, "BUY", qty, price)

    def sell_market(self, symbol, qty):
        """
        [Safety Fix] 해외주식 안전 매도
        시장가(Price=0)가 안 될 경우를 대비해 현재가 조회 후 -5% 가격으로 지정가 매도(Immediate Fill 유도)
        """
        curr = self.get_current_price(symbol)
        if curr:
            # 현재가보다 5% 낮게 던져서 즉시 체결 유도 (사실상 시장가)
            safe_price = curr['last'] * 0.95
            return self._place_order(symbol, "SELL", qty, safe_price)
        else:
            # 조회 실패 시 그냥 0으로 시도
            return self._place_order(symbol, "SELL", qty, "0")

    def get_minute_candles(self, symbol, timeframe="1"):
        """분봉 조회 -> DataFrame 변환"""
        path = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
        self._update_headers("HHDFS76950200")
        
        params = {
            "AUTH": "", "EXCD": "NAS", "SYMB": symbol,
            "NMIN": timeframe, "PINC": "1", "NEXT": "", "NREC": "100", "KEYB": ""
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
        except Exception as e:
            logger.error(f"분봉 조회 에러: {e}")
            return pd.DataFrame()

    def check_order_filled(self, order_no):
        """주문 체결 확인"""
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
                    # 전량 체결 여부
                    return ccld_qty >= ord_qty and ord_qty > 0
            return False
        except:
            return False

    def wait_for_fill(self, order_no, timeout=30):
        """체결 대기 (최대 timeout초)"""
        start = time.time()
        while time.time() - start < timeout:
            if self.check_order_filled(order_no):
                return True
            time.sleep(1)
        return False