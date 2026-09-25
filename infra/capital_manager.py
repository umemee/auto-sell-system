# capital_manager.py
"""
[GapZone Shared Capital Manager - Chronological FIFO Arbitrator]
전략1(EMA)과 전략2(Alpha)의 독립 신호를 단일 타임라인으로 통합하여,
공유 자본 풀(seed=1.0)을 기준으로 시간순(FIFO) 자본 배분 및 스킵을 결정하는 매니저 모듈.

규칙 명세:
- 전략1(EMA): 슬롯 0.5 고정 (최대 2슬롯). 가용자본 >= 0.5 진입, 미달 시 INSUFFICIENT_CAPITAL 스킵.
- 전략2(Alpha): 잔여 가용자본 전부 스윕 (available > 0). 가용자본 == 0 시 INSUFFICIENT_CAPITAL 스킵.
- 0주 방어: 배분 금액으로 주식 1주도 살 수 없는 경우 ZERO_QTY_ROUNDING 스킵.
- 동일 초 충돌: 전략1 우선 순회 (사용자 확정, 2026-09-22).
- 청산-진입 동시: 포지션 exit_time <= entry_time 시 자본 반환 선행.
"""

import logging
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional
import pandas as pd
import numpy as np

logger = logging.getLogger("CapitalManager")


class SharedCapitalManager:
    """
    공유 자본 배분 매니저 (Single Source of Truth for Capital Allocation)
    """
    def __init__(self, seed: float = 1.0, epsilon: float = 1e-9):
        self.seed = float(seed)
        self.used = 0.0
        self.epsilon = epsilon
        self.open_positions: List[Dict[str, Any]] = []
        self.admitted_trades: List[Dict[str, Any]] = []
        self.skipped_signals: List[Dict[str, Any]] = []

    def reset(self):
        """매니저 상태 초기화"""
        self.used = 0.0
        self.open_positions.clear()
        self.admitted_trades.clear()
        self.skipped_signals.clear()

    def get_available_capital(self) -> float:
        return max(0.0, self.seed - self.used)

    def allocate_and_admit(
        self,
        signals: List[Dict[str, Any]],
        portfolio: Optional[Any] = None,
        dry_run: bool = False
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        신호 목록을 시간순으로 정렬한 뒤, FIFO 순서로 자본 배분/스킵을 판정합니다.
        
        매개변수:
            signals: 통합할 원시 신호 목록
            portfolio: 실전/백테스트 Portfolio 인스턴스 (0주 방어 검사용, 선택적)
            dry_run: True일 경우 내부 상태를 변경하지 않고 복사본으로 시뮬레이션
        """
        # 동일 초 충돌 시 전략1 우선 처리 (사용자 확정, 2026-09-22)
        sorted_signals = sorted(
            signals,
            key=lambda s: (
                pd.to_datetime(s['entry_time']),
                0 if s.get('strategy') == 'EMA' else 1,
                s.get('ticker', '')
            )
        )

        used = self.used if not dry_run else 0.0
        open_positions = self.open_positions if not dry_run else []
        admitted = self.admitted_trades if not dry_run else []
        skipped = self.skipped_signals if not dry_run else []

        if dry_run:
            open_positions = []
            admitted = []
            skipped = []

        trade_counter = 1

        for s in sorted_signals:
            entry_dt = pd.to_datetime(s['entry_time'])
            exit_dt = pd.to_datetime(s['exit_time'])
            strat = s.get('strategy', 'EMA')
            ticker = s.get('ticker', 'UNKNOWN')
            d_str = s.get('date', entry_dt.strftime('%Y-%m-%d'))
            entry_p = float(s.get('entry_price', s.get('signal_price', 1.0)))

            # -------------------------------------------------------------
            # 1) 이 시점까지 청산된 포지션은 자본 즉시 반환 (exit_time <= entry_time)
            # -------------------------------------------------------------
            for pos in list(open_positions):
                if pos['exit_time'] <= entry_dt:
                    used = max(0.0, used - pos['size'])
                    open_positions.remove(pos)

            available = max(0.0, self.seed - used)

            # -------------------------------------------------------------
            # 2) 전략별 진입 규칙 및 자본 배분
            # -------------------------------------------------------------
            if strat == 'EMA':  # 전략1 (EMA)
                if available >= 0.5 - self.epsilon:
                    size = 0.5

                    # 0주 방어 검사 (portfolio가 제공된 경우)
                    if portfolio is not None and hasattr(portfolio, 'balance'):
                        tot_eq = portfolio.balance
                        for p in portfolio.positions.values():
                            tot_eq += p['qty'] * p['entry_price']
                        alloc_dollar = tot_eq * size
                        qty = int(alloc_dollar // entry_p) if entry_p > 0 else 0
                        if qty <= 0:
                            skipped.append({
                                'date': d_str,
                                'strategy': strat,
                                'ticker': ticker,
                                'entry_time': str(entry_dt),
                                'reason': 'ZERO_QTY_ROUNDING',
                                'available_capital_at_skip': round(available, 4)
                            })
                            continue

                    used += size
                    capital_used_after = used
                    trade_id = s.get(
                        'trade_id',
                        f"{d_str.replace('-', '')}_{ticker}_{strat}_{trade_counter:04d}"
                    )
                    trade_counter += 1

                    pos_record = {
                        'strategy': strat,
                        'ticker': ticker,
                        'size': size,
                        'entry_time': entry_dt,
                        'exit_time': exit_dt,
                        'trade_id': trade_id,
                        'capital_used_after': capital_used_after
                    }
                    open_positions.append(pos_record)

                    trade_item = dict(s)
                    trade_item['trade_id'] = trade_id
                    trade_item['strategy'] = strat
                    trade_item['capital_frac'] = round(size, 4)
                    trade_item['capital_used_after'] = round(capital_used_after, 4)
                    admitted.append(trade_item)
                else:
                    skipped.append({
                        'date': d_str,
                        'strategy': strat,
                        'ticker': ticker,
                        'entry_time': str(entry_dt),
                        'reason': 'INSUFFICIENT_CAPITAL',
                        'available_capital_at_skip': round(available, 4)
                    })

            else:  # 전략2 (ALPHA)
                if available > self.epsilon:
                    size = available

                    # 0주 방어 검사 (portfolio가 제공된 경우)
                    if portfolio is not None and hasattr(portfolio, 'balance'):
                        tot_eq = portfolio.balance
                        for p in portfolio.positions.values():
                            tot_eq += p['qty'] * p['entry_price']
                        alloc_dollar = tot_eq * size
                        qty = int(alloc_dollar // entry_p) if entry_p > 0 else 0
                        if qty <= 0:
                            skipped.append({
                                'date': d_str,
                                'strategy': strat,
                                'ticker': ticker,
                                'entry_time': str(entry_dt),
                                'reason': 'ZERO_QTY_ROUNDING',
                                'available_capital_at_skip': round(available, 4)
                            })
                            continue

                    used += size
                    capital_used_after = used
                    trade_id = s.get(
                        'trade_id',
                        f"{d_str.replace('-', '')}_{ticker}_{strat}_{trade_counter:04d}"
                    )
                    trade_counter += 1

                    pos_record = {
                        'strategy': strat,
                        'ticker': ticker,
                        'size': size,
                        'entry_time': entry_dt,
                        'exit_time': exit_dt,
                        'trade_id': trade_id,
                        'capital_used_after': capital_used_after
                    }
                    open_positions.append(pos_record)

                    trade_item = dict(s)
                    trade_item['trade_id'] = trade_id
                    trade_item['strategy'] = strat
                    trade_item['capital_frac'] = round(size, 4)
                    trade_item['capital_used_after'] = round(capital_used_after, 4)
                    admitted.append(trade_item)
                else:
                    skipped.append({
                        'date': d_str,
                        'strategy': strat,
                        'ticker': ticker,
                        'entry_time': str(entry_dt),
                        'reason': 'INSUFFICIENT_CAPITAL',
                        'available_capital_at_skip': round(available, 4)
                    })

        if not dry_run:
            self.used = used

        return admitted, skipped


# ==============================================================================
# 🧪 명세서 7장 엣지케이스 단위 테스트 스위트 (EC-1 ~ EC-6)
# ==============================================================================
def run_edge_case_tests() -> bool:
    """
    명세서 7장에 정의된 6대 엣지케이스에 대한 정밀 단위 테스트를 수행합니다.
    모든 테스트가 통과하면 True를 반환합니다.
    """
    print("=" * 80)
    print("[TEST] Running Edge Case Unit Tests (EC-1 to EC-6)...")
    print("=" * 80)

    # -------------------------------------------------------------
    # EC-1: 자본 정확히 0.5 남았을 때 전략1 진입 (경계값 & Epsilon 방어)
    # -------------------------------------------------------------
    cm1 = SharedCapitalManager(seed=1.0)
    # 이미 0.5 사용 중
    cm1.used = 0.5
    cm1.open_positions.append({
        'strategy': 'EMA', 'ticker': 'EXIST1', 'size': 0.5,
        'entry_time': pd.Timestamp('2026-05-01 09:00:00'),
        'exit_time': pd.Timestamp('2026-05-01 09:30:00')
    })
    # 정확히 0.5 남은 상황에서 새 전략1 신호
    sig1 = [{
        'strategy': 'EMA', 'ticker': 'TEST1',
        'entry_time': '2026-05-01 09:05:00',
        'exit_time': '2026-05-01 09:20:00',
        'entry_price': 2.0
    }]
    adm1, skp1 = cm1.allocate_and_admit(sig1)
    assert len(adm1) == 1, "EC-1 Failed: Available exact 0.5 should admit S1"
    assert len(skp1) == 0, "EC-1 Failed: S1 should not be skipped"
    assert adm1[0]['capital_frac'] == 0.5, "EC-1 Failed: Size must be 0.5"
    print("  [PASS] [EC-1] Boundary available=0.5 S1 admission test: PASSED")

    # -------------------------------------------------------------
    # EC-2: 극소액 자본(0.001) 남았을 때 전략2 진입 및 0주 방어 (ZERO_QTY_ROUNDING)
    # -------------------------------------------------------------
    cm2 = SharedCapitalManager(seed=1.0)
    cm2.used = 0.999  # 남은 자본 0.001 ($2000 기준 $2.00)
    cm2.open_positions.append({
        'strategy': 'EMA', 'ticker': 'EXIST2', 'size': 0.999,
        'entry_time': pd.Timestamp('2026-05-01 09:00:00'),
        'exit_time': pd.Timestamp('2026-05-01 09:30:00')
    })
    class MockPortfolio:
        balance = 2.0
        positions = {}
        def get_max_order_amount(self, capital_frac=None):
            return 2.0 * capital_frac
    sig2 = [{
        'strategy': 'ALPHA', 'ticker': 'HIGH_PRICE',
        'entry_time': '2026-05-01 09:05:00',
        'exit_time': '2026-05-01 09:20:00',
        'entry_price': 5.0  # $5.00 주가인데 가용금액은 $2.00 -> 0주
    }]
    adm2, skp2 = cm2.allocate_and_admit(sig2, portfolio=MockPortfolio())
    assert len(adm2) == 0, "EC-2 Failed: Zero qty should not be admitted"
    assert len(skp2) == 1, "EC-2 Failed: Zero qty must be skipped"
    assert skp2[0]['reason'] == 'ZERO_QTY_ROUNDING', "EC-2 Failed: Reason must be ZERO_QTY_ROUNDING"
    print("  [PASS] [EC-2] Micro capital sweep & 0-qty rounding defense: PASSED")

    # -------------------------------------------------------------
    # EC-3: 동일 초(entry_time) 충돌 시 전략1 우선 (Tie-break)
    # -------------------------------------------------------------
    cm3 = SharedCapitalManager(seed=1.0)
    # 동일 일시에 ALPHA가 리스트 앞에 있더라도 EMA가 먼저 자본을 선점해야 함
    sig3 = [
        {
            'strategy': 'ALPHA', 'ticker': 'ALPHA_RACE',
            'entry_time': '2026-06-01 09:00:00',
            'exit_time': '2026-06-01 09:35:00',
            'entry_price': 1.5
        },
        {
            'strategy': 'EMA', 'ticker': 'EMA_RACE',
            'entry_time': '2026-06-01 09:00:00',
            'exit_time': '2026-06-01 09:10:00',
            'entry_price': 2.0
        }
    ]
    adm3, skp3 = cm3.allocate_and_admit(sig3)
    assert len(adm3) == 2, "EC-3 Failed: Both should be admitted"
    assert adm3[0]['ticker'] == 'EMA_RACE', "EC-3 Failed: S1 (EMA) must be processed FIRST on tie-break"
    assert adm3[0]['capital_frac'] == 0.5, "EC-3 Failed: S1 must get 0.5"
    assert adm3[1]['ticker'] == 'ALPHA_RACE', "EC-3 Failed: S2 (ALPHA) must be processed SECOND"
    assert adm3[1]['capital_frac'] == 0.5, "EC-3 Failed: S2 must sweep remaining 0.5"
    print("  [PASS] [EC-3] Same-second tie-break S1 priority: PASSED")

    # -------------------------------------------------------------
    # EC-4: 청산 시각과 진입 시각 동시 도달 (exit_time == entry_time 자본 반환 선행)
    # -------------------------------------------------------------
    cm4 = SharedCapitalManager(seed=1.0)
    # 09:15:00까지 1.0 전액 사용 중
    cm4.used = 1.0
    cm4.open_positions.append({
        'strategy': 'ALPHA', 'ticker': 'CLOSING_POS', 'size': 1.0,
        'entry_time': pd.Timestamp('2026-05-01 08:45:00'),
        'exit_time': pd.Timestamp('2026-05-01 09:15:00')
    })
    # 정확히 09:15:00에 새 전략1 신호 도착
    sig4 = [{
        'strategy': 'EMA', 'ticker': 'NEW_ARRIVE',
        'entry_time': '2026-05-01 09:15:00',
        'exit_time': '2026-05-01 09:30:00',
        'entry_price': 1.8
    }]
    adm4, skp4 = cm4.allocate_and_admit(sig4)
    assert len(adm4) == 1, "EC-4 Failed: Capital must be returned at exit_time <= entry_time"
    assert len(skp4) == 0, "EC-4 Failed: New signal should not be skipped"
    assert adm4[0]['capital_frac'] == 0.5, "EC-4 Failed: Size must be 0.5"
    print("  [PASS] [EC-4] Simultaneous exit and entry capital return: PASSED")

    # -------------------------------------------------------------
    # EC-5: 전략1 2슬롯 소진 중 3번째 전략1 신호 도착 (스킵)
    # -------------------------------------------------------------
    cm5 = SharedCapitalManager(seed=1.0)
    cm5.used = 1.0
    cm5.open_positions.extend([
        {'strategy': 'EMA', 'ticker': 'S1_A', 'size': 0.5, 'entry_time': pd.Timestamp('2026-05-01 09:00:00'), 'exit_time': pd.Timestamp('2026-05-01 09:30:00')},
        {'strategy': 'EMA', 'ticker': 'S1_B', 'size': 0.5, 'entry_time': pd.Timestamp('2026-05-01 09:01:00'), 'exit_time': pd.Timestamp('2026-05-01 09:30:00')}
    ])
    sig5 = [{
        'strategy': 'EMA', 'ticker': 'S1_C',
        'entry_time': '2026-05-01 09:05:00',
        'exit_time': '2026-05-01 09:20:00',
        'entry_price': 3.0
    }]
    adm5, skp5 = cm5.allocate_and_admit(sig5)
    assert len(adm5) == 0, "EC-5 Failed: 3rd S1 signal must not be admitted when 2 slots full"
    assert len(skp5) == 1, "EC-5 Failed: 3rd S1 signal must be skipped"
    assert skp5[0]['reason'] == 'INSUFFICIENT_CAPITAL', "EC-5 Failed: Reason must be INSUFFICIENT_CAPITAL"
    print("  [PASS] [EC-5] S1 slot exhaustion 3rd signal skip: PASSED")

    # -------------------------------------------------------------
    # EC-6: 전략2 보유 중 2번째 전략2 신호 도착 (스킵)
    # -------------------------------------------------------------
    cm6 = SharedCapitalManager(seed=1.0)
    cm6.used = 1.0
    cm6.open_positions.append({
        'strategy': 'ALPHA', 'ticker': 'S2_ACTIVE', 'size': 1.0,
        'entry_time': pd.Timestamp('2026-05-01 08:40:00'),
        'exit_time': pd.Timestamp('2026-05-01 09:15:00')
    })
    sig6 = [{
        'strategy': 'ALPHA', 'ticker': 'S2_SECOND',
        'entry_time': '2026-05-01 08:50:00',
        'exit_time': pd.Timestamp('2026-05-01 09:25:00'),
        'entry_price': 2.5
    }]
    adm6, skp6 = cm6.allocate_and_admit(sig6)
    assert len(adm6) == 0, "EC-6 Failed: 2nd S2 signal must not be admitted when available=0"
    assert len(skp6) == 1, "EC-6 Failed: 2nd S2 signal must be skipped"
    assert skp6[0]['reason'] == 'INSUFFICIENT_CAPITAL', "EC-6 Failed: Reason must be INSUFFICIENT_CAPITAL"
    print("  [PASS] [EC-6] S2 double trigger second signal skip: PASSED")

    print("-" * 80)
    print("[ALL PASS] ALL 6 EDGE CASE UNIT TESTS PASSED SUCCESSFULLY!")
    print("=" * 80)
    return True


# ==============================================================================
# 황금수치 양방향 자동 검증 (53건 버전 & 66건 버전)
# ==============================================================================
def verify_golden_benchmarks(s1_trades_df: pd.DataFrame, s2_ledger_df: pd.DataFrame):
    """
    1) 53건 버전 검증: 결합 시 162건 체결, 10건 스킵(전부 전략1), 7건 전략2 부분진입
    2) 66건 버전 검증: 결합 시 162건 체결, 23건 스킵(전략1 10건 + 전략2 13건),
       전략2 스킵 13건의 티커 목록이 원본 standardized_trade_ledger.csv의 REJECTED 13건과 정확히 일치.
    """
    print("\n" + "=" * 85)
    print("[AUDIT] RUNNING GOLDEN BENCHMARK AUTOMATED ASSERTIONS")
    print("=" * 85)

    # 1. 원시 신호 정규화
    s1_signals = []
    for idx, r in s1_trades_df.iterrows():
        s1_signals.append({
            'strategy': 'EMA',
            'ticker': r['ticker'],
            'date': r['date'],
            'entry_time': str(r['entry_time']),
            'exit_time': str(r['exit_time']),
            'entry_price': float(r['entry_price']),
            'exit_price': float(r['exit_price']),
            'return_pct': float(r['return_pct']),
            'pnl': float(r['pnl']),
            'reason': r['reason'],
            'trade_id': r.get('trade_id', f"EMA_{idx:04d}")
        })

    s2_filled = s2_ledger_df[s2_ledger_df['execution_status'] == 'FILLED'].copy()
    s2_rejected = s2_ledger_df[s2_ledger_df['execution_status'] == 'REJECTED'].copy()

    s2_signals_53 = []
    for idx, r in s2_filled.iterrows():
        s2_signals_53.append({
            'strategy': 'ALPHA',
            'ticker': r['ticker'],
            'date': r['date'],
            'entry_time': str(r['entry_time']),
            'exit_time': str(r['exit_time']),
            'entry_price': float(r['entry_price']),
            'exit_price': float(r['exit_price']),
            'return_pct': float(r['return_pct']),
            'pnl': float(r['pnl_dollar']),
            'reason': r['exit_reason'],
            'trade_id': r['trade_id']
        })

    s2_signals_66 = []
    for idx, r in s2_ledger_df.iterrows():
        s2_signals_66.append({
            'strategy': 'ALPHA',
            'ticker': r['ticker'],
            'date': r['date'],
            'entry_time': str(r['entry_time']),
            'exit_time': str(r['exit_time']),
            'entry_price': float(r['entry_price']) if pd.notnull(r['entry_price']) else float(r['signal_price']),
            'exit_price': float(r['exit_price']) if pd.notnull(r['exit_price']) else float(r['signal_price']),
            'return_pct': float(r['return_pct']) if pd.notnull(r['return_pct']) else 0.0,
            'pnl': float(r['pnl_dollar']) if pd.notnull(r['pnl_dollar']) else 0.0,
            'reason': r['exit_reason'] if r['exit_reason'] != 'NONE' else 'REJECTED_ORIGINAL',
            'trade_id': r['trade_id'],
            'orig_status': r['execution_status']
        })

    # -------------------------------------------------------------
    # 검증 A: 53건 버전 시뮬레이션
    # -------------------------------------------------------------
    cm_53 = SharedCapitalManager(seed=1.0)
    all_53 = s1_signals + s2_signals_53
    admitted_53, skipped_53 = cm_53.allocate_and_admit(all_53)

    adm_s1_53 = [a for a in admitted_53 if a['strategy'] == 'EMA']
    adm_s2_53 = [a for a in admitted_53 if a['strategy'] == 'ALPHA']
    skp_s1_53 = [s for s in skipped_53 if s['strategy'] == 'EMA']
    skp_s2_53 = [s for s in skipped_53 if s['strategy'] == 'ALPHA']

    s2_partial = [a for a in adm_s2_53 if a['capital_frac'] < 1.0 - 1e-9]
    partial_tickers = [a['ticker'] for a in s2_partial]

    print("\n--- [VERIFICATION A: 53-Signal Version] ---")
    print(f"Total Inputs           : {len(all_53)} (EMA: {len(s1_signals)}, ALPHA: {len(s2_signals_53)})")
    print(f"Total Admitted         : {len(admitted_53)} (EMA: {len(adm_s1_53)}, ALPHA: {len(adm_s2_53)})")
    print(f"Total Skipped          : {len(skipped_53)} (EMA: {len(skp_s1_53)}, ALPHA: {len(skp_s2_53)})")
    print(f"ALPHA Partial Entries  : {len(s2_partial)} ({partial_tickers})")

    assert len(admitted_53) == 162, f"Assertion Failed: Expected 162 admitted, got {len(admitted_53)}"
    assert len(adm_s1_53) == 109, f"Assertion Failed: Expected 109 EMA admitted, got {len(adm_s1_53)}"
    assert len(adm_s2_53) == 53, f"Assertion Failed: Expected 53 ALPHA admitted, got {len(adm_s2_53)}"
    assert len(skipped_53) == 10, f"Assertion Failed: Expected 10 skipped, got {len(skipped_53)}"
    assert len(skp_s1_53) == 10, f"Assertion Failed: Expected 10 EMA skipped, got {len(skp_s1_53)}"
    assert len(skp_s2_53) == 0, f"Assertion Failed: Expected 0 ALPHA skipped in 53-version, got {len(skp_s2_53)}"
    expected_partial = ['YOOV', 'SUNE', 'LXEH', 'CIIT', 'EHGO', 'POLA', 'VIVK']
    assert partial_tickers == expected_partial, f"Assertion Failed: Partial tickers mismatch: {partial_tickers} vs {expected_partial}"
    print("  [PASS] Verification A PASSED: Exact 162 Admitted, 10 S1 Skipped, 7 Partial Entries perfectly match spec!")

    # -------------------------------------------------------------
    # 검증 B: 66건 버전 시뮬레이션 (원시 66건 전체 입력 시 일치성 단언)
    # -------------------------------------------------------------
    cm_66 = SharedCapitalManager(seed=1.0)
    all_66 = s1_signals + s2_signals_66
    admitted_66, skipped_66 = cm_66.allocate_and_admit(all_66)

    adm_s1_66 = [a for a in admitted_66 if a['strategy'] == 'EMA']
    adm_s2_66 = [a for a in admitted_66 if a['strategy'] == 'ALPHA']
    skp_s1_66 = [s for s in skipped_66 if s['strategy'] == 'EMA']
    skp_s2_66 = [s for s in skipped_66 if s['strategy'] == 'ALPHA']

    skp_s2_tickers = [s['ticker'] for s in skp_s2_66]
    orig_rejected_tickers = list(s2_rejected['ticker'])

    print("\n--- [VERIFICATION B: 66-Signal Raw Version] ---")
    print(f"Total Inputs           : {len(all_66)} (EMA: {len(s1_signals)}, ALPHA: {len(s2_signals_66)})")
    print(f"Total Admitted         : {len(admitted_66)} (EMA: {len(adm_s1_66)}, ALPHA: {len(adm_s2_66)})")
    print(f"Total Skipped          : {len(skipped_66)} (EMA: {len(skp_s1_66)}, ALPHA: {len(skp_s2_66)})")
    print(f"ALPHA Skipped Tickers  : {skp_s2_tickers}")
    print(f"Orig REJECTED Tickers  : {orig_rejected_tickers}")

    assert len(admitted_66) == 162, f"Assertion Failed: Expected 162 admitted in 66-version, got {len(admitted_66)}"
    assert len(adm_s1_66) == 109, f"Assertion Failed: Expected 109 EMA admitted, got {len(adm_s1_66)}"
    assert len(adm_s2_66) == 53, f"Assertion Failed: Expected 53 ALPHA admitted, got {len(adm_s2_66)}"
    assert len(skp_s1_66) == 10, f"Assertion Failed: Expected 10 EMA skipped, got {len(skp_s1_66)}"
    assert len(skp_s2_66) == 13, f"Assertion Failed: Expected 13 ALPHA skipped, got {len(skp_s2_66)}"
    assert skp_s2_tickers == orig_rejected_tickers, (
        f"Assertion Failed: ALPHA skipped tickers do not match original REJECTED tickers!\n"
        f"Skipped : {skp_s2_tickers}\nOriginal: {orig_rejected_tickers}"
    )
    print("  [PASS] Verification B PASSED: 162 Admitted, 10 S1 Skipped, 13 S2 Skipped 100% IDENTICAL to original REJECTED tickers!")
    print("=" * 85 + "\n")
    return True


if __name__ == "__main__":
    run_edge_case_tests()
