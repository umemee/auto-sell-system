# infra/risk_filter.py
import datetime
import pytz
from typing import Optional, Set, Tuple
from config import Config
from infra.utils import get_logger

class TradeRiskFilter:
    """
    [TradeRiskFilter - 3중 매수 차단 필터 엔진]
    기획서 PRD-202608-TRADING-01 완벽 반영
    
    1. 시간대 필터: ET 09:15 ~ 09:30 (KST 22:15 ~ 22:30)
    2. 주가대 필터: $5.00 <= entry_price < $10.00
    3. 손절 종목 재진입 필터: 과거 PnL < 0 손절 종목 동적 블랙리스트
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

        self._load_historical_loss_tickers()

    def _load_historical_loss_tickers(self):
        """DB 또는 파일에서 기존 손절 종목 초기 로드"""
        if self.db_conn:
            try:
                cursor = self.db_conn.cursor()
                cursor.execute("SELECT DISTINCT ticker FROM trade_logs WHERE pnl < 0")
                rows = cursor.fetchall()
                for row in rows:
                    self.loss_blacklist.add(row[0])
                self.logger.info(f"💾 [RiskFilter] DB 손절 블랙리스트 {len(self.loss_blacklist)}개 로드 완료")
            except Exception as e:
                self.logger.error(f"⚠️ [RiskFilter] 손절 종목 로드 중 DB 오류: {e}")

    def register_trade_result(self, ticker: str, pnl: float, reason: str = ""):
        """
        포지션 청산 시 호출되어 손절 발생 종목을 블랙리스트에 실시간 추가
        """
        if pnl < 0 or reason in ["STOP_LOSS", "TIME_CUT_LOSS"]:
            self.loss_blacklist.add(ticker)
            self.logger.warning(f"🚫 [RiskFilter:Blacklist] 손절 발생 종목 등록: {ticker} (PnL: {pnl:.2f}%, 사유: {reason})")

    def is_order_blocked(
        self, ticker: str, price: float, current_time_et: Optional[datetime.datetime] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        매수 발주 직전 3중 필터 검증 함수
        
        Args:
            ticker: 대상 종목 심볼
            price: 진입 예정가 / 현재가
            current_time_et: 미국 동부시간 기준 datetime (미전달 시 자동 계산)
            
        Returns:
            (is_blocked: bool, block_reason_str: Optional[str])
        """
        if not self.enabled:
            return False, None

        if current_time_et is None:
            tz_et = pytz.timezone('US/Eastern')
            current_time_et = datetime.datetime.now(tz_et)
        elif current_time_et.tzinfo is None:
            tz_et = pytz.timezone('US/Eastern')
            current_time_et = tz_et.localize(current_time_et)

        reasons = []

        # =========================================================
        # 1. 시간대 필터 (ET 09:15:00 ~ 09:30:00 / KST 22:15:00 ~ 22:30:00)
        # =========================================================
        start_h, start_m = map(int, self.block_start_et.split(':'))
        end_h, end_m = map(int, self.block_end_et.split(':'))
        
        cur_min_val = current_time_et.hour * 60 + current_time_et.minute
        start_min_val = start_h * 60 + start_m
        end_min_val = end_h * 60 + end_m

        if start_min_val <= cur_min_val <= end_min_val:
            tz_kst = pytz.timezone('Asia/Seoul')
            cur_kst = current_time_et.astimezone(tz_kst)
            reasons.append(
                f"TIME_WINDOW (ET {self.block_start_et}~{self.block_end_et} / KST {cur_kst.strftime('%H:%M')})"
            )

        # =========================================================
        # 2. 주가대 필터 ($5.00 <= price < $10.00)
        # =========================================================
        if self.price_band_min <= price < self.price_band_max:
            reasons.append(
                f"PRICE_BAND (${self.price_band_min:.2f}-${self.price_band_max:.2f}, current: ${price:.2f})"
            )

        # =========================================================
        # 3. 과거 손절 종목 재진입 필터
        # =========================================================
        if self.block_loss_tickers and (ticker in self.loss_blacklist):
            reasons.append("PREVIOUS_LOSS_TICKER")

        # =========================================================
        # 최종 판정 및 로깅
        # =========================================================
        if reasons:
            combined_reason = ", ".join(reasons)
            tz_kst = pytz.timezone('Asia/Seoul')
            now_kst = current_time_et.astimezone(tz_kst)
            
            # 기획서 3.3 로깅 형식 준수
            self.logger.info(
                f"[{now_kst.strftime('%Y-%m-%d %H:%M:%S')}] [BLOCK] "
                f"Ticker: {ticker} | Price: ${price:.2f} | Reason: {combined_reason}"
            )
            return True, combined_reason

        return False, None

    def reset_daily(self):
        """일일 리셋 (세션 전환 시 초기화가 필요한 경우)"""
        self.loss_blacklist.clear()
        self._load_historical_loss_tickers()
        self.logger.info("✨ [RiskFilter] 일일 손절 블랙리스트 초기화 완료")