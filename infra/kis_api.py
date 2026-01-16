import sys
import os

# -----------------------------------------------------------
# [필수] 상위 폴더(config.py가 있는 곳)를 인식하도록 경로 강제 추가
current_dir = os.path.dirname(os.path.abspath(__file__)) 
root_dir = os.path.dirname(current_dir)                  
sys.path.append(root_dir)                                
# -----------------------------------------------------------

import requests
import json
import pandas as pd
from config import Config
from infra.utils import get_logger, log_api_call

# 전역 로거 (데코레이터 등에서 사용)
logger = get_logger()

class KisApi:
    def __init__(self, token_manager):
        self.tm = token_manager
        self.base_url = Config().BASE_URL
        
        # [수정 1] self.logger 명시적 선언 (AttributeError 해결)
        self.logger = get_logger("KisApi")
        
        self.headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": "",
            "appkey": Config().APP_KEY,
            "appsecret": Config.APP_SECRET,
            "tr_id": "",
            "custtype": "P"
        }

    def _update_headers(self, tr_id):
        self.headers["authorization"] = f"Bearer {self.tm.get_token()}"
        self.headers["tr_id"] = tr_id
        
        # [모의투자 자동 변환 로직 추가]
        # 실전 TR(T로 시작)을 모의 TR(V로 시작)로 자동 변환
        if "vts" in self.base_url and tr_id.startswith("T"):
            self.headers["tr_id"] = "V" + tr_id[1:]

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
    def get_buyable_cash(self, symbol="AAPL"):
        """
        예수금 조회 (TTTS3007R) - 실제 주문 가능 금액 확인용
        [수정 2] Code 7 에러 해결을 위해 ITEM_CD와 가격 파라미터 수정
        """
        path = "/uapi/overseas-stock/v1/trading/inquire-psamount"
        self._update_headers("TTTS3007R")
        
        params = {
            "CANO": Config.CANO,
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "OVRS_EXCG_CD": "NASD", 
            "OVRS_ORD_UNPR": "0",   # [수정] 빈 문자열("") -> "0"
            "ITEM_CD": symbol       # [수정] 빈 문자열("") -> 대표종목(AAPL)
        }
        
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
            data = res.json()
            
            if data['rt_cd'] == '0':
                output = data['output']
                # frcr_ord_psbl_amt1: 외화주문가능금액 (통합)
                cash = float(output.get('frcr_ord_psbl_amt1', 0))
                return cash
            else:
                self.logger.error(f"❌ 주문가능금액 조회 실패: {data['msg1']} (Code: {data.get('msg_cd')})")
                return 0.0
        except Exception as e:
            self.logger.error(f"❌ API Error (get_buyable_cash): {e}")
            return 0.0

    def buy_limit(self, symbol, price, qty):
        """
        지정가 매수 (기존 코드에서 끊겨있던 부분 복구 및 place_order_final 활용)
        """
        return self.place_order_final("NASD", symbol, "BUY", qty, price)

    @log_api_call("잔고 조회")
    def get_balance(self):
        path = "/uapi/overseas-stock/v1/trading/inquire-balance"
        # _update_headers에서 T->V 변환을 하므로 여기선 실전용 ID만 넣어도 됨
        self._update_headers("TTTS3012R")
        
        params = {
            "CANO": Config.CANO, 
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "OVRS_EXCG_CD": "NASD", 
            "TR_CRCY_CD": "USD", 
            "CTX_AREA_FK200": "", 
            "CTX_AREA_NK200": ""
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
            else:
                self.logger.error(f"❌ 잔고 조회 실패: {data.get('msg1')}")
        except Exception as e:
            self.logger.error(f"❌ 잔고 조회 중 에러: {e}")
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
                if not result:
                    raise ValueError("Ranking data is empty")
                return result
                
        except Exception as e:
            self.logger.warning(f"⚠️ 등락률 조회 실패 또는 데이터 없음: {e}. 거래량 순위로 우회합니다.")
            pass 

        try:
            return self._get_volume_ranking()
        except Exception as e:
            self.logger.error(f"❌ 랭킹 조회 최종 실패: {e}")
            return []

    def _get_volume_ranking(self):
        """[Fallback] 거래량 상위 종목 조회"""
        path = "/uapi/overseas-stock/v1/ranking/trade-vol"
        self._update_headers("HHDFS76310010") 
        params = {
            "AUTH": "", "EXCD": "NAS", "GUBN": "0", "VOL_RANG": "0", "KEYB": ""
        }
        res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
        data = res.json()
        if data['rt_cd'] == '0':
            return data.get('output', [])
        return []

    @log_api_call("현재가 상세 조회")
    def get_current_price(self, symbol, exchange="NAS"):
        """
        [실시간 현재가 조회]
        - 반환값: 현재가(float) 단일 값
        - main.py와의 호환성을 위해 exchange="NAS" 기본값 설정 및 반환 타입 수정
        """
        path = "/uapi/overseas-price/v1/quotations/price-detail"
        self._update_headers("HHDFS76200200")
        
        # exchange가 없으면 NAS(나스닥)으로 간주 (필요 시 로직 추가)
        lookup_excd = self._get_lookup_excd(exchange) 
        
        params = {
            "AUTH": "", 
            "EXCD": lookup_excd, 
            "SYMB": symbol
        }
        
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params, timeout=5)
            data = res.json()
            
            if data['rt_cd'] == '0':
                output = data['output']
                # [중요] 딕셔너리 전체가 아니라 '현재가(last)' 숫자만 반환해야 함!
                return self._safe_float(output.get('last', 0))
            else:
                self.logger.warning(f"⚠️ 현재가 조회 실패 ({symbol}): {data.get('msg1')}")
                return None
                
        except Exception as e:
            self.logger.error(f"❌ 현재가 조회 중 에러 ({symbol}): {e}")
            return None

    @log_api_call("주문 전송")
    def place_order_final(self, exchange, symbol, side, qty, price):
        path = "/uapi/overseas-stock/v1/trading/order"
        is_buy = (side == "BUY")
        
        # 실전투자 ID 기준 (모의투자는 _update_headers에서 자동 변환)
        tr_id = "TTTT1002U" if is_buy else "TTTT1006U"

        self._update_headers(tr_id)

        try:
            f_price = float(price)
            if f_price < 1.0:
                final_price = f"{f_price:.4f}"
            else:
                final_price = f"{f_price:.2f}"
        except:
            final_price = "0"

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
            res = requests.post(f"{self.base_url}{path}", headers=self.headers, json=body, timeout=10)
            data = res.json()
            
            if data['rt_cd'] == '0':
                odno = data['output'].get('ODNO')
                self.logger.info(f"✅ 주문 전송 성공 [{side}] {symbol} {qty}주 (주문번호: {odno})")
                return odno
            else: 
                self.logger.error(f"❌ 주문 실패 ({symbol}): {data.get('msg1')} (Code: {data.get('msg_cd')})")
        except Exception as e: 
            self.logger.error(f"❌ API 통신 에러: {e}")
            
        return None

    def sell_market(self, symbol, qty, price_hint=None):
        """
        시장가 매도 (안전 장치 강화 버전)
        """
        path = "/uapi/overseas-stock/v1/trading/order"
        self._update_headers("TTTT1006U") 

        # 1. 현재가 조회 시도
        current_price = 0.0
        try:
            price_data = self.get_current_price("NASD", symbol)
            if price_data:
                current_price = float(price_data['last'])
        except:
            pass

        # 2. 가격 결정 로직 (수정됨)
        final_price = 0.0
        
        if current_price > 0:
            # 시세 조회 성공: 현재가보다 5% 낮게
            final_price = current_price * 0.95
        elif price_hint and price_hint > 0:
            # [수정] 매수가의 50%는 너무 과격하여 거부됨. -> 15% 할인으로 변경
            self.logger.warning(f"⚠️ [매도] 시세 조회 실패 -> 매수가(${price_hint}) 기준 -15% 가격으로 주문")
            final_price = price_hint * 0.85 
        else:
            self.logger.error(f"🚨 [매도] 가격 정보 전무. 주문 실패 가능성 높음.")
            # 가격 정보가 아예 없으면 0.01로 시도하기보다 안전하게 중단하거나 0 처리
            final_price = 0.0 

        # 가격 포맷팅
        if final_price < 1.0:
            formatted_price = f"{final_price:.4f}"
        else:
            formatted_price = f"{final_price:.2f}"

        data = {
            "CANO": Config.CANO,
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "OVRS_EXCG_CD": "NASD",
            "PDNO": symbol,
            "ORD_DVSN": "00", 
            "ORD_QTY": str(int(qty)),
            "OVRS_ORD_UNPR": formatted_price, 
            "ORD_SVR_DVSN_CD": "0"
        }
        
        try:
            res = requests.post(f"{self.base_url}{path}", headers=self.headers, data=json.dumps(data))
            
            # [수정] JSON 파싱 에러 방어
            try:
                data = res.json()
            except Exception:
                self.logger.error(f"❌ 매도 응답 파싱 실패 (Body: {res.text})")
                return None

            if data['rt_cd'] == '0':
                return data['output']['ODNO']
            else:
                # 에러 메시지 상세 출력
                self.logger.error(f"❌ 매도 실패 [{symbol}]: {data['msg1']} (Code: {data['msg_cd']})")
                return None
        except Exception as e:
            self.logger.error(f"❌ API Error (sell): {e}")
            return None

    def get_minute_candles(self, market, symbol, limit=100):
        path = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
        self._update_headers("HHDFS76950200")
        params = {
            "AUTH": "", "EXCD": "NAS", "SYMB": symbol,
            "NMIN": "1", "PINC": "1", "NEXT": "", "NREC": str(limit), "KEYB": ""
        }
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params, timeout=10)
            data = res.json()
            if data['rt_cd'] == '0' and data.get('output2'):
                df = pd.DataFrame(data['output2'])
                
                df = df.rename(columns={
                    'kymd': 'date', 'khms': 'time',
                    'open': 'open', 'high': 'high', 'low': 'low', 
                    'last': 'close', 
                    'vols': 'volume', 
                    'evol': 'volume'
                })
                
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    if col in df.columns:
                        df[col] = df[col].apply(self._safe_float)
                    
                return df.sort_values('time')
            else:
                self.logger.warning(f"⚠️ 캔들 조회 실패 ({symbol}): {data.get('msg1')}")

        except Exception as e:
            self.logger.error(f"❌ 캔들 데이터 에러: {e}")
            
        return pd.DataFrame()