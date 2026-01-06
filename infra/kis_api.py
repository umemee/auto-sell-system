import requests
import json
import pandas as pd
import time
from config import Config
from infra.utils import get_logger

class KisApi:
    def __init__(self, token_manager):
        self.logger = get_logger("KisApi")
        self.token_manager = token_manager
        self.base_url = Config.BASE_URL
        
        # [핵심 Fix] 계좌번호 강제 정제 로직 (무조건 앞 8자리만 추출)
        # 예: "12345678-01" -> "12345678"
        # 예: "1234567801" -> "12345678"
        raw_account = str(Config.CANO).strip()
        if '-' in raw_account:
            self.account_no = raw_account.split('-')[0]
        elif len(raw_account) > 8:
            self.account_no = raw_account[:8]
        else:
            self.account_no = raw_account
            
        self.logger.info(f"✅ Account No Configured: {self.account_no} (Cleaned)")
    
    def _safe_float(self, val):
        try:
            if val == "" or val is None:
                return 0.0
            return float(val)
        except Exception:
            return 0.0

    def _normalize_market_price(self, market):
        market = market.upper()
        if market in ["NASD", "NASDAQ"]: return "NAS"
        if market in ["NYSE", "NYS"]: return "NYS"
        if market in ["AMEX", "AMS"]: return "AMS"
        return market

    def _get_headers(self, tr_id):
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.token_manager.get_token()}",
            "appkey": Config.APP_KEY,
            "appsecret": Config.APP_SECRET,
            "tr_id": tr_id
        }

    def _send_request(self, method, path, headers, params=None, data=None):
        url = f"{self.base_url}{path}"
        try:
            if method == "GET":
                resp = requests.get(url, headers=headers, params=params, timeout=10)
            else:
                resp = requests.post(url, headers=headers, json=data, timeout=10)
            
            try:
                return resp.json()
            except json.JSONDecodeError:
                self.logger.error(f"Invalid JSON response: {resp.text[:200]}")
                return None
        except Exception as e:
            self.logger.error(f"API Request Failed: {e}")
            return None

    def get_current_price(self, market, symbol):
        market_code = self._normalize_market_price(market)
        path = "/uapi/overseas-price/v1/quotations/price-detail"
        headers = self._get_headers("HHDFS76200200")
        params = {"AUTH": "", "EXCD": market_code, "SYMB": symbol}
        
        res = self._send_request("GET", path, headers, params)
        if res and res.get('output'):
            out = res['output']
            return {
                "symbol": symbol,
                "last": self._safe_float(out.get('last')),
                "open": self._safe_float(out.get('open')),
                "high": self._safe_float(out.get('high')),
                "low": self._safe_float(out.get('low')),
                "base": self._safe_float(out.get('base'))
            }
        return None

    def get_minute_candles(self, market, symbol, limit=100):
        market_code = self._normalize_market_price(market)
        path = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
        headers = self._get_headers("HHDFS76950200")
        params = {
            "AUTH": "", "EXCD": market_code, "SYMB": symbol,
            "NMIN": "1", "PINC": "1", "NEXT": "", "NREC": "120", "KEYB": ""
        }
        res = self._send_request("GET", path, headers, params)
        if res and res.get('output2'):
            df = pd.DataFrame(res['output2'])
            df = df.rename(columns={
                'kymd': 'date', 'khms': 'time',
                'open': 'open', 'high': 'high', 'low': 'low', 'last': 'close', 
                'evol': 'volume' 
            })
            if 'volume' not in df.columns and 'vols' in df.columns:
                 df = df.rename(columns={'vols': 'volume'})
            
            cols = ['open', 'high', 'low', 'close', 'volume']
            valid_cols = [c for c in cols if c in df.columns]
            for col in valid_cols:
                df[col] = df[col].apply(lambda x: self._safe_float(x))
            return df.sort_values('time')
        return pd.DataFrame()

    def get_balance(self):
        path = "/uapi/overseas-stock/v1/trading/inquire-balance"
        headers = self._get_headers("TTTS3012R")
        params = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": "01",
            "OVRS_EXCG_CD": "NASD",
            "TR_CRCY_CD": "USD",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""
        }
        res = self._send_request("GET", path, headers, params)
        holdings = []
        output1 = res.get('output1') if res else []
        if output1:
            for item in output1:
                qty = self._safe_float(item.get('ovrs_cblc_qty'))
                if qty > 0:
                    holdings.append({"symbol": item.get('ovrs_pdno'), "qty": int(qty)})
        return holdings

    def get_buyable_cash(self):
        path = "/uapi/overseas-stock/v1/trading/foreign-margin"
        headers = self._get_headers("TTTC2101R")
        params = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": "01"
        }
        res = self._send_request("GET", path, headers, params)
        max_cash = 0.0
        output = res.get('output') if res else []
        if output:
            for item in output:
                if item.get('crcy_cd') == 'USD':
                    ord_psbl = self._safe_float(item.get('frcr_ord_psbl_amt1'))
                    itgr_psbl = self._safe_float(item.get('itgr_ord_psbl_amt'))
                    max_cash = max(ord_psbl, itgr_psbl)
        return max_cash

    def buy_limit(self, symbol, price, qty):
        # 1. 잔고 체크 (옵션)
        try:
            cash = self.get_buyable_cash()
            est_amt = float(price) * int(qty)
            if est_amt > cash:
                self.logger.warning(f"⚠️ Check: Need ${est_amt:.2f}, Have ${cash:.2f}")
                # return None # 강제 차단 대신 경고만 하고 주문 시도
        except: pass

        path = "/uapi/overseas-stock/v1/trading/order"
        headers = self._get_headers("TTTT1002U") 
        
        # 2. 가격 포맷팅 (소수점 2자리)
        formatted_price = f"{float(price):.2f}"
        
        data = {
            "CANO": self.account_no,  # 여기서 정제된 8자리가 들어감
            "ACNT_PRDT_CD": "01",
            "OVRS_EXCG_CD": "NASD",
            "PDNO": symbol,
            "ORD_DVSN": "00",
            "ORD_QTY": str(qty),
            "ORD_UNPR": formatted_price,
            "ORD_SVR_DVSN_CD": "0"
        }
        
        # [디버깅] 실제 전송 데이터 확인
        self.logger.info(f"📡 Sending Buy Order: {symbol} ${formatted_price} (Acc: {self.account_no})")
        
        res = self._send_request("POST", path, headers, data=data)
        
        if res:
            if res.get('rt_cd') == '0':
                return res.get('output', {}).get('ODNO')
            else:
                # 에러 상세 출력
                self.logger.error(f"❌ Buy Failed: {res.get('msg1')} (Code: {res.get('msg_cd')})")
        return None

    def sell_market(self, symbol, qty):
        path = "/uapi/overseas-stock/v1/trading/order"
        headers = self._get_headers("TTTT1006U")
        data = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": "01",
            "OVRS_EXCG_CD": "NASD",
            "PDNO": symbol,
            "ORD_DVSN": "00", 
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0",
            "ORD_SVR_DVSN_CD": "0"
        }
        res = self._send_request("POST", path, headers, data=data)
        
        if res and res.get('rt_cd') == '0':
            return res.get('output', {}).get('ODNO')
            
        if res and (res.get('msg_cd') == 'IGW00014' or '시장가' in str(res.get('msg1', ''))):
            self.logger.warning("Market Sell Failed. Retrying with Limit Sell...")
            last_price_info = self.get_current_price("NASD", symbol)
            if last_price_info:
                last = last_price_info['last']
                if last > 0:
                    data['ORD_UNPR'] = f"{last * 0.99:.2f}"
                    res_retry = self._send_request("POST", path, headers, data=data)
                    if res_retry and res_retry.get('rt_cd') == '0':
                        self.logger.info("✅ Retry Success")
                        return res_retry.get('output', {}).get('ODNO')

        self.logger.error(f"❌ Sell Failed: {res.get('msg1')}")
        return None