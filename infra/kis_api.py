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

    def place_order_final(self, exchange, symbol, side, qty, price, trade_id=None):
        """
        [최종 주문 함수]
        trade_id: 백테스팅 및 전략 구분용 (실전 API 전송 시에는 사용 안 함)
        """
        path = "/uapi/overseas-stock/v1/trading/order"
        is_buy = (side == "BUY")
        tr_id = "TTTT1002U" if is_buy else "TTTT1006U"
        self._update_headers(tr_id)
        
        # 가격 호가 단위 준수
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
            "OVRS_ORD_UNPR": final_price,
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00"
        }
        
        try:
            res = requests.post(f"{self.base_url}{path}", headers=self.headers, json=body)
            data = res.json()
            
            if data['rt_cd'] == '0':
                # [수정] 주문 성공 시 '주문번호(ODNO)'를 반환 (추후 체결 확인용)
                odno = data['output'].get('ODNO')
                logger.info(f"✅ 주문 전송 성공 [{side}] {symbol} {qty}주 @ ${final_price} (ODNO: {odno})")
                return odno # 주문번호 리턴 (Python에서 문자열은 True로 취급됨 -> 기존 로직 호환)
            else:
                logger.error(f"주문 실패: {data.get('msg1')} (Code: {data.get('msg_cd')})")
                return None
        except Exception as e:
            logger.error(f"주문 요청 중 에러: {e}")
            return None

    def get_unfilled_qty(self, exchange, symbol, order_no=None):
        """
        [신규 기능] 미체결 내역 조회 (체결 확인용)
        - symbol: 종목코드
        - order_no: (선택) 특정 주문번호만 확인할 경우
        - Return: 해당 종목(또는 주문)의 미체결 수량 (0이면 전량 체결 의미)
        """
        path = "/uapi/overseas-stock/v1/trading/inquire-nccs"
        self._update_headers("TTTS3018R") # 해외주식 미체결내역 TR
        
        params = {
            "CANO": Config.CANO,
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "OVRS_EXCG_CD": exchange,
            "SORT_SQN": "DS", # 역순(최근주문부터)
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": ""
        }
        
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
            data = res.json()
            
            if data['rt_cd'] != '0':
                # 조회 실패 시 안전하게 0 리턴 (로그만 남김)
                # logger.warning(f"미체결 조회 실패 ({symbol}): {data.get('msg1')}")
                return 0
                
            output = data.get('output', [])
            total_unfilled = 0
            
            for item in output:
                # 종목 코드가 일치하는지 확인
                if item.get('pdno') == symbol:
                    # 특정 주문번호가 지정된 경우, 그 주문만 체크
                    # API에서 주는 주문번호(odno)와 내가 가진 번호(order_no)가 일치하는지 확인
                    if order_no and item.get('odno') != order_no:
                        continue
                    
                    # 일치하는 주문을 찾으면 미체결 수량(nccs_qty) 반환
                    return int(item.get('nccs_qty', 0))
            
            return 0 # 리스트를 다 뒤져도 없으면 미체결 없음(전량 체결됨)
            
        except Exception as e:
            logger.error(f"미체결 확인 중 에러: {e}")
            return 0
        
    def revise_order(self, exchange, symbol, orgn_order_no, revise_type, qty, price, order_type="00"):
        """
        [주문 정정/취소 API]
        API ID: 해외주식 주문 정정/취소 (TTTS3035U / VTTS3035U)
        
        Args:
            exchange (str): 거래소 코드 (NASD, NYS, AMS)
            symbol (str): 종목 코드
            orgn_order_no (str): 원주문 번호 (get_unfilled_qty로 조회한 미체결 주문번호)
            revise_type (str): "01"(정정), "02"(취소)
            qty (int): 정정/취소할 수량 (전량 취소 시 미체결 잔량 입력)
            price (float): 정정할 가격 (취소 시에는 0 입력)
            order_type (str): 주문 구분 (기본 "00": 지정가)
        """
        path = "/uapi/overseas-stock/v1/trading/order-rvsecncl"
        
        # [TR ID 설정] 
        # 실전투자: TTTS3035U
        # 모의투자: VTTS3035U
        # ※ 학습 단계이므로 실전용 ID를 기본으로 하되, 모의투자 시 아래 값을 'VTTS3035U'로 변경하세요.
        tr_id = "TTTS3035U"
        
        self._update_headers(tr_id)

        body = {
            "CANO": Config.CANO,
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "OVRS_EXCG_CD": exchange, 
            "PDNO": symbol,
            "ORGN_ODNO": orgn_order_no,
            "RVSE_CNCL_DV_CD": revise_type, # 01: 정정, 02: 취소
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price),
            "ORD_DVSN": order_type,
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": ""
        }

        try:
            res = requests.post(f"{self.base_url}{path}", headers=self.headers, data=json.dumps(body))
            data = res.json()
            
            if data['rt_cd'] == '0':
                action = "정정" if revise_type == "01" else "취소"
                logger.info(f"✅ [{symbol}] 주문 {action} 성공! (원주문:{orgn_order_no} -> {action})")
                return data['output']
            else:
                logger.error(f"❌ [{symbol}] 주문 정정/취소 실패: {data['msg1']} (Code: {data['msg_cd']})")
                return None
        except Exception as e:
            logger.error(f"[주문 정정/취소 예외 발생] {e}")
            return None