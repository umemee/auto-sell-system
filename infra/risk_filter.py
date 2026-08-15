# infra/risk_filter.py
import datetime
import pytz
import json
import os
from typing import Optional, Set, Tuple
from pathlib import Path
from config import Config
from infra.utils import get_logger

PERSISTENT_LOSS_FILE = "loss_blacklist.json"

class TradeRiskFilter:
    """
    [TradeRiskFilter - 3중 매수 차단 필터 엔진 (영구 보존판)]
    1. 시간대 필터: ET 09:15 ~ 09:30
    2. 주가대 필터: $5.00 <= entry_price < $10.00
    3. 손절 종목 재진입 필터: Config 초기 목록 + JSON 파일 영구 동기화
    """

    def __init__(self, db_conn=None):
        self.logger = get_logger("RiskFilter")
        self.db_conn = db_conn
        self.loss_blacklist: Set[str] = set()
        
        # 설정값 로드
        self.enabled = getattr(Config, 'USE_RISK_FILTER', True)
        self.block_start_et = getattr(Config, 'RISK_TIME_BLOCK_START_ET', "09:15")
        self.block_end_et = getattr(Config, 'RISK_TIME_BLOCK_END_ET', "09:30")
        self.price_band_min = float(getattr(Config, 'RISK_PRICE_BAND_MIN', 5.0))
        self.price_band_max = float(getattr(Config, 'RISK_PRICE_BAND_MAX', 10.0))
        self.block_loss_tickers = getattr(Config, 'BLOCK_PREVIOUS_LOSS_TICKERS', True)

        # 1. Config에 정의된 과거 손절 종목 1차 탑재
        initial_tickers = getattr(Config, 'INITIAL_LOSS_TICKERS', [])
        self.loss_blacklist.update(initial_tickers)

        # 2. 로컬 영구 파일 및 DB 로드
        self._load_persistent_loss_tickers()

    def _load_persistent_loss_tickers(self):
        """파일 및 DB에서 손절 목록 복원"""
        # JSON 파일 로드
        if os.path.exists(PERSISTENT_LOSS_FILE):
            try:
                with open(PERSISTENT_LOSS_FILE, "r", encoding="utf-8") as f:
                    saved_list = json.load(f)
                    if isinstance(saved_list, list):
                        self.loss_blacklist.update(saved_list)
                self.logger.info(f"💾 [RiskFilter] 파일에서 손절 종목 {len(self.loss_blacklist)}개 복원 완료")
            except Exception as e:
                self.logger.error(f"⚠️ [RiskFilter] 손절 목록 파일 로드 실패: {e}")

        # DB 로드 (연결이 있을 경우)
        if self.db_conn:
            try:
                cursor = self.db_conn.cursor()
                cursor.execute("SELECT DISTINCT ticker FROM trade_logs WHERE pnl < 0")
                for row in cursor.fetchall():
                    self.loss_blacklist.add(row[0])
            except Exception as e:
                self.logger.error(f"⚠️ [RiskFilter] 손절 종목 DB 로드 실패: {e}")

    def _save_persistent_loss_tickers(self):
        """손절 목록을 파일에 즉시 영구 저장"""
        try:
            with open(PERSISTENT_LOSS_FILE, "w", encoding="utf-8") as f:
                json.dump(list(self.loss_blacklist), f, indent=4)
        except Exception as e:
            self.logger.error(f"⚠️ [RiskFilter] 손절 목록 파일 저장 실패: {e}")

    def register_trade_result(self, ticker: str, pnl: float, reason: str = ""):
        """손절 발생 시 메모리 및 영구 파일에 실시간 동시 저장"""
        if pnl < 0 or reason in ["STOP_LOSS", "TIME_CUT_LOSS"]:
            self.loss_blacklist.add(ticker)
            self._save_persistent_loss_tickers()
            self.logger.warning(
                f"🚫 [RiskFilter:Blacklist] 손절 발생 종목 영구 등록: {ticker} "
                f"(PnL: {pnl:.2f}%, 사유: {reason}) | 누적 차단 종목: {len(self.loss_blacklist)}개"
            )

    def is_order_blocked(
        self, ticker: str, price: float, current_time_et: Optional[datetime.datetime] = None
    ) -> Tuple[bool, Optional[str]]:
        if not self.enabled:
            return False, None

        if current_time_et is None:
            tz_et = pytz.timezone('US/Eastern')
            current_time_et = datetime.datetime.now(tz_et)
        elif current_time_et.tzinfo is None:
            tz_et = pytz.timezone('US/Eastern')
            current_time_et = tz_et.localize(current_time_et)

        reasons = []

        # 1. 시간대 필터 (ET 09:15 ~ 09:30 / KST 22:15 ~ 22:30)
        start_h, start_m = map(int, self.block_start_et.split(':'))
        end_h, end_m = map(int, self.block_end_et.split(':'))
        cur_min = current_time_et.hour * 60 + current_time_et.minute
        if (start_h * 60 + start_m) <= cur_min <= (end_h * 60 + end_m):
            tz_kst = pytz.timezone('Asia/Seoul')
            cur_kst = current_time_et.astimezone(tz_kst)
            reasons.append(f"TIME_WINDOW (ET {self.block_start_et}~{self.block_end_et} / KST {cur_kst.strftime('%H:%M')})")

        # 2. 주가대 필터 ($5.00 <= price < $10.00)
        if self.price_band_min <= price < self.price_band_max:
            reasons.append(f"PRICE_BAND (${self.price_band_min:.2f}-${self.price_band_max:.2f}, current: ${price:.2f})")

        # 3. 과거 손절 종목 재진입 필터
        if self.block_loss_tickers and (ticker in self.loss_blacklist):
            reasons.append("PREVIOUS_LOSS_TICKER")

        if reasons:
            combined_reason = ", ".join(reasons)
            tz_kst = pytz.timezone('Asia/Seoul')
            now_kst = current_time_et.astimezone(tz_kst)
            self.logger.info(
                f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')}] [BLOCK] "
                f"Ticker: {ticker} | Price: ${price:.2f} | Reason: {combined_reason}"
            )
            return True, combined_reason

        return False, None

    def reset_daily(self):
        """일일 리셋: Config 기본 목록은 유지하면서 파일 동기화"""
        initial_tickers = getattr(Config, 'INITIAL_LOSS_TICKERS', [])
        self.loss_blacklist.clear()
        self.loss_blacklist.update(initial_tickers)
        self._load_persistent_loss_tickers()
        self.logger.info(f"✨ [RiskFilter] 일일 리셋 완료 (유지 중인 차단 종목: {len(self.loss_blacklist)}개)")