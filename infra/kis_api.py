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

    def _get_lookup_excd(self, exchange):
        excd_map = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}
        return excd_map.get(exchange, exchange)

    @log_api_call("예수금 조회")
    def get_buyable_cash(self) -> float:
        """예수금 조회 (통합 증거금 확인)"""
        path = "/uapi/overseas-stock/v1/trading/inquire-present-balance"
        tr_id = "VTRP6504R" if "vts" in self.base_url else "CTRP6504R"
        self._update_headers(tr_id)
        
        # [Fix 1] debug_balance.py와 동일하게 파라미터 수정 (TR_MK -> TR_MKET_CD)
        params = {
            "CANO": Config.CANO,
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "WCRC_FRCR_DVSN_CD": "02",
            "NATN_CD": "840",
            "TR_MKET_CD": "00",  # 👈 여기가 수정되었습니다!
            "INQR_DVSN_CD": "00"
        }
        
        res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
        data = res.json()
        
        if data['rt_cd'] == '0':
            output2 = data.get('output2', [])
            if output2 and len(output2) > 0:
                cash_str = output2[0].get('frcr_dncl_amt_2') 
                if not cash_str:
                    cash_str = output2[0].get('frcr_drwg_psbl_amt_1')
                if cash_str:
                    return float(cash_str)
        return 0.0

    @log_api_call("랭킹 조회")
    def get_ranking(self, sort_type="vol"):
        """거래량/등락률 상위 종목 조회"""
        path = "/uapi/overseas-stock/v1/ranking/trade-vol"
        self._update_headers("HHDFS76310010") 
        
        params = {
            "AUTH": "", "EXCD": "NAS", "NDAY": "0",
            "PRC1": "", "PRC2": "", "VOL_RANG": "0", "KEYB": ""
        }
        
        res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
        data = res.json()
        if data.get('rt_cd') == '0':
            ranking_data = data.get('output2', [])
            if not ranking_data:
                    ranking_data = data.get('output', [])
            return ranking_data
        return []

    @log_api_call("현재가 조회")
    def get_current_price(self, exchange, symbol):
        """현재가 조회"""
        path = "/uapi/overseas-price/v1/quotations/price"
        self._update_headers("HHDFS00000300")
        lookup_excd = self._get_lookup_excd(exchange)
        
        params = {"AUTH": "", "EXCD": lookup_excd, "SYMB": symbol}
        
        res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
        data = res.json()
        if data['rt_cd'] == '0': 
            return dict(
                last=float(data['output']['last']),
                open=float(data['output']['open']),
                volume=int(data['output']['tvol'])
            )
        return None

    @log_api_call("일봉 차트 조회")
    def get_daily_candle(self, exchange, symbol, period=100):
        """과거 n일 간의 일봉 데이터 조회 (OHLCV)"""
        path = "/uapi/overseas-price/v1/quotations/dailyprice"
        self._update_headers("HHDFS76240000")
        lookup_excd = self._get_lookup_excd(exchange)
        
        params = {
            "AUTH": "",
            "EXCD": lookup_excd,
            "SYMB": symbol,
            "GUBN": "0",
            "BYMD": "",
            "MODP": "1"
        }
        
        res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
        data = res.json()
        
        if data['rt_cd'] == '0':
            output2 = data.get('output2', [])
            df = pd.DataFrame(output2)
            if not df.empty:
                df = df[['xymd', 'open', 'high', 'low', 'clos', 'tvol']]
                df.columns = ['date', 'open', 'high', 'low', 'close', 'volume']
                df = df.astype({'open': float, 'high': float, 'low': float, 'close': float, 'volume': int})
                df = df.sort_values('date').tail(period)
                return df
        return None

    @log_api_call("주문 전송")
    def place_order_final(self, exchange, symbol, side, qty, price, trade_id=None):
        """실제 주문 전송 (매수/매도 공통)"""
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
            # [Fix 2 & 3] 매수/매도 모두 OVRS_ORD_UNPR 사용 (ORD_UNPR 아님!)
            "OVRS_ORD_UNPR": final_price,  # 👈 여기가 수정되었습니다!
            "ORD_SVR_DVSN_CD": "0", 
            "ORD_DVSN": "00"
        }
        
        res = requests.post(f"{self.base_url}{path}", headers=self.headers, json=body)
        data = res.json()
        if data['rt_cd'] == '0':
            return data['output'].get('ODNO')
        else:
            logger.error(f"주문 실패 메시지: {data.get('msg1')}")
            return None

    # (호환성 유지) 구버전 buy_limit 함수
    def buy_limit(self, symbol, price, qty):
        return self.place_order_final("NASD", symbol, "BUY", qty, price)

    # (호환성 유지) 구버전 sell_market 함수
    def sell_market(self, symbol, qty):
        # 시장가 매도라도 안전하게 가격 0으로 지정가 주문 전송 (해외주식 관행)
        return self.place_order_final("NASD", symbol, "SELL", qty, 0)

    @log_api_call("미체결 조회")
    def get_unfilled_qty(self, exchange, symbol, order_no=None):
        """미체결 수량 확인"""
        path = "/uapi/overseas-stock/v1/trading/inquire-nccs"
        self._update_headers("TTTS3018R")
        params = {
            "CANO": Config.CANO, "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "OVRS_EXCG_CD": exchange, "SORT_SQN": "DS", 
            "CTX_AREA_FK200": "", "CTX_AREA_NK200": ""
        }
        
        res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
        data = res.json()
        if data['rt_cd'] != '0': return 0
        
        output = data.get('output', [])
        for item in output:
            if item.get('pdno') == symbol:
                if order_no and item.get('odno') != order_no: continue
                return int(item.get('nccs_qty', 0))
        return 0
    
    # (호환성 유지) 분봉 차트 조회
    def get_minute_candles(self, market, symbol, limit=100):
        path = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
        self._update_headers("HHDFS76950200")
        
        params = {
            "AUTH": "", "EXCD": market, "SYMB": symbol,
            "NMIN": "1", "PINC": "1", "NEXT": "", "NREC": "120", "KEYB": ""
        }
        res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
        data = res.json()
        if data and data.get('output2'):
            df = pd.DataFrame(data['output2'])
            df = df.rename(columns={
                'kymd': 'date', 'khms': 'time',
                'open': 'open', 'high': 'high', 'low': 'low', 'last': 'close', 'vols': 'volume'
            })
            cols = ['open', 'high', 'low', 'close', 'volume']
            df[cols] = df[cols].apply(pd.to_numeric)
            return df.sort_values('time')
        return pd.DataFrame()