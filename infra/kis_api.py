import sys
import os

# -----------------------------------------------------------
# [필수] 상위 폴더(config.py가 있는 곳)를 인식하도록 경로 강제 추가
# 현재 파일 위치: .../auto-sell/infra/kis_api.py
# 추가할 경로: .../auto-sell/
current_dir = os.path.dirname(os.path.abspath(__file__)) # infra 폴더
root_dir = os.path.dirname(current_dir)                  # auto-sell 폴더 (상위)
sys.path.append(root_dir)                                # 검색 경로에 추가
# -----------------------------------------------------------

import requests
import json
import pandas as pd
from config import Config  # 이제 에러가 나지 않습니다.
from infra.utils import get_logger, log_api_call

logger = get_logger()

class KisApi:
    def __init__(self, token_manager):
        self.tm = token_manager
        self.base_url = Config().BASE_URL
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
    def get_buyable_cash(self):
        """
        예수금 조회 (TTTS3007R) - 실제 주문 가능 금액 확인용
        """
        path = "/uapi/overseas-stock/v1/trading/inquire-psamount"
        self._update_headers("TTTS3007R")
        
        params = {
            "CANO": Config.CANO,
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "OVRS_EXCG_CD": "NASD", # 대표 거래소 설정
            "OVRS_ORD_UNPR": "",
            "ITEM_CD": ""
        }
        
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params)
            data = res.json()
            
            if data['rt_cd'] == '0':
                # output이 딕셔너리인지 리스트인지 확인 (매뉴얼 Scenario 1.3 참조)
                output = data['output']
                # 보통 'frcr_ord_psbl_amt1'(외화주문가능금액 - 통합)을 사용
                # 혹은 'ovrs_ord_psbl_amt' 등 API 버전에 따라 다를 수 있음.
                # 여기서는 가장 안전한 'frcr_ord_psbl_amt1' 사용
                cash = float(output.get('frcr_ord_psbl_amt1', 0))
                return cash
            else:
                self.logger.error(f"주문가능금액 조회 실패: {data['msg1']}")
                return 0.0
        except Exception as e:
            self.logger.error(f"API Error (get_buyable_cash): {e}")
            return 0.0
    def buy_limit(self, symbol, price, qty):
        path = "/uapi/overseas-stock/v1/trading/order"
        self._update_headers("TTTT1002U") # 미국 매수

        # [중요] 미국 주식 호가 단위 규정 준수
        # $1 미만: 소수점 4자리
        # $1 이상: 소수점 2자리
        if price < 1.0:
            formatted_price = f"{price:.4f}"
        else:
            formatted_price = f"{price:.2f}"

        data = {
            "CANO": Config.CANO,
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "OVRS_EXCG_CD": "NASD", # 나스닥 (혹은 종목에 따라 NYS, AMS 확인 필요)
            "PDNO": symbol,
            "ORD_DVSN": "00", # 지정가
            "ORD_QTY": str(int(qty)),
            "OVRS_ORD_UNPR": formatted_price, # [Scenario 1.1 Fix]
            "ORD_SVR_DVSN_CD": "0"
        }

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
                            "price": self._safe_float(item.get('ovrs_stck_evlu_amt')),  # 평가금액 (qty * 현재가)
                            "pnl_pct": self._safe_float(item.get('frcr_evlu_pfls_rt'))  # 수익률 (%)
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

    @log_api_call("현재가 상세 조회")
    def get_current_price(self, exchange, symbol):
        # [수정] URL 변경: price -> price-detail (상세 시세)
        path = "/uapi/overseas-price/v1/quotations/price-detail"
        
        # [수정] TR_ID 변경: HHDFS00000300(기본) -> HHDFS76200200(상세)
        self._update_headers("HHDFS76200200")
        
        lookup_excd = self._get_lookup_excd(exchange) 
        
        params = {
            "AUTH": "", 
            "EXCD": lookup_excd, 
            "SYMB": symbol
        }
        
        try:
            res = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params, timeout=10)
            data = res.json()
            
            if data['rt_cd'] == '0':
                output = data['output']
                # [수정] 상세 API는 open, high, low를 모두 제공합니다.
                return {
                    "last": self._safe_float(output.get('last', 0)),
                    "open": self._safe_float(output.get('open', 0)),
                    "high": self._safe_float(output.get('high', 0)),
                    "low": self._safe_float(output.get('low', 0)),
                    "volume": int(self._safe_float(output.get('tvol', 0)))
                }
            else:
                logger.warning(f"⚠️ 현재가 조회 실패 ({symbol}): {data.get('msg1')} (Code: {data.get('msg_cd')})")
                
        except Exception as e:
            logger.error(f"❌ 현재가 조회 중 에러 ({symbol}): {e}")
            
        return None

    @log_api_call("주문 전송")
    def place_order_final(self, exchange, symbol, side, qty, price):
        path = "/uapi/overseas-stock/v1/trading/order"
        is_buy = (side == "BUY")
        
        # [수정 1] TR_ID 명확화 (모의투자 매도 ID 변경 가능성 대응)
        if "vts" in self.base_url:
            # 모의투자: 매수 VTTT1002U / 매도 VTTT1006U (기존 1001U에서 변경 권장)
            tr_id = "VTTT1002U" if is_buy else "VTTT1006U"
        else:
            # 실전투자: 매수 TTTT1002U / 매도 TTTT1006U
            tr_id = "TTTT1002U" if is_buy else "TTTT1006U"

        self._update_headers(tr_id)

        # [수정 2] 가격 포맷팅 강화 (Tick Size 오류 방지)
        try:
            f_price = float(price)
            # 1달러 미만은 소수점 4자리, 이상은 2자리 (미국 주식 일반적 규칙)
            if f_price < 1.0:
                final_price = f"{f_price:.4f}"
            else:
                final_price = f"{f_price:.2f}"
        except:
            final_price = "0" # 예외 시 0 처리 (시장가 등)

        body = {
            "CANO": Config.CANO, 
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "OVRS_EXCG_CD": exchange, 
            "PDNO": symbol, 
            "ORD_QTY": str(int(qty)),  # 정수 문자열 변환 필수
            "OVRS_ORD_UNPR": final_price, 
            "ORD_SVR_DVSN_CD": "0", 
            "ORD_DVSN": "00" # 지정가
        }
        
        try:
            res = requests.post(f"{self.base_url}{path}", headers=self.headers, json=body, timeout=10)
            data = res.json()
            
            if data['rt_cd'] == '0':
                odno = data['output'].get('ODNO')
                logger.info(f"✅ 주문 전송 성공 [{side}] {symbol} {qty}주 (주문번호: {odno})")
                return odno
            else: 
                logger.error(f"❌ 주문 실패 ({symbol}): {data.get('msg1')} (Code: {data.get('msg_cd')})")
        except Exception as e: 
            logger.error(f"❌ API 통신 에러: {e}")
            
        return None

    def sell_market(self, symbol, qty):
        """
        [수정] 시장가 매도 (강제 청산)
        - 현재가를 못 가져와도 무조건 매도 주문을 냅니다.
        """
        try:
            # 1. 현재가 조회 시도
            price_info = self.get_current_price("NASD", symbol)
            
            limit_price = 0.0
            if price_info and price_info['last'] > 0:
                # 현재가가 있으면 10% 아래로 던짐 (확실한 체결)
                limit_price = price_info['last'] * 0.90
            else:
                # [핵심] 현재가 조회 실패 시, 0.01달러(최저가)로 던짐 -> 시장가 효과
                logger.warning(f"🚨 {symbol} 시세 조회 실패! 최저가 강제 매도 시도")
                limit_price = 0.01 

            return self.place_order_final("NASD", symbol, "SELL", qty, limit_price)
            
        except Exception as e:
            logger.error(f"❌ 시장가 매도 로직 에러: {e}")
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
                
                # [수정] 거래량 필드명을 'vols'와 'evol' 모두 대응하도록 처리
                df = df.rename(columns={
                    'kymd': 'date', 'khms': 'time',
                    'open': 'open', 'high': 'high', 'low': 'low', 
                    'last': 'close', 
                    'vols': 'volume', 
                    'evol': 'volume'  # 해외주식 분봉 특화
                })
                
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    if col in df.columns:
                        df[col] = df[col].apply(self._safe_float)
                    
                return df.sort_values('time')
            else:
                logger.warning(f"⚠️ 캔들 조회 실패 ({symbol}): {data.get('msg1')}")

        except Exception as e:
            logger.error(f"❌ 캔들 데이터 에러: {e}")
            
        return pd.DataFrame()

    # [DEPRECATED] 미구현 함수 - 사용 안 함
    # def get_daily_candle(self, exchange, symbol, period=100): 
    #     return pd. DataFrame()