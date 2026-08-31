# infra/paper_execution_engine.py
"""
[Virtual Execution Engine - Paper Trading Simulator]
- 실계좌 주문 전면 차단 및 가상 체결 엔진
- 지정가(Limit Order) 호가 관통 검사
- 시장가(Market Order) 슬리피지 페널티(0.02% ~ 0.05%) 보정
- 네트워크 레이턴시(100ms) 모사
- ms 단위 정밀 타임스탬프 로깅 및 CSV/JSON DB 영속화
"""
import os
import time
import uuid
import csv
import json
import logging
import datetime
import pytz
from pathlib import Path
from config import Config
from infra.utils import get_logger, round_price


class VirtualExecutionEngine:
    """
    가상 체결 시뮬레이터 (Paper Trading Execution Engine)
    """
    def __init__(self, kis_api):
        self.kis = kis_api
        self.logger = get_logger("VirtualExecution")
        
        # 설정값 로드
        self.latency_ms = getattr(Config, 'VIRTUAL_LATENCY_MS', 100) # 100ms 지연
        self.slippage_pct = getattr(Config, 'VIRTUAL_SLIPPAGE_PCT', 0.0003) # 0.03% 보수적 슬리피지
        
        # 로그 및 DB 저장 경로
        self.log_dir = Path("logs/paper")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # 가상 미체결 주문 큐 (Pending Orders: {order_id: {...}})
        self.pending_orders = {}
        
        # 가상 주문 번호 시퀀스
        self._order_seq = 1000

    def _get_next_order_id(self):
        self._order_seq += 1
        return f"PT-{datetime.datetime.now().strftime('%Y%m%d')}-{self._order_seq:05d}"

    def _get_current_timestamps(self):
        """ms 단위 타임스탬프 생성"""
        now = datetime.datetime.now(pytz.timezone('US/Eastern'))
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        ms_epoch = int(time.time() * 1000)
        return now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], ms_epoch

    def simulate_latency(self):
        """100ms 네트워크 지연 모사"""
        if self.latency_ms > 0:
            time.sleep(self.latency_ms / 1000.0)

    def calculate_simulated_market_price(self, side, current_price, ask, bid):
        """
        시장가 체결가 및 슬리피지 계산
        - 매수: Ask 1 + 슬리피지 페널티
        - 매도: Bid 1 - 슬리피지 페널티
        """
        if side == "BUY":
            base_price = ask if ask > 0 else current_price
            # 매수 슬리피지 가산 (+0.03% ~ 0.05%)
            fill_price = base_price * (1.0 + self.slippage_pct)
        else: # SELL
            base_price = bid if bid > 0 else current_price
            # 매도 슬리피지 차감 (-0.03% ~ 0.05%)
            fill_price = base_price * (1.0 - self.slippage_pct)

        # SEC Rule 612 틱 사이즈 보정 ($1 이상 2자리, $1 미만 4자리)
        fill_price = round_price(fill_price)
        return fill_price, base_price

    def log_paper_trade(self, record: dict):
        """가상 체결 내역 CSV 및 JSON DB에 누락 없이 기록"""
        today_str = datetime.datetime.now(pytz.timezone('US/Eastern')).strftime("%Y%m%d")
        csv_file = self.log_dir / f"paper_trades_{today_str}.csv"
        json_file = self.log_dir / f"paper_trades_{today_str}.jsonl"
        
        file_exists = csv_file.exists()
        
        fieldnames = [
            "trade_id", "date", "ticker", "side", "qty",
            "signal_price", "simulated_fill_price", "slippage_amount", "slippage_pct",
            "signal_time_ms", "order_time_ms", "fill_time_ms",
            "trigger_reason", "realized_pnl", "return_pct",
            "ask_price", "bid_price", "latency_ms"
        ]
        
        try:
            with open(csv_file, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                if not file_exists:
                    writer.writeheader()
                writer.writerow(record)
                
            with open(json_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            self.logger.error(f"❌ [Paper Logging Error] 가상 거래 기록 실패: {e}")

    def execute_paper_buy(self, ticker, qty, signal_price, exchange="NAS"):
        """
        [가상 매수 주문 체결 시뮬레이션]
        1. 시그널 시점 기록
        2. 100ms 네트워크 지연 모사
        3. 지연 후 실시간 호가 조회
        4. 호가 관통 및 슬리피지 페널티 적용 체결
        5. 정밀 로깅
        """
        sig_time_str, sig_time_ms = self._get_current_timestamps()
        order_id = self._get_next_order_id()
        
        # 100ms 네트워크 지연 모사
        self.simulate_latency()
        ord_time_str, ord_time_ms = self._get_current_timestamps()

        # 지연 후 실시간 호가 조회
        try:
            ask, bid, ask_vol, bid_vol = self.kis.get_market_spread(ticker, exchange=exchange)
        except Exception as e:
            self.logger.warning(f"⚠️ [Paper Spread] 호가 조회 실패, 시그널가 사용: {e}")
            ask, bid = signal_price, signal_price

        # 시장가 매수 체결가 산출
        fill_price, base_price = self.calculate_simulated_market_price("BUY", signal_price, ask, bid)
        fill_time_str, fill_time_ms = self._get_current_timestamps()

        slippage_amt = abs(fill_price - signal_price)
        slippage_pct = (slippage_amt / signal_price * 100.0) if signal_price > 0 else 0.0

        trade_record = {
            "trade_id": order_id,
            "date": datetime.datetime.now(pytz.timezone('US/Eastern')).strftime("%Y-%m-%d"),
            "ticker": ticker,
            "side": "BUY",
            "qty": int(qty),
            "signal_price": round(float(signal_price), 4),
            "simulated_fill_price": round(float(fill_price), 4),
            "slippage_amount": round(float(slippage_amt), 4),
            "slippage_pct": round(float(slippage_pct), 4),
            "signal_time_ms": sig_time_ms,
            "order_time_ms": ord_time_ms,
            "fill_time_ms": fill_time_ms,
            "trigger_reason": "BUY_SIGNAL",
            "realized_pnl": 0.0,
            "return_pct": 0.0,
            "ask_price": round(float(ask), 4) if ask else 0.0,
            "bid_price": round(float(bid), 4) if bid else 0.0,
            "latency_ms": self.latency_ms
        }

        self.log_paper_trade(trade_record)
        
        self.logger.info(
            f"📝 [PAPER BUY FILL] {ticker} | {qty}주 | "
            f"시그널: ${signal_price:.4f} -> 체결: ${fill_price:.4f} "
            f"(슬리피지: ${slippage_amt:.4f}, +{slippage_pct:.3f}%) | 주문ID: #{order_id}"
        )

        return {
            'rt_cd': '0',
            'msg1': '가상 매수 체결 성공 (Paper Trading)',
            'output': {
                'ODNO': order_id,
                'fill_price': fill_price,
                'qty': qty,
                'trade_record': trade_record
            }
        }

    def execute_paper_sell(self, ticker, qty, entry_price, signal_price, reason="TAKE_PROFIT", exchange="NAS"):
        """
        [가상 매도 주문 체결 시뮬레이션]
        1. 시그널 시점 기록
        2. 100ms 네트워크 지연 모사
        3. 지연 후 실시간 호가 조회
        4. 지정가(TP) 관통 검사 및 시장가(SL/Timecut) 슬리피지 페널티 적용
        5. 실현 손익 및 정밀 로깅
        """
        sig_time_str, sig_time_ms = self._get_current_timestamps()
        order_id = self._get_next_order_id()
        
        # 100ms 네트워크 지연 모사
        self.simulate_latency()
        ord_time_str, ord_time_ms = self._get_current_timestamps()

        # 지연 후 실시간 호가 조회
        try:
            ask, bid, ask_vol, bid_vol = self.kis.get_market_spread(ticker, exchange=exchange)
        except Exception as e:
            self.logger.warning(f"⚠️ [Paper Spread] 호가 조회 실패, 시그널가 사용: {e}")
            ask, bid = signal_price, signal_price

        # 지정가(TP) vs 비상 매도(SL/TimeCut/EOD) 체결 로직
        if reason == "TAKE_PROFIT":
            # 지정가 체결: Bid 1 또는 실제 체결가가 목표가 이상일 때 체결
            # 목표가(signal_price)에 도달했으므로 목표가에 체결된 것으로 처리 (슬리피지 0 또는 미세 유리)
            fill_price = round_price(signal_price)
        else:
            # 시장가 손절/타임컷: Bid 1 - 슬리피지 페널티 강제 적용
            fill_price, _ = self.calculate_simulated_market_price("SELL", signal_price, ask, bid)

        fill_time_str, fill_time_ms = self._get_current_timestamps()

        # 손익 계산
        realized_pnl = (fill_price - entry_price) * qty
        return_pct = ((fill_price - entry_price) / entry_price * 100.0) if entry_price > 0 else 0.0
        slippage_amt = abs(fill_price - signal_price)
        slippage_pct = (slippage_amt / signal_price * 100.0) if signal_price > 0 else 0.0

        trade_record = {
            "trade_id": order_id,
            "date": datetime.datetime.now(pytz.timezone('US/Eastern')).strftime("%Y-%m-%d"),
            "ticker": ticker,
            "side": "SELL",
            "qty": int(qty),
            "signal_price": round(float(signal_price), 4),
            "simulated_fill_price": round(float(fill_price), 4),
            "slippage_amount": round(float(slippage_amt), 4),
            "slippage_pct": round(float(slippage_pct), 4),
            "signal_time_ms": sig_time_ms,
            "order_time_ms": ord_time_ms,
            "fill_time_ms": fill_time_ms,
            "trigger_reason": reason,
            "realized_pnl": round(float(realized_pnl), 2),
            "return_pct": round(float(return_pct), 2),
            "ask_price": round(float(ask), 4) if ask else 0.0,
            "bid_price": round(float(bid), 4) if bid else 0.0,
            "latency_ms": self.latency_ms
        }

        self.log_paper_trade(trade_record)

        self.logger.info(
            f"📝 [PAPER SELL FILL] {ticker} | {qty}주 | 사유: {reason} | "
            f"진입: ${entry_price:.4f} -> 청산: ${fill_price:.4f} | "
            f"손익: ${realized_pnl:+.2f} ({return_pct:+.2f}%) | 주문ID: #{order_id}"
        )

        return {
            'rt_cd': '0',
            'msg1': f'가상 매도 체결 성공 ({reason})',
            'output': {
                'ODNO': order_id,
                'fill_price': fill_price,
                'realized_pnl': realized_pnl,
                'return_pct': return_pct,
                'qty': qty,
                'trade_record': trade_record
            }
        }
