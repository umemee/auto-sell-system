# infra/virtual_combined_portfolio.py
"""
GapZone Virtual Combined Portfolio Engine (Baseline FIFO Shared)
================================================================
실시간 시세(호가/1분봉) 기반 가상 매매(Paper Trading) 독립 엔진.

핵심 원칙:
1. 실계좌 주문(KIS API) 완전 차단 (Sandbox / Mock Execution).
2. 자본 100% 단일 공유 풀 (초기 자본: $2,000.0, Zero Cash Drag).
3. Baseline FIFO Shared (선착순 집행):
   - EMA (전략1): 최대 2슬롯 (슬롯당 가용 자본의 50% 배분)
   - Alpha (전략2): 최대 1슬롯 (잔여 가용 자본 전액 스윕, Sweep Remaining)
   - 동일 도착 시각 처리: EMA 우선 순회
4. 실시간 호가 기반 가상 체결 (Virtual Fill):
   - 매수: Ask 1호가 기반 가상 슬리피지 0.1% (+0.0010) 적용
   - 매도: Bid 1호가 기반 가상 슬리피지 0.1% (-0.0010) 적용
   - SEC Rule 612 틱 사이즈 자동 보정 ($1.00 이상 2자리, $1.00 미만 4자리)
5. 실시간 괴리 분석 로그 적재:
   - logs/paper/execution_drift_log.csv
"""

import os
import sys
import time
import csv
import logging
import datetime
import pytz
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config import Config
from infra.utils import get_logger, round_price


class VirtualCombinedPortfolio:
    """
    단일 공유 자본 풀 기반 가상 결합 포트폴리오 엔진
    """
    def __init__(
        self,
        initial_capital: float = 2000.0,
        fee_rate: float = 0.001,
        slippage_rate: float = 0.001,
        log_dir: Optional[Path] = None
    ):
        self.logger = get_logger("VirtualCombinedPortfolio")
        self.initial_capital = float(initial_capital)
        self.balance = float(initial_capital)
        self.fee_rate = float(fee_rate)          # 0.1% 편도 수수료
        self.slippage_rate = float(slippage_rate) # 0.1% 가상 슬리피지

        # 슬롯 제한
        self.MAX_EMA_SLOTS = 2
        self.MAX_ALPHA_SLOTS = 1

        # 포지션 및 거래 상태
        self.positions: Dict[str, Dict[str, Any]] = {}  # {ticker: position_dict}
        self.closed_trades: List[Dict[str, Any]] = []
        self.daily_realized_pnl = 0.0

        # 로그 디렉토리 및 괴리 로그 CSV 경로
        if log_dir is None:
            self.log_dir = BASE_DIR / "logs" / "paper"
        else:
            self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.drift_log_path = self.log_dir / "execution_drift_log.csv"

        self._init_drift_log_header()
        self.logger.info(
            f"🧪 [VirtualCombinedPortfolio Init] 초기 자본: ${self.initial_capital:,.2f} | "
            f"수수료: {self.fee_rate*100:.2f}% | 슬리피지: {self.slippage_rate*100:.2f}% | "
            f"엔진 모드: Baseline FIFO Shared"
        )

    def _init_drift_log_header(self):
        """괴리 분석 로그 CSV 헤더 초기화"""
        fieldnames = [
            "timestamp", "strategy", "ticker", "side",
            "signal_price", "virtual_fill_price", "slippage_pct",
            "capital_state_before", "executed_qty", "fill_amount",
            "skip_reason", "realized_pnl", "return_pct", "trigger_reason"
        ]
        if not self.drift_log_path.exists() or self.drift_log_path.stat().st_size == 0:
            with open(self.drift_log_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()

    def get_current_timestamps(self) -> Tuple[str, int]:
        """밀리초 단위 정밀 타임스탬프 반환"""
        now = datetime.datetime.now(pytz.timezone('US/Eastern'))
        ms_epoch = int(time.time() * 1000)
        return now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], ms_epoch

    @property
    def total_equity(self) -> float:
        """체결 기준 총자산 (현금 잔고 + 보유 포지션 평가액)"""
        pos_val = sum(
            p['qty'] * p.get('current_price', p['entry_price'])
            for p in self.positions.values()
        )
        return self.balance + pos_val

    @property
    def used_capital_fraction(self) -> float:
        """현재 사용 중인 자본 비율 (0.0 ~ 1.0)"""
        return sum(p.get('capital_frac', 0.0) for p in self.positions.values())

    @property
    def available_capital_fraction(self) -> float:
        """가용 자본 비율 (0.0 ~ 1.0)"""
        return max(0.0, 1.0 - self.used_capital_fraction)

    def get_capital_state_summary(self) -> str:
        """현재 자본 상태 요약 문자열 반환"""
        ema_cnt = len([p for p in self.positions.values() if p['strategy'] == 'EMA'])
        alpha_cnt = len([p for p in self.positions.values() if p['strategy'] == 'ALPHA'])
        return (
            f"Equity: ${self.total_equity:.2f}, AvailFrac: {self.available_capital_fraction:.2f}, "
            f"Slots(E:{ema_cnt}/{self.MAX_EMA_SLOTS}, A:{alpha_cnt}/{self.MAX_ALPHA_SLOTS})"
        )

    def can_admit_signal(self, strategy: str, ticker: str) -> Tuple[bool, str, float]:
        """
        Baseline FIFO Shared 규칙에 따른 신호 수용 가능 여부 판정
        반환값: (수용가능여부, 사유, 배분비율)
        """
        strat_upper = strategy.upper()
        if ticker in self.positions:
            return False, "ALREADY_IN_POSITION", 0.0

        avail = self.available_capital_fraction

        if strat_upper == "EMA":
            ema_positions = [p for p in self.positions.values() if p['strategy'] == 'EMA']
            if len(ema_positions) >= self.MAX_EMA_SLOTS:
                return False, "MAX_SLOTS_REACHED", 0.0

            if avail < (0.5 - 1e-9):
                return False, "INSUFFICIENT_CAPITAL", 0.0

            return True, "APPROVED", 0.5

        elif strat_upper == "ALPHA":
            alpha_positions = [p for p in self.positions.values() if p['strategy'] == 'ALPHA']
            if len(alpha_positions) >= self.MAX_ALPHA_SLOTS:
                return False, "MAX_SLOTS_REACHED", 0.0

            if avail <= 1e-9:
                return False, "INSUFFICIENT_CAPITAL", 0.0

            # 잔여 가용자본 전부 스윕 (Sweep Remaining)
            return True, "APPROVED", avail

        else:
            return False, f"UNKNOWN_STRATEGY_{strategy}", 0.0

    def log_drift_record(
        self,
        strategy: str,
        ticker: str,
        side: str,
        signal_price: float,
        virtual_fill_price: float,
        capital_state_before: str,
        executed_qty: int = 0,
        fill_amount: float = 0.0,
        skip_reason: str = "NONE",
        realized_pnl: float = 0.0,
        return_pct: float = 0.0,
        trigger_reason: str = "SIGNAL",
        timestamp_str: Optional[str] = None
    ):
        """괴리 분석 로그 파일(execution_drift_log.csv)에 실시간 기록"""
        if timestamp_str is None:
            timestamp_str, _ = self.get_current_timestamps()

        # 슬리피지율 계산 (%)
        if signal_price > 0 and virtual_fill_price > 0:
            if side == "BUY":
                slippage_pct = round(((virtual_fill_price - signal_price) / signal_price) * 100.0, 4)
            else:
                slippage_pct = round(((signal_price - virtual_fill_price) / signal_price) * 100.0, 4)
        else:
            slippage_pct = 0.0

        record = {
            "timestamp": timestamp_str,
            "strategy": strategy.upper(),
            "ticker": ticker,
            "side": side.upper(),
            "signal_price": round(signal_price, 4),
            "virtual_fill_price": round(virtual_fill_price, 4),
            "slippage_pct": slippage_pct,
            "capital_state_before": capital_state_before,
            "executed_qty": executed_qty,
            "fill_amount": round(fill_amount, 2),
            "skip_reason": skip_reason,
            "realized_pnl": round(realized_pnl, 2),
            "return_pct": round(return_pct, 2),
            "trigger_reason": trigger_reason
        }

        try:
            with open(self.drift_log_path, "a", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=record.keys())
                writer.writerow(record)
        except Exception as e:
            self.logger.error(f"⚠️ [DriftLog Write Error] {ticker}: {e}")

    def execute_virtual_buy(
        self,
        strategy: str,
        ticker: str,
        signal_price: float,
        ask: float = 0.0,
        bid: float = 0.0,
        volume: int = 0,
        timestamp: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        가상 매수 주문 집행 (실시간 호가 기반 가상 슬리피지 0.1% 반영)
        """
        ts_str = timestamp or self.get_current_timestamps()[0]
        cap_state = self.get_capital_state_summary()

        # 1. 수용 가능 여부 판정
        admitted, reason, alloc_frac = self.can_admit_signal(strategy, ticker)
        if not admitted:
            self.logger.warning(
                f"🚫 [Paper Skip] {strategy} {ticker} 진입 거부 ({reason}) | {cap_state}"
            )
            self.log_drift_record(
                strategy=strategy,
                ticker=ticker,
                side="BUY",
                signal_price=signal_price,
                virtual_fill_price=0.0,
                capital_state_before=cap_state,
                skip_reason=reason,
                trigger_reason="BUY_SIGNAL",
                timestamp_str=ts_str
            )
            return {'status': 'skipped', 'reason': reason, 'ticker': ticker}

        # 2. 가상 체결가 계산 (호가 + 0.1% 슬리피지)
        base_price = ask if ask > 0 else signal_price
        fill_price = round_price(base_price * (1.0 + self.slippage_rate))

        # 3. 배분 금액 및 수량 계산 ($2,000 Hard Cap 적용)
        nominal_alloc = self.total_equity * alloc_frac
        hard_cap = getattr(Config, 'MAX_SINGLE_ORDER_AMOUNT', 2000.0)
        if hard_cap and hard_cap > 0:
            alloc_dollars = min(nominal_alloc, hard_cap)
        else:
            alloc_dollars = nominal_alloc

        qty = int(alloc_dollars // fill_price) if fill_price > 0 else 0
        if qty <= 0:
            self.logger.warning(
                f"🚫 [Paper Skip] {strategy} {ticker} 0주 라운딩 스킵 (배분 ${alloc_dollars:.2f} < 주가 ${fill_price:.4f})"
            )
            self.log_drift_record(
                strategy=strategy,
                ticker=ticker,
                side="BUY",
                signal_price=signal_price,
                virtual_fill_price=fill_price,
                capital_state_before=cap_state,
                skip_reason="ZERO_QTY_ROUNDING",
                trigger_reason="BUY_SIGNAL",
                timestamp_str=ts_str
            )
            return {'status': 'skipped', 'reason': 'ZERO_QTY_ROUNDING', 'ticker': ticker}

        cost = qty * fill_price
        fee = cost * self.fee_rate
        self.balance -= (cost + fee)

        position_record = {
            'strategy': strategy.upper(),
            'ticker': ticker,
            'qty': qty,
            'entry_price': fill_price,
            'signal_price': signal_price,
            'entry_time': ts_str,
            'capital_frac': alloc_frac,
            'cost': cost,
            'entry_fee': fee,
            'current_price': fill_price,
            'highest_price': fill_price
        }
        self.positions[ticker] = position_record

        self.logger.info(
            f"⚡ [Paper BUY Filled] {strategy} {ticker} | {qty}주 @ ${fill_price:.4f} "
            f"(신호가: ${signal_price:.4f}, 슬리피지: +{((fill_price-signal_price)/signal_price)*100:.2f}%) | "
            f"투자금: ${cost:,.2f} | 잔여현금: ${self.balance:,.2f}"
        )

        self.log_drift_record(
            strategy=strategy,
            ticker=ticker,
            side="BUY",
            signal_price=signal_price,
            virtual_fill_price=fill_price,
            capital_state_before=cap_state,
            executed_qty=qty,
            fill_amount=cost,
            skip_reason="NONE",
            trigger_reason="BUY_SIGNAL",
            timestamp_str=ts_str
        )

        return {
            'status': 'filled',
            'strategy': strategy,
            'ticker': ticker,
            'qty': qty,
            'fill_price': fill_price,
            'cost': cost,
            'capital_frac': alloc_frac
        }

    def execute_virtual_sell(
        self,
        ticker: str,
        reason: str,
        signal_price: float,
        bid: float = 0.0,
        ask: float = 0.0,
        timestamp: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        가상 매도 주문 집행 (실시간 Bid 호가 - 0.1% 슬리피지 반영)
        """
        if ticker not in self.positions:
            return None

        pos = self.positions.pop(ticker)
        strategy = pos['strategy']
        qty = pos['qty']
        entry_price = pos['entry_price']
        entry_fee = pos['entry_fee']
        ts_str = timestamp or self.get_current_timestamps()[0]
        cap_state = self.get_capital_state_summary()

        # 가상 매도 체결가 (Bid 호가 - 0.1% 슬리피지)
        base_price = bid if bid > 0 else signal_price
        fill_price = round_price(base_price * (1.0 - self.slippage_rate))

        gross_proceeds = qty * fill_price
        exit_fee = gross_proceeds * self.fee_rate
        net_proceeds = gross_proceeds - exit_fee
        pnl = net_proceeds - (qty * entry_price + entry_fee)
        ret_pct = ((fill_price - entry_price) / entry_price * 100.0) if entry_price > 0 else 0.0

        self.balance += net_proceeds
        self.daily_realized_pnl += pnl

        trade_record = {
            'strategy': strategy,
            'ticker': ticker,
            'qty': qty,
            'entry_price': entry_price,
            'exit_price': fill_price,
            'entry_time': pos['entry_time'],
            'exit_time': ts_str,
            'pnl': round(pnl, 2),
            'return_pct': round(ret_pct, 2),
            'reason': reason,
            'capital_frac': pos['capital_frac']
        }
        self.closed_trades.append(trade_record)

        self.logger.info(
            f"🔴 [Paper SELL Filled] {strategy} {ticker} ({reason}) | {qty}주 @ ${fill_price:.4f} "
            f"(신호가: ${signal_price:.4f}) | 손익: ${pnl:+,.2f} ({ret_pct:+.2f}%) | "
            f"총자산: ${self.total_equity:,.2f}"
        )

        self.log_drift_record(
            strategy=strategy,
            ticker=ticker,
            side="SELL",
            signal_price=signal_price,
            virtual_fill_price=fill_price,
            capital_state_before=cap_state,
            executed_qty=qty,
            fill_amount=gross_proceeds,
            skip_reason="NONE",
            realized_pnl=pnl,
            return_pct=ret_pct,
            trigger_reason=reason,
            timestamp_str=ts_str
        )

        return trade_record

    def update_quote(self, ticker: str, current_price: float):
        """실시간 시세 변동 반영 (고점 갱신 및 포지션 평가)"""
        if ticker in self.positions:
            self.positions[ticker]['current_price'] = current_price
            if current_price > self.positions[ticker].get('highest_price', 0.0):
                self.positions[ticker]['highest_price'] = current_price

    def force_eod_exit(self, eod_price_map: Optional[Dict[str, float]] = None):
        """장 마감 시 미체결 잔여 포지션 일괄 강제 청산"""
        open_tickers = list(self.positions.keys())
        for ticker in open_tickers:
            pos = self.positions[ticker]
            curr_p = pos.get('current_price', pos['entry_price'])
            if eod_price_map and ticker in eod_price_map:
                curr_p = eod_price_map[ticker]
            self.execute_virtual_sell(
                ticker=ticker,
                reason="FORCE_EOD_EXIT",
                signal_price=curr_p,
                bid=curr_p * 0.995
            )

    def daily_reset(self):
        """익일 장 시작 전 상태 초기화"""
        self.daily_realized_pnl = 0.0
        self.closed_trades.clear()
        self.logger.info("🔄 [VirtualCombinedPortfolio] 일일 상태 리셋 완료")
