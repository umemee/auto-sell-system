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
    def _fetch_with_retry(self, path, params, tr_id, method="GET", timeout=3):
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
                self.logger.warning(f"⚠️ API 호출 실패 [{tr_id}]: {msg}")
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
        data = self._fetch_with_retry(path, params, "TTTS3007R", timeout=3)
        
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
        """
        [수정 완료] 분봉 데이터 연속 조회 (Pagination)
        - 해결: KEYB를 '현지 시간'으로 설정하여 120개 제한 돌파
        """
        path = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
        
        # 거래소 코드 변환
        lookup_excd = self._get_lookup_excd(market) if market else "NAS"
        
        all_data = []
        next_key = ""  # 초기값 공백
        
        # [Loop] 목표 개수를 채우거나 더 이상 데이터가 없을 때까지 반복
        while len(all_data) < limit:
            # 첫 요청은 NEXT="", 이후 요청부터는 NEXT="1"
            is_next = "1" if next_key else ""
            
            params = {
                "AUTH": "", 
                "EXCD": lookup_excd, 
                "SYMB": symbol,
                "NMIN": "1", 
                "PINC": "1", 
                "NEXT": is_next, 
                "NREC": "120", 
                "KEYB": next_key  # 현지 시간 기준 키값
            }
            
            # API 호출
            data = self._fetch_with_retry(path, params, "HHDFS76950200", timeout=3)
            
            if not data or not data.get('output2'):
                break
            
            chunk = data['output2']
            if not chunk:
                break

            # -----------------------------------------------------------
            # 🛡️ 무한 루프 방지 (중복 데이터 체크)
            # -----------------------------------------------------------
            if all_data:
                # [기존 데이터 끝] vs [새 데이터 시작] 시간 비교
                last_saved_korea = all_data[-1]['kymd'] + all_data[-1]['khms']
                first_new_korea = chunk[0]['kymd'] + chunk[0]['khms']
                
                # 주의: 경계선 데이터는 시간이 같을 수 있음 (>= 가 아니라 > 로 비교해야 함)
                # 만약 새 데이터가 더 미래라면(=API가 첫 페이지를 다시 줌), 루프 종료
                if first_new_korea > last_saved_korea:
                    self.logger.warning(f"⚠️ [Pagination] 중복/미래 데이터 감지 ({symbol}) -> 수집 종료")
                    break
            # -----------------------------------------------------------
             
            all_data.extend(chunk)
            
            # 목표 개수 충족 시 조기 종료
            if len(all_data) >= limit:
                break
            
            # 데이터가 120개 미만이면 더 이상 과거 데이터가 없는 것
            if len(chunk) < 120:
                break
                
            # -----------------------------------------------------------
            # ✅ [핵심 수정] 다음 조회를 위한 KEYB는 '현지 시간'을 써야 함
            # -----------------------------------------------------------
            last_item = chunk[-1]
            if 'xymd' in last_item and 'xhms' in last_item:
                # 현지 일자 + 현지 시간 (이게 정답)
                next_key = last_item['xymd'] + last_item['xhms']
            else:
                # 비상시 한국 시간 (데이터 없을 경우 대비)
                next_key = last_item['kymd'] + last_item['khms']
            
            time.sleep(0.2) # API 부하 방지
            
        # 데이터프레임 변환
        if not all_data:
            return pd.DataFrame()

        df = pd.DataFrame(all_data)
        
        # 컬럼명 통일
        df = df.rename(columns={
            'kymd': 'date', 'khms': 'time',
            'open': 'open', 'high': 'high', 'low': 'low', 
            'last': 'close', 'vols': 'volume', 'evol': 'volume'
        })
        
        # 숫자 형변환
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = df[col].apply(self._safe_float)
        
        # 정렬: [과거 -> 최신] 순서로 변경
        df = df.iloc[::-1].reset_index(drop=True)
        
        # 요청한 limit만큼 자르기 (최신순 유지)
        if len(df) > limit:
            df = df.iloc[-limit:].reset_index(drop=True)
            
        return df

    # =================================================================
    # 🔫 [주문 관련] 매수/매도 실행 (수정됨)
    # =================================================================

    def buy_limit(self, symbol, price, qty):
        """지정가 매수"""
        # "00"은 지정가(Limit) 코드입니다.
        return self.place_order_final("NASD", symbol, "BUY", qty, price, ord_dvsn="00")

    def buy_market(self, symbol, current_price, qty):
        """
        [신규] 시장가 매수 (사실상 시장가)
        - 급등주 00초 진입 시 주문 거부를 막기 위해 '현재가 + 5%' 지정가로 주문합니다.
        - 이는 가장 확실하게 즉시 체결시키는 방법입니다.
        """
        # 현재가보다 5% 비싸게 주문 -> 매도 호가 전량을 긁으며 즉시 체결됨
        agressive_price = current_price * 1.05 
        return self.place_order_final("NASD", symbol, "BUY", qty, agressive_price, ord_dvsn="00")

    @log_api_call("주문 전송")
    def place_order_final(self, exchange, symbol, side, qty, price, ord_dvsn="00"):
        """
        [수정] ord_dvsn 파라미터 추가 (기본값 "00": 지정가)
        """
        path = "/uapi/overseas-stock/v1/trading/order"
        is_buy = (side == "BUY")
        tr_id = "TTTT1002U" if is_buy else "TTTT1006U"

        # 가격 포맷팅
        try:
            f_price = float(price)
            # 0원이면 시장가(혹은 가격무관)로 간주
            final_price = f"{f_price:.4f}" if f_price < 1.0 else f"{f_price:.2f}"
            if f_price == 0: final_price = "0"
        except:
            final_price = "0"

        exchange_candidates = [exchange]
        if exchange == "NASD":
            exchange_candidates.extend(["AMS", "NYSE"]) 
        
        last_error_msg = ""

        for try_exch in exchange_candidates:
            self._update_headers(tr_id)
            body = {
                "CANO": Config.CANO, 
                "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
                "OVRS_EXCG_CD": try_exch, 
                "PDNO": symbol, 
                "ORD_QTY": str(int(qty)),  
                "OVRS_ORD_UNPR": final_price, 
                "ORD_SVR_DVSN_CD": "0", 
                # [수정] 하드코딩된 "00" 대신 파라미터 사용
                "ORD_DVSN": ord_dvsn 
            }
            
            try:
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
                odno = self.place_order_final("NASD", ticker, "SELL", qty, price, ord_dvsn="00")
        
        elif side == "BUY":
            # [수정] 매수 시 MARKET 옵션 처리 추가
            if order_type == "MARKET" and price:
                 odno = self.buy_market(ticker, price, qty)
            else:
                 odno = self.buy_limit(ticker, price, qty)

        if odno:
            return {'rt_cd': '0', 'msg1': '주문 전송 성공', 'output': {'ODNO': odno}}
        else:
            return {'rt_cd': '1', 'msg1': '주문 전송 실패 (로그 확인)'}
        
        # -------------------------------------------------------------
    # [신규 추가] 데이터 정합성 및 유동성 검증 (공식 문서 기반)
    # -------------------------------------------------------------

    def get_daily_liquidity_status(self, symbol):
        """
        [Ghost Stock Check]
        문서: [해외주식] 기본시세.xlsx - 해외주식 기간별시세
        TR_ID: HHDFS76240000
        """
        path = "/uapi/overseas-price/v1/quotations/dailyprice"
        params = {
            "AUTH": "", 
            "EXCD": "NAS", 
            "SYMB": symbol,
            "GUBN": "0",  # 0: 일봉
            "BYMD": "",   # 공백 시 최근일 기준
            "MODP": "0"   # 0: 수정주가 미적용
        }
        
        # 일봉 데이터 조회
        data = self._fetch_with_retry(path, params, "HHDFS76240000", timeout=3)
        
        if data and data.get('output2'):
            # output2 리스트: [0]=오늘(장중), [1]=어제, [2]=그제 ...
            daily_data = data['output2']
            
            # 최소한 데이터가 2일치 이상은 있어야 '어제' 데이터를 확인 가능
            if len(daily_data) < 2:
                return None 
            
            # 어제 데이터 추출
            yesterday = daily_data[1]
            return {
                'date': yesterday['xymd'], # 문서상 날짜 필드명: xymd
                'close': self._safe_float(yesterday['clos']),
                'volume': self._safe_float(yesterday['tvol'])
            }
        return None

    def get_market_spread(self, symbol):
        """
        [Spread Check] 현재 매수/매도 호가 및 '잔량' 조회
        TR_ID: HHDFS76200100 
        """
        path = "/uapi/overseas-price/v1/quotations/inquire-asking-price"
        params = {
            "AUTH": "", 
            "EXCD": "NAS", 
            "SYMB": symbol
        }
        
        data = self._fetch_with_retry(path, params, "HHDFS76200100", timeout=3)
        
        if data and data.get('output1'):
            # pbid1: 매수 1호가, pask1: 매도 1호가
            ask = self._safe_float(data['output1'].get('pask1')) 
            bid = self._safe_float(data['output1'].get('pbid1')) 
            # [수정] 잔량(Volume)까지 반환해야 main.py의 필터가 작동함
            ask_vol = self._safe_float(data['output1'].get('vask1'))
            bid_vol = self._safe_float(data['output1'].get('vbid1'))
            
            return ask, bid, ask_vol, bid_vol
            
        return 0.0, 0.0, 0.0, 0.0

    def get_pending_orders(self, symbol=None):
        """
        [신규 추가] 미체결 내역 조회 (중복 주문 방지용)
        문서: [해외주식] 미체결내역.csv (TR_ID: TTTS3018R)
        """
        path = "/uapi/overseas-stock/v1/trading/inquire-nccs"
        params = {
            "CANO": Config.CANO,
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "OVRS_EXCG_CD": "NASD",
            "SORT_SQN": "DS", # 내림차순
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": ""
        }
        
        # 미체결 내역 조회
        data = self._fetch_with_retry(path, params, "TTTS3018R", timeout=3)
        
        pending_list = []
        if data and data.get('output'):
            for item in data['output']:
                item_sym = item.get('pdno')
                
                # '매도' 주문이면서 '미체결 수량'이 남아있는 경우만 필터링
                if item.get('sll_buy_dvsn_cd_name') == '매도' and int(item.get('nccs_qty', 0)) > 0:
                    if symbol and symbol != item_sym:
                        continue
                    pending_list.append({
                        "odno": item.get('odno'),
                        "symbol": item_sym,
                        "qty": int(item.get('nccs_qty')),
                        "price": float(item.get('ft_ord_unpr3', 0))
                    })
                    
        return pending_list
    
    def get_recent_candles(self, ticker, limit=400):
        """
        [해외주식 분봉 조회] - 공식 문서 기반 수정 (TR_ID: HHDFS76950200)
        문서 출처: [해외주식] 기본시세.xlsx - 해외주식분봉조회.csv
        """
        # URL 및 TR_ID 설정 (실전 투자 기준)
        path = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
        tr_id = "HHDFS76950200" 

        # [요청 헤더 준비]
        headers = self._get_header(tr_id)

        # [요청 파라미터 준비]
        # EXCD(거래소)는 편의상 'NAS'(나스닥)으로 고정하나, 필요 시 인자로 받아야 함
        params = {
            "AUTH": "",
            "EXCD": "NAS",      # 나스닥(NAS), 뉴욕(NYS), 아멕스(AMS)
            "SYMB": ticker,
            "NMIN": "1",        # 1분봉
            "PINC": "1",        # 전일 포함 ("1" 필수)
            "NEXT": "",         # 처음 조회 시 공백
            "NREC": str(limit), # 최대 120
            "FILL": "",
            "KEYB": ""
        }

        # API 호출
        try:
            res = requests.get(
                url=f"{self.base_url}{path}",
                headers=headers,
                params=params,
                timeout=10
            )
            
            if res.status_code != 200:
                self.logger.error(f"분봉 조회 실패({ticker}): {res.text}")
                return pd.DataFrame()

            data = res.json()
            
            # 응답 코드가 성공이 아니면 빈 DF 반환
            if data['rt_cd'] != '0': 
                return pd.DataFrame()

            if 'output2' in data:
                # [공식 문서 필드명 매핑]
                # tymd: 현지영업일자, xhms: 현지기준시간
                # open: 시가, high: 고가, low: 저가, last: 종가, evol: 체결량
                df = pd.DataFrame(data['output2'])
                
                # 필요한 컬럼만 추출 및 이름 변경
                # API 필드명 -> 내부 사용 변수명
                df = df[['tymd', 'xhms', 'open', 'high', 'low', 'last', 'evol']]
                df.columns = ['date', 'time', 'open', 'high', 'low', 'close', 'volume']
                
                # 데이터 타입 변환 (문자열 -> 숫자)
                cols = ['open', 'high', 'low', 'close', 'volume']
                df[cols] = df[cols].apply(pd.to_numeric)
                
                # 날짜와 시간을 합쳐서 datetime 객체 생성 (정렬을 위해)
                # 예: date='20240222', time='160000' -> '2024-02-22 16:00:00'
                df['datetime'] = pd.to_datetime(df['date'] + df['time'], format='%Y%m%d%H%M%S')
                
                # 시간 역순(최신이 0번)으로 들어오므로, 과거->현재 순으로 정렬
                df = df.sort_values('datetime').reset_index(drop=True)
                
                return df
                
            return pd.DataFrame()
            
        except Exception as e:
            self.logger.error(f"get_recent_candles 예외 발생: {e}")
            return pd.DataFrame()
        
    def _get_header(self, tr_id=None):
        """API 요청용 헤더 생성 헬퍼 (수정 완료)"""
        if tr_id is None:
            raise ValueError("API 요청 시 tr_id는 필수입니다.")
            
        # [수정 포인트]
        # 1. self.token_manager -> self.tm (변수명 일치)
        # 2. get_access_token() -> get_token() (메서드명 일치)
        # 3. self.tm.APP_KEY -> Config.APP_KEY (Config 객체 직접 참조로 안전성 확보)
        
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.tm.get_token()}",
            "appkey": Config.APP_KEY,
            "appsecret": Config.APP_SECRET,
            "tr_id": tr_id
        }
    
    def cancel_order(self, ticker, order_id, qty=0, exchange="NASD"):
        """
        [주문 취소] 거래소 정보를 인자로 받아 유동적으로 처리
        """
        path = "/uapi/overseas-stock/v1/trading/order-rvsecncl"
        tr_id = "TTTT1004U" 

        token = self.tm.get_token()
        if not token.startswith("Bearer"):
            token = f"Bearer {token}"

        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": token,
            "appkey": Config.APP_KEY,
            "appsecret": Config.APP_SECRET,
            "tr_id": tr_id
        }

        # [수정] 인자로 받은 exchange 사용 (기본값 NASD)
        params = {
            "CANO": Config.CANO,
            "ACNT_PRDT_CD": Config.ACNT_PRDT_CD,
            "OVRS_EXCG_CD": exchange, # 여기가 핵심!
            "PDNO": ticker,
            "ORGN_ODNO": order_id, 
            "RVSE_CNCL_DVSN_CD": "02", 
            "ORD_QTY": str(qty) if qty > 0 else "0", 
            "OVRS_ORD_UNPR": "0",
            "ORD_SVR_DVSN_CD": "0"
        }

        try:
            res = requests.post(
                url=f"{self.base_url}{path}",
                headers=headers,
                data=json.dumps(params),
                timeout=5
            )
            return res.json()
        except Exception as e:
            self.logger.error(f"주문 취소 실패: {e}")
            return None