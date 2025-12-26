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
        """
        해외주식 거래량 순위 조회 (수정: 응답 Key 'output2'로 변경)
        API ID: 해외주식-043 (HHDFS76310010)
        """
        path = "/uapi/overseas-stock/v1/ranking/trade-vol"
        self._update_headers("HHDFS76310010") 
        
        params = {
            "AUTH": "",
            "EXCD": "NAS",      # 나스닥
            "NDAY": "0",        # 당일
            "PRC1": "", "PRC2": "",
            "VOL_RANG": "0",    # 전체
            "KEYB": ""
        }
        
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
            data = res.json()
            
            rt_cd = data.get('rt_cd')
            
            if rt_cd == '0':
                # [수정] 거래량 순위 API는 종목 리스트를 'output2'에 담아서 줍니다.
                # 기존: data.get('output', []) -> 변경: data.get('output2', [])
                ranking_data = data.get('output2', [])
                
                if not ranking_data:
                    logger.warning(f"Ranking 조회 성공했으나 데이터가 비어있습니다. 응답: {str(data)[:100]}...")
                    
                return ranking_data
            else:
                logger.error(f"Ranking 조회 실패: {data.get('msg1')} (Code: {data.get('msg_cd')})")
                return []
                
        except Exception as e:
            logger.error(f"Ranking 요청 중 예외 발생: {e}")
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
        
        # [수정] 가격 호가 단위 준수 (1달러 이상: 소수점 2자리, 1달러 미만: 소수점 4자리)
        # API 에러 방지: "1$이상 소수점 2자리까지만 가능 합니다."
        if float(price) >= 1.0:
            final_price = f"{float(price):.2f}"
        else:
            final_price = f"{float(price):.4f}"
        
        body = {
            "CANO": Config.CANO,
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "OVRS_EXCG_CD": exchange,
            "PDNO": symbol,
            "ORD_QTY": str(int(qty)),
            "OVRS_ORD_UNPR": final_price, # [변경] str(price) -> final_price
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00"
        }
        
        try:
            res = requests.post(f"{self.base_url}{path}", headers=self.headers, json=body)
            data = res.json()
            
            if data['rt_cd'] == '0':
                logger.info(f"✅ 주문 성공 [{side}] {symbol} {qty}주 @ ${final_price}")
                return True
            else:
                logger.error(f"주문 실패: {data.get('msg1')} (Code: {data.get('msg_cd')})")
                return False
        except Exception as e:
            logger.error(f"주문 요청 중 에러: {e}")
            return False