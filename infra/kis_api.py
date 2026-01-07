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

    @log_api_call("예수금 조회(주문가능)")
    def get_buyable_cash(self) -> float:
        """
        [수정 완료] 단순 잔고(Balance)가 아닌, '매수 가능 금액(Buying Power)'을 조회합니다.
        API: 해외주식 매수가능금액조회 (TTTS3007R)
        """
        path = "/uapi/overseas-stock/v1/trading/inquire-psamount"
        
        # 실전: TTTS3007R / 모의: VTTS3007R
        tr_id = "TTTS3007R" if "vts" not in self.base_url else "VTTS3007R"
        self._update_headers(tr_id)
        
        params = {
            "CANO": Config.CANO, 
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "OVRS_EXCG_CD": "NASD", 
            "OVRS_ORD_UNPR": "0",  # 시장가 기준 계산
            "ITEM_CD": "AAPL"      # 기준 종목 (애플 기준)
        }
        
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
            data = res.json()
            
            if data['rt_cd'] == '0':
                # API 명세서 기준: output은 리스트가 아니라 객체(Dictionary)입니다.
                output = data.get('output', {})
                # frcr_ord_psbl_amt1: 외화주문가능금액 (실제 사용할 수 있는 돈)
                return self._safe_float(output.get('frcr_ord_psbl_amt1'))
            else:
                logger.warning(f"주문가능금액 조회 실패 Msg: {data.get('msg1')}")
                
        except Exception as e:
            logger.error(f"주문가능금액 조회 에러: {e}")
            
        return 0.0

    @log_api_call("잔고 조회")
    def get_balance(self):
        path = "/uapi/overseas-stock/v1/trading/inquire-balance"
        tr_id = "TTTS3012R" if "vts" not in self.base_url else "VTTS3012R"
        self._update_headers(tr_id)
        
        # [수정] FK100 -> FK200, NK100 -> NK200 (해외주식 전용 키)
        params = {
            "CANO": Config.CANO, 
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "OVRS_EXCG_CD": "NASD", 
            "TR_CRCY_CD": "USD", 
            "CTX_AREA_FK200": "",  # <-- 여기 수정됨
            "CTX_AREA_NK200": ""   # <-- 여기 수정됨
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

    @log_api_call("랭킹 조회(통합)")
    def get_ranking(self):
        try:
            path = "/uapi/overseas-stock/v1/ranking/updown-rate" 
            self._update_headers("HHDFS76290000")
            params = {
                "AUTH": "", "EXCD": "NAS", "GUBN": "1", "NDAY": "0", 
                "VOL_RANG": "0", "KEYB": ""
            }
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params, timeout=10)
            
            if res.status_code != 200 or not res.text.strip().startswith("{"):
                raise ValueError("Invalid Response Format")

            data = res.json()
            if data['rt_cd'] == '0':
                result = data.get('output2', [])
                # [논리 수정] 데이터가 비어있으면 실패로 간주하여 except로 보냄
                if not result:
                    raise ValueError("Ranking data is empty")
                return result
                
        except Exception as e:
            logger.warning(f"⚠️ 등락률 조회 실패 또는 데이터 없음: {e}. 거래량 순위로 우회합니다.")
            # 여기서 자동으로 아래의 _get_volume_ranking() 시도로 넘어감
            pass 

        try:
            return self._get_volume_ranking()
        except Exception as e:
            logger.error(f"❌ 랭킹 조회 최종 실패: {e}")
            return []

    def _get_volume_ranking(self):
        """[Fallback] 거래량 상위 종목 조회"""
        path = "/uapi/overseas-stock/v1/ranking/trade-vol"
        self._update_headers("HHDFS76310010") # 거래량 순위 TR ID
        params = {
            "AUTH": "", "EXCD": "NAS", "GUBN": "0", "VOL_RANG": "0", "KEYB": ""
        }
        res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
        data = res.json()
        if data['rt_cd'] == '0':
            return data.get('output', [])
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
            else:
                # [수정] 실패 시 침묵하지 않고 경고 로그 출력
                logger.warning(f"⚠️ 현재가 조회 실패 ({symbol}): {data.get('msg1')} (Code: {data.get('msg_cd')})")
                
        except Exception as e:
            # [수정] 에러 발생 시 로그 출력
            logger.error(f"❌ 현재가 조회 중 에러 ({symbol}): {e}")
            
        return None

    @log_api_call("주문 전송")
    def place_order_final(self, exchange, symbol, side, qty, price):
        path = "/uapi/overseas-stock/v1/trading/order"
        is_buy = (side == "BUY")
        _id = "TTTT1002U" if is_buy else "TTTT1006U"
        if "vts" in self.base_url: tr_id = "VTTT1002U" if is_buy else "VTTT1001U"
        
        self._update_headers(tr_id)
        
        f_price = float(price)
        if f_price >= 1.0: final_price = f"{f_price:.2f}" 
        else: final_price = f"{f_price:.4f}" 
        
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

    # [수정] 미국 주식용 시장가 매도 (현재가 -5% 지정가로 투척) #
    def sell_market(self, symbol, qty):
        """시장가 매도 시뮬레이션: 현재가 대비 5% 낮게 던져 즉시 체결 유도"""
        try:
            price_info = self.get_current_price("NASD", symbol)
            if not price_info: return None
            
            # [수정] 하드코딩된 0.95를 명확한 변수로 분리 (추후 Config 연동 권장)
            SELL_BUFFER = 0.95 
            limit_price = price_info['last'] * SELL_BUFFER
            
            return self.place_order_final("NASD", symbol, "SELL", qty, limit_price)
        except: return None
    
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
        except Exception: pass
        return pd.DataFrame()
    
    def get_daily_candle(self, exchange, symbol, period=100): return pd.DataFrame()