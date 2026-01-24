# infra/kis_api.py
import sys
import os
import requests
import json
import pandas as pd
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# -----------------------------------------------------------
# [필수] 상위 폴더(config.py가 있는 곳)를 인식하도록 경로 강제 추가
# -----------------------------------------------------------
current_dir = os.path.dirname(os.path.abspath(__file__)) 
root_dir = os.path.dirname(current_dir)                  
sys.path.append(root_dir)                                

from config import Config
from infra.utils import get_logger, log_api_call

class KisApi:
    """
    [한국투자증권 API 래퍼 클래스 v5.3]
    - 핵심 변경사항: 'Smart Retry' 로직 도입
    - 역할: 시세 조회, 잔고 확인, 주문 전송 등 서버와의 모든 통신 담당
    - 안전장치: 네트워크 불안정(Timeout) 시 즉시 포기하지 않고 3회 재시도 수행
    """
    def __init__(self, token_manager):
        self.tm = token_manager
        self.base_url = Config().BASE_URL
        
        # 로거 설정
        self.logger = get_logger("KisApi")
        
        self.headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": "",
            "appkey": Config().APP_KEY,
            "appsecret": Config.APP_SECRET,
            "tr_id": "",
            "custtype": "P"
        }
        
        # [Smart Retry] 세션 설정 (HTTP 연결 풀링 및 재시도)
        # requests.get을 매번 새로 만드는 것보다 Session을 쓰면 훨씬 빠르고 안정적입니다.
        self.session = requests.Session()
        retries = Retry(
            total=3,                # 최대 3번 재시도
            backoff_factor=0.3,     # 0.3초, 0.6초, 1.2초... 간격으로 대기
            status_forcelist=[500, 502, 503, 504], # 서버 에러 시 재시도
            allowed_methods=["GET"] # GET 요청만 재시도 (주문(POST)은 중복 위험으로 제외)
        )
        self.session.mount('https://', HTTPAdapter(max_retries=retries))

    def _update_headers(self, tr_id):
        """API 호출 전 토큰과 TR_ID(거래코드)를 헤더에 갱신"""
        self.headers["authorization"] = f"Bearer {self.tm.get_token()}"
        self.headers["tr_id"] = tr_id
        
        # [모의투자 자동 변환 로직]
        if "vts" in self.base_url and tr_id.startswith("T"):
            self.headers["tr_id"] = "V" + tr_id[1:]

    def _safe_float(self, val):
        """문자열 숫자를 안전하게 float로 변환"""
        try:
            if not val: return 0.0
            return float(str(val).replace(",", ""))
        except Exception:
            return 0.0
            
    def _get_lookup_excd(self, exchange):
        """거래소 코드 변환 (NASD -> NAS)"""
        excd_map = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}
        return excd_map.get(exchange, exchange)

    # =================================================================
    # 🛠️ [핵심] 스마트 요청 처리기 (Smart Request Handler)
    # =================================================================
    def _fetch_with_retry(self, path, params, tr_id, method="GET", timeout=5):
        """
        [공통 함수] 모든 조회 요청은 이 함수를 거쳐갑니다.
        - 자동으로 헤더를 갱신하고
        - 타임아웃 발생 시 재시도하며
        - 에러를 우아하게(Graceful) 처리합니다.
        """
        self._update_headers(tr_id)
        url = f"{self.base_url}{path}"
        
        try:
            # Session을 사용하여 재시도 로직 적용
            if method == "GET":
                res = self.session.get(url, headers=self.headers, params=params, timeout=timeout)
            else:
                # POST는 재시도 로직을 함부로 쓰면 안 됨 (주문 중복 위험)
                res = requests.post(url, headers=self.headers, json=params, timeout=timeout)
            
            # 응답 코드가 200이 아니면 에러 발생
            res.raise_for_status()
            
            # JSON 파싱
            data = res.json()
            
            # KIS API 자체 에러 코드 확인 (rt_cd가 0이 아니면 실패)
            if data.get('rt_cd') != '0':
                # 단, 장 종료 등 흔한 메시지는 로그 레벨을 낮출 수 있음
                msg = data.get('msg1')
                # self.logger.warning(f"⚠️ API 호출 실패 [{tr_id}]: {msg}")
                return None
                
            return data
            
        except requests.exceptions.Timeout:
            self.logger.error(f"⏳ [Timeout] 요청 시간 초과: {tr_id}")
            return None
        except requests.exceptions.RequestException as e:
            self.logger.error(f"💥 [Network Error] 통신 실패: {e}")
            return None
        except json.JSONDecodeError:
            self.logger.error(f"📝 [JSON Error] 응답 데이터 파싱 실패")
            return None

    # =================================================================
    # 💰 [자산 관련] 예수금 및 잔고 조회
    # =================================================================

    @log_api_call("예수금 조회(주문가능)")
    def get_buyable_cash(self, symbol="AAPL"):
        """예수금 조회 (재시도 로직 적용됨)"""
        path = "/uapi/overseas-stock/v1/trading/inquire-psamount"
        params = {
            "CANO": Config.CANO,
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "OVRS_EXCG_CD": "NASD", 
            "OVRS_ORD_UNPR": "0",
            "ITEM_CD": symbol
        }
        
        # [Smart Retry] 적용
        data = self._fetch_with_retry(path, params, "TTTS3007R", timeout=5)
        
        if data:
            return float(data['output'].get('frcr_ord_psbl_amt1', 0))
        return 0.0

    @log_api_call("잔고 조회")
    def get_balance(self):
        """실시간 잔고 조회 (재시도 로직 적용됨)"""
        path = "/uapi/overseas-stock/v1/trading/inquire-balance"
        params = {
            "CANO": Config.CANO, 
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "OVRS_EXCG_CD": "NASD", 
            "TR_CRCY_CD": "USD", 
            "CTX_AREA_FK200": "", 
            "CTX_AREA_NK200": ""
        }
        
        # [Smart Retry] 적용 (데이터가 크므로 timeout 10초)
        data = self._fetch_with_retry(path, params, "TTTS3012R", timeout=10)
        
        holdings = []
        if data:
            output1 = data.get('output1', [])
            for item in output1:
                qty = self._safe_float(item.get('ovrs_cblc_qty'))
                if qty > 0:
                    avg_price = self._safe_float(item.get('pchs_avg_pric'))
                    holdings.append({
                        "symbol": item.get('ovrs_pdno'),
                        "qty": qty,
                        "price": avg_price,
                        "pnl_pct": self._safe_float(item.get('frcr_evlu_pfls_rt'))
                    })
        return holdings

    # =================================================================
    # 🔍 [시장 데이터] 랭킹 및 시세 조회
    # =================================================================

    @log_api_call("랭킹 조회(통합)")
    def get_ranking(self):
        """
        급등주 랭킹 조회 (등락률 상위)
        - 실패 시 거래량 상위 랭킹(Fallback)으로 자동 전환
        """
        path = "/uapi/overseas-stock/v1/ranking/updown-rate" 
        params = {
            "AUTH": "", "EXCD": "NAS", "GUBN": "1", "NDAY": "0", 
            "VOL_RANG": "0", "KEYB": ""
        }
        
        # [Smart Retry] 적용
        data = self._fetch_with_retry(path, params, "HHDFS76290000", timeout=10)
        
        if data and data.get('output2'):
            return data.get('output2')

        # 1차 조회 실패 시 백업 로직 실행 (로그 남김)
        self.logger.warning("⚠️ 등락률 순위 조회 실패 -> 거래량 순위로 우회 시도")
        return self._get_volume_ranking()

    def _get_volume_ranking(self):
        """[Fallback] 거래량 상위 종목 조회"""
        path = "/uapi/overseas-stock/v1/ranking/trade-vol"
        params = {
            "AUTH": "", "EXCD": "NAS", "GUBN": "0", "VOL_RANG": "0", "KEYB": ""
        }
        
        # [Smart Retry] 여기도 적용해야 완벽합니다.
        data = self._fetch_with_retry(path, params, "HHDFS76310010", timeout=5)
        
        if data and data.get('output'):
            return data.get('output')
        
        self.logger.error("❌ 랭킹 조회 최종 실패 (등락률 & 거래량 모두 응답 없음)")
        return []

    @log_api_call("현재가 상세 조회")
    def get_current_price(self, symbol, exchange="NAS"):
        """실시간 현재가 조회"""
        path = "/uapi/overseas-price/v1/quotations/price-detail"
        lookup_excd = self._get_lookup_excd(exchange) 
        params = {
            "AUTH": "", "EXCD": lookup_excd, "SYMB": symbol
        }
        
        data = self._fetch_with_retry(path, params, "HHDFS76200200", timeout=5)
        
        if data:
            return self._safe_float(data['output'].get('last', 0))
        return None

    def get_minute_candles(self, market, symbol, limit=400):
        """분봉 데이터 조회"""
        path = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
        params = {
            "AUTH": "", "EXCD": "NAS", "SYMB": symbol,
            "NMIN": "1", "PINC": "1", "NEXT": "", "NREC": str(limit), "KEYB": ""
        }
        
        data = self._fetch_with_retry(path, params, "HHDFS76950200", timeout=10)
        
        if data and data.get('output2'):
            df = pd.DataFrame(data['output2'])
            df = df.rename(columns={
                'kymd': 'date', 'khms': 'time',
                'open': 'open', 'high': 'high', 'low': 'low', 
                'last': 'close', 'vols': 'volume', 'evol': 'volume'
            })
            for col in ['open', 'high', 'low', 'close', 'volume']:
                if col in df.columns:
                    df[col] = df[col].apply(self._safe_float)
            return df.sort_values('time')
            
        return pd.DataFrame()

    # =================================================================
    # 🔫 [주문 관련] 매수/매도 실행
    # =================================================================

    def buy_limit(self, symbol, price, qty):
        """지정가 매수"""
        return self.place_order_final("NASD", symbol, "BUY", qty, price)

    @log_api_call("주문 전송")
    def place_order_final(self, exchange, symbol, side, qty, price):
        """
        [Smart Order] 거래소 자동 감지 및 주문 전송
        - 주문은 재시도(Retry)를 함부로 하면 중복 체결 위험이 있으므로
        - 기존 방식대로 거래소를 변경(Fail-over)하는 방식만 유지합니다.
        """
        path = "/uapi/overseas-stock/v1/trading/order"
        is_buy = (side == "BUY")
        tr_id = "TTTT1002U" if is_buy else "TTTT1006U"

        # 가격 포맷팅
        try:
            f_price = float(price)
            final_price = f"{f_price:.4f}" if f_price < 1.0 else f"{f_price:.2f}"
        except:
            final_price = "0"

        # 시도할 거래소 목록 (NASD -> AMS -> NYSE)
        exchange_candidates = [exchange]
        if exchange == "NASD":
            exchange_candidates.extend(["AMS", "NYSE"]) 
        
        last_error_msg = ""

        for try_exch in exchange_candidates:
            # 주문은 POST 요청이므로 _fetch_with_retry를 쓰지 않고 직접 호출
            # (주문 중복 방지를 위해 requests.post를 1회만 시도)
            self._update_headers(tr_id)
            body = {
                "CANO": Config.CANO, 
                "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
                "OVRS_EXCG_CD": try_exch, 
                "PDNO": symbol, 
                "ORD_QTY": str(int(qty)),  
                "OVRS_ORD_UNPR": final_price, 
                "ORD_SVR_DVSN_CD": "0", 
                "ORD_DVSN": "00"
            }
            
            try:
                # [Safety] 주문 타임아웃 10초
                res = requests.post(f"{self.base_url}{path}", headers=self.headers, json=body, timeout=10)
                data = res.json()
                
                if data['rt_cd'] == '0':
                    odno = data['output'].get('ODNO')
                    self.logger.info(f"✅ 주문 성공 ({try_exch}) [{side}] {symbol} {qty}주 #{odno}")
                    return odno
                else:
                    msg = data.get('msg1')
                    code = data.get('msg_cd')
                    self.logger.warning(f"⚠️ 주문 실패 ({try_exch}): {msg} (Code: {code}) -> 거래소 변경")
                    last_error_msg = f"{msg} ({code})"
                    
            except Exception as e: 
                self.logger.error(f"❌ 주문 통신 에러 ({try_exch}): {e}")
                last_error_msg = str(e)
            
            # 너무 빠른 거래소 변경 방지
            time.sleep(0.2)

        self.logger.error(f"❌ 최종 주문 실패 ({symbol}): {last_error_msg}")
        return None

    def sell_market(self, symbol, qty, price_hint=None):
        """시장가(현재가 -5% 지정가) 매도"""
        # 현재가 조회 (여기서는 _fetch_with_retry 덕분에 내부적으로 3회 시도됨)
        current_price = self.get_current_price(symbol, exchange="NAS")
        
        final_price = 0.0
        if current_price and current_price > 0:
            final_price = current_price * 0.95 
        elif price_hint and price_hint > 0:
            self.logger.warning(f"⚠️ 시세 조회 실패 -> 장부가(${price_hint}) 기준 -5% 주문")
            final_price = price_hint * 0.95
        else:
            self.logger.error(f"🚨 [매도 불가] 가격 정보 없음")
            return None 

        return self.place_order_final("NASD", symbol, "SELL", qty, final_price)

    def send_order(self, ticker, side, qty, price=None, order_type="MARKET"):
        """[호환성 래퍼] RealOrderManager용"""
        odno = None
        if side == "SELL":
            if order_type == "MARKET" or not price or price <= 0:
                odno = self.sell_market(ticker, qty)
            else:
                odno = self.place_order_final("NASD", ticker, "SELL", qty, price)
        elif side == "BUY":
            odno = self.buy_limit(ticker, price, qty)

        if odno:
            return {'rt_cd': '0', 'msg1': '주문 전송 성공', 'output': {'ODNO': odno}}
        else:
            return {'rt_cd': '1', 'msg1': '주문 전송 실패 (로그 확인)'}