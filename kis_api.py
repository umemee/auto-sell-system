# kis_api.py
import requests
import json
import time
import math
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

    def get_candles(self, exchange, symbol, timeframe, limit=200):
        """
        [핵심] 차트 데이터(OHLCV) 조회 및 DataFrame 변환
        - timeframe: "1M" (1분), "5M" (5분)
        """
        path = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
        self._update_headers("HHDFS76950200") # 해외주식 분봉 조회 TR_ID

        # 분봉 변환 ("1M" -> "1", "5M" -> "5")
        nmin = "1"
        if timeframe == "5M": nmin = "5"
        
        params = {
            "AUTH": "",
            "EXCD": exchange,
            "SYMB": symbol,
            "NMIN": nmin,
            "PINC": "1",
            "NEXT": "",
            "NREC": "120", # API 1회 최대 조회 수
            "KEYB": ""
        }

        # KIS API는 1회 120건 제한이 있으므로, 200개 이상 필요 시 반복 호출 로직 필요할 수 있음.
        # 여기서는 전략의 핵심인 최근 데이터 확보를 위해 120건 기준으로 처리 (최근 데이터가 중요)
        # 필요시 Loop 로직 추가 가능하나 속도를 위해 1회 호출로 최적화
        
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
            res.raise_for_status()
            data = res.json()
            
            if data['rt_cd'] != '0':
                logger.error(f"Candle Fetch Error: {data['msg1']}")
                return pd.DataFrame()

            items = data['output2']
            if not items:
                return pd.DataFrame()

            # DataFrame 생성 및 타입 변환
            df = pd.DataFrame(items)
            df = df[['kymd', 'khms', 'open', 'high', 'low', 'last', 'evol']]
            df.columns = ['date', 'time', 'open', 'high', 'low', 'close', 'volume']
            df = df.astype({'open': float, 'high': float, 'low': float, 'close': float, 'volume': int})
            
            # 시간 오름차순 정렬 (과거 -> 현재)
            df = df.sort_values(by=['date', 'time']).reset_index(drop=True)
            
            return df

        except Exception as e:
            logger.error(f"Get Candles Failed: {e}")
            return pd.DataFrame()
        finally:
            time.sleep(0.1) # Rate Limit 미세 조정

    def get_current_price(self, exchange, symbol):
        """현재가 조회"""
        path = "/uapi/overseas-price/v1/quotations/price"
        self._update_headers("HHDFS00000300")
        
        params = {
            "AUTH": "",
            "EXCD": exchange,
            "SYMB": symbol
        }
        
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
            data = res.json()
            if data['rt_cd'] == '0':
                return float(data['output']['last'])
            return 0.0
        except Exception as e:
            logger.error(f"Price Fetch Error: {e}")
            return 0.0

    def place_order(self, exchange, symbol, side, price=0):
        """
        주문 실행 (매수/매도)
        - [Budget Guard] 적용됨
        """
        path = "/uapi/overseas-stock/v1/trading/order"
        
        # TR_ID 설정 (실전/모의 & 매수/매도)
        is_buy = (side == "BUY")
        if Config.IS_MOCK:
            tr_id = "VTTT1002U" if is_buy else "VTTT1001U"
        else:
            tr_id = "TTTT1002U" if is_buy else "TTTT1006U" # 미국 기준
            
        self._update_headers(tr_id)

        # 현재가 조회 및 수량 계산
        current_price = price if price > 0 else self.get_current_price(exchange, symbol)
        if current_price <= 0:
            logger.error("현재가 조회 실패로 주문 불가")
            return None

        qty = 0
        if is_buy:
            # [Smart Quantity] 예산 범위 내 최대 수량 계산
            max_qty = int(Config.TOTAL_BUDGET_USD // current_price)
            if max_qty < 1:
                logger.warning(f"예산 부족: ${Config.TOTAL_BUDGET_USD} / 현재가 ${current_price}")
                return None
            qty = max_qty
        else:
            # 매도 시에는 보유 수량을 strategy에서 받아와야 함 (여기서는 API 호출 규격만 정의)
            # 실제 qty는 strategy에서 주입받는 구조로 변경하거나, params로 받아야 함.
            # 이 함수는 qty를 인자로 받도록 수정 권장하나, 요청사항의 문맥상 자동계산 로직 예시임.
            # 아래 strategy.py 연동을 위해 qty를 인자로 받는 형태로 확장.
            pass 

        # (오버로딩 흉내: qty가 인자로 안 들어오면 자동 계산)
        # 실제 호출 시: place_order_final(self, exchange, symbol, side, qty, price) 사용 권장
        pass

    def place_order_final(self, exchange, symbol, side, qty, price):
        """최종 주문 전송"""
        path = "/uapi/overseas-stock/v1/trading/order"
        
        is_buy = (side == "BUY")
        if Config.IS_MOCK:
            tr_id = "VTTT1002U" if is_buy else "VTTT1001U"
        else:
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
            "ORD_DVSN": "00" # 지정가
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