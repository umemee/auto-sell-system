import requests
import json
import pandas as pd
import time
from config import Config
from infra.utils import get_logger, log_api_call

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

    def _safe_float(self, val):
        try:
            if not val: return 0.0
            return float(str(val).replace(",", ""))
        except Exception:
            return 0.0
            
    def _get_lookup_excd(self, exchange):
        excd_map = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}
        return excd_map.get(exchange, exchange)

    @log_api_call("예수금 조회")
    def get_buyable_cash(self) -> float:
        """예수금 조회"""
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
                    return self._safe_float(output2[0].get('frcr_dncl_amt_2') or output2[0].get('frcr_drwg_psbl_amt_1'))
        except Exception as e:
            logger.error(f"예수금 조회 실패: {e}")
        return 0.0

    @log_api_call("잔고 조회")
    def get_balance(self):
        """보유 종목 잔고 조회"""
        path = "/uapi/overseas-stock/v1/trading/inquire-balance"
        tr_id = "TTTS3012R" if "vts" not in self.base_url else "VTTS3012R"
        self._update_headers(tr_id)
        
        params = {
            "CANO": Config.CANO,
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "OVRS_EXCG_CD": "NASD",
            "TR_CRCY_CD": "USD",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""
        }
        
        holdings = []
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
            data = res.json()
            if data['rt_cd'] == '0':
                output1 = data.get('output1', [])
                for item in output1:
                    qty = self._safe_float(item.get('ovrs_cblc_qty'))
                    if qty > 0:
                        holdings.append({
                            "symbol": item.get('ovrs_pdno'),
                            "qty": qty,
                            "price": self._safe_float(item.get('ovrs_stck_evlu_amt')), 
                            "pnl_pct": self._safe_float(item.get('frcr_evlu_pfls_rt'))
                        })
        except Exception as e:
            logger.error(f"잔고 조회 중 에러: {e}")
        return holdings

    @log_api_call("랭킹 조회")
    def get_ranking(self):
        """[NEW] 전일 대비 등락률 상위 종목 조회 (급등주 발굴용)"""
        path = "/uapi/overseas-stock/v1/ranking/fluctuation"
        self._update_headers("HHDFS76410000") # 등락률 순위 TR 코드
        
        params = {
            "AUTH": "",
            "EXCD": "NAS",      # 나스닥
            "GUBN": "0",        # 전체
            "VOL_RANG": "0",    # 거래량 조건 없음
            "KEYB": "",         # 다음 데이터 키
            "V_RANK_SORT_CLS_CODE": "0" # 0: 상승순
        }
        
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
            data = res.json()
            if data['rt_cd'] == '0':
                return data.get('output', [])
        except Exception as e:
            logger.error(f"랭킹 조회 실패: {e}")
        return []

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
                return {
                    "last": self._safe_float(data['output']['last']),
                    "open": self._safe_float(data['output']['open']),
                    "high": self._safe_float(data['output']['high']),
                    "low": self._safe_float(data['output']['low']),
                    "volume": int(self._safe_float(data['output']['tvol']))
                }
        except Exception:
            pass
        return None

    @log_api_call("주문 전송")
    def place_order_final(self, exchange, symbol, side, qty, price):
        path = "/uapi/overseas-stock/v1/trading/order"
        is_buy = (side == "BUY")
        tr_id = "TTTT1002U" if is_buy else "TTTT1006U"
        if "vts" in self.base_url: tr_id = "VTTT1002U" if is_buy else "VTTT1001U"
        
        self._update_headers(tr_id)
        
        final_price = f"{float(price):.2f}" if float(price) >= 1.0 else f"{float(price):.4f}"
        
        body = {
            "CANO": Config.CANO, "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "OVRS_EXCG_CD": exchange, "PDNO": symbol, "ORD_QTY": str(int(qty)),
            "OVRS_ORD_UNPR": final_price, "ORD_SVR_DVSN_CD": "0", "ORD_DVSN": "00"
        }
        try:
            res = requests.post(f"{self.base_url}{path}", headers=self.headers, json=body)
            data = res.json()
            if data['rt_cd'] == '0': return data['output'].get('ODNO')
            else: logger.error(f"주문실패 ({symbol}): {data.get('msg1')}")
        except Exception as e: logger.error(f"API Error: {e}")
        return None

    def buy_limit(self, s, p, q): return self.place_order_final("NASD", s, "BUY", q, p)
    def sell_market(self, s, q): return self.place_order_final("NASD", s, "SELL", q, 0)
    
    def get_minute_candles(self, market, symbol, limit=100):
        path = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
        self._update_headers("HHDFS76950200")
        params = {
            "AUTH": "", "EXCD": "NAS", "SYMB": symbol,
            "NMIN": "1", "PINC": "1", "NEXT": "", "NREC": str(limit), "KEYB": ""
        }
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
            data = res.json()
            if data['rt_cd'] == '0' and data.get('output2'):
                df = pd.DataFrame(data['output2'])
                df = df.rename(columns={
                    'kymd': 'date', 'khms': 'time',
                    'open': 'open', 'high': 'high', 'low': 'low', 'last': 'close', 'vols': 'volume'
                })
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    df[col] = df[col].apply(self._safe_float)
                return df.sort_values('time')
        except Exception:
            pass
        return pd.DataFrame()
    
    def get_daily_candle(self, exchange, symbol, period=100):
        # 전략에서 필요로 할 경우를 대비해 유지
        return pd.DataFrame()