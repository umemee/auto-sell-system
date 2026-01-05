# infra/kis_api.py - v3.1 Fixed (Chart Data Restored)
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
        
        params = {
            "CANO": Config.CANO,
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "WCRC_FRCR_DVSN_CD": "02",
            "NATN_CD": "840",
            "TR_MK": "00",
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

    # [🔥 복구된 기능] 일봉 차트 데이터 조회 (SignalEngine 필수)
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
            "MODP": "1" # 수정주가 적용
        }
        
        res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
        data = res.json()
        
        if data['rt_cd'] == '0':
            output2 = data.get('output2', [])
            df = pd.DataFrame(output2)
            if not df.empty:
                # 필요한 컬럼만 추출 및 형변환
                df = df[['xymd', 'open', 'high', 'low', 'clos', 'tvol']]
                df.columns = ['date', 'open', 'high', 'low', 'close', 'volume']
                df = df.astype({'open': float, 'high': float, 'low': float, 'close': float, 'volume': int})
                df = df.sort_values('date').tail(period) # 날짜 오름차순 정렬 후 최근 n개
                return df
        return None

    @log_api_call("주문 전송")
    def place_order_final(self, exchange, symbol, side, qty, price, trade_id=None):
        """실제 주문 전송"""
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
        
        res = requests.post(f"{self.base_url}{path}", headers=self.headers, json=body)
        data = res.json()
        if data['rt_cd'] == '0':
            return data['output'].get('ODNO')
        else:
            logger.error(f"주문 실패 메시지: {data.get('msg1')}")
            return None

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

    def get_minute_candles(self, exchange, symbol, timeframe="1"):
        """
        [Fix] 해외주식 분봉 조회
        """
        path = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
        url = f"{self.base_url}{path}"
        
        # [수정 포인트] _get_headers() 삭제 -> _update_headers() 호출 후 self.headers 사용
        self._update_headers("HHDFS76950200")
        
        # 거래소 코드 변환
        exch_map = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}
        kis_exch = exch_map.get(exchange, "NAS")
        
        params = {
            "AUTH": "",
            "EXCD": kis_exch,
            "SYMB": symbol,
            "NMIN": timeframe, 
            "PINC": "1",
            "NEXT": "",
            "NREC": "100", 
            "KEYB": ""
        }
        
        try:
            # [수정 포인트] headers=self.headers 로 변경
            res = requests.get(url, headers=self.headers, params=params)
            
            if res.status_code == 200:
                data = res.json()
                if data['rt_cd'] == '0':
                    return data['output2'] 
                else:
                    logger.error(f"Candle Fail: {data['msg1']}")
            else:
                logger.error(f"API Error {res.status_code}")    
        except Exception as e:
            logger.error(f"Request Error: {e}")


    # === [실전 필수 패치: 체결 확인 로직] ===
    
    def check_order_filled(self, order_no):
        """
        ① 특정 주문번호의 체결 상태 확인
        """
        path = "/uapi/overseas-stock/v1/trading/inquire-lcc-order-res"
        self._update_headers("TTTS3035R") # 해외주식 주문체결 조회 TR
        
        params = {
            "CANO": self.tm.cano,
            "ACNT_PRDT_CD": self.tm.acnt_prdt_cd,
            "ODNO": order_no,
            "PRCS_DVSN": "00", # 00: 전체
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": ""
        }
        
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
            if res.status_code == 200:
                data = res.json()
                output = data.get('output', [])
                if output:
                    # 주문수량(ord_qty)과 총체결수량(tot_ccld_qty) 비교
                    ord_qty = int(output[0].get('ord_qty', 0))
                    ccld_qty = int(output[0].get('tot_ccld_qty', 0))
                    return ccld_qty >= ord_qty and ord_qty > 0
            return False
        except Exception as e:
            logger.error(f"체결 확인 중 오류: {e}")
            return False

    def wait_for_fill(self, order_no, timeout=30):
        """
        ② 최대 30초 대기하며 체결 확인
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.check_order_filled(order_no):
                return True
            time.sleep(2) # 2초 간격 재확인
        return False