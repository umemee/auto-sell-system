# drift_reporter.py
"""
GapZone EOD Execution Drift Reporter (실전-가상-백테스트 3자 괴리 추적기)
=====================================================================
장 마감 후 당일(또는 지정 일자)의 실시간 가상 매매(Paper Trading) 로그와
백테스트(Backtest) 예상 체결 결과를 1:1 정밀 대조하여 괴리 리포트를 생성합니다.

분석 항목:
1. 신호 정합도 (Signal Match Rate): 백테스트 신호 수 vs 실시간 가상 신호 수 일치율(%)
2. 타이밍 및 지연(Latency) 손실: 호가/타이밍 차이로 인한 신호 누락 및 스킵 건수
3. 호가 스프레드 및 슬리피지(Slippage): 신호가 대비 실제 가상 체결가 괴리율(%) 및 금액($)
4. FIFO 자본 경합 괴리: 자본 부족(INSUFFICIENT_CAPITAL) 스킵 일치 여부
5. 손익 괴리(PnL Drift): 백테스트 예상 순익 vs 가상 체결 실현 순익 차이
"""

import sys
import os
import argparse
import pickle
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np
from tabulate import tabulate

# Windows cp949 인코딩 안전 조치
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent if BASE_DIR.name == "auto-sell-system-main" else BASE_DIR

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def load_backtest_signals(target_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """백테스트 캐시에서 대상 일자의 신호 목록을 로드합니다."""
    cache_path = PROJECT_ROOT / "logs" / "cache" / "weekly_signals_cache.pkl"
    if not cache_path.exists():
        print(f"⚠️ [DriftReporter] 캐시 파일을 찾을 수 없습니다: {cache_path}")
        return []

    with open(cache_path, "rb") as f:
        data = pickle.load(f)

    s1 = data.get('s1_signals', [])
    s2 = data.get('s2_signals_raw', [])
    combined = s1 + s2

    if target_date:
        combined = [s for s in combined if s.get('date') == target_date]

    return combined


def load_drift_log(target_date: Optional[str] = None) -> pd.DataFrame:
    """가상 매매 괴리 로그(logs/paper/execution_drift_log.csv)를 로드합니다."""
    possible_paths = [
        BASE_DIR / "logs" / "paper" / "execution_drift_log.csv",
        PROJECT_ROOT / "logs" / "paper" / "execution_drift_log.csv",
        BASE_DIR / "execution_drift_log.csv"
    ]

    log_file = None
    for p in possible_paths:
        if p.exists() and p.stat().st_size > 0:
            log_file = p
            break

    if not log_file:
        return pd.DataFrame()

    df = pd.read_csv(log_file, encoding='utf-8-sig')
    if target_date and not df.empty and 'timestamp' in df.columns:
        df = df[df['timestamp'].str.startswith(target_date)]

    return df


def generate_drift_report(
    target_date: Optional[str] = None,
    output_md: Optional[str] = None
) -> Dict[str, Any]:
    print("=" * 80)
    print(" GapZone EOD Execution Drift Reporter (실전-가상-백테스트 3자 괴리 리포트)")
    print("=" * 80)
    if target_date:
        print(f"분석 대상 일자: {target_date}")
    else:
        print("분석 대상 일자: 전체 기록 누적")
    print("=" * 80)

    # 1. 데이터 로드
    df_paper = load_drift_log(target_date)
    bt_signals = load_backtest_signals(target_date)

    if df_paper.empty:
        print("⚠️ [DriftReporter] 가상 매매 기록(execution_drift_log.csv)이 비어있거나 대상 일자 기록이 없습니다.")
        return {'status': 'empty'}

    # 2. 신호 정합도 분석
    # 가상 매수 신호 (체결 + 스킵 모두 포함)
    paper_buys = df_paper[df_paper['side'] == 'BUY']
    paper_filled_buys = paper_buys[paper_buys['skip_reason'] == 'NONE']
    paper_skipped_buys = paper_buys[paper_buys['skip_reason'] != 'NONE']

    paper_buy_keys = set(zip(paper_buys['timestamp'].str[:10], paper_buys['ticker'], paper_buys['strategy']))
    bt_buy_keys = set(
        (s.get('date', pd.to_datetime(s['entry_time']).strftime('%Y-%m-%d')), s['ticker'], s.get('strategy', 'EMA').upper())
        for s in bt_signals
    )

    matched_keys = paper_buy_keys.intersection(bt_buy_keys)
    paper_only_keys = paper_buy_keys - bt_buy_keys
    bt_only_keys = bt_buy_keys - paper_buy_keys

    total_unique_signals = len(paper_buy_keys.union(bt_buy_keys))
    match_rate = (len(matched_keys) / total_unique_signals * 100.0) if total_unique_signals > 0 else 0.0

    # 3. 슬리피지 분석 (체결된 매수 기준)
    avg_buy_slippage_pct = paper_filled_buys['slippage_pct'].mean() if not paper_filled_buys.empty else 0.0
    max_buy_slippage_pct = paper_filled_buys['slippage_pct'].max() if not paper_filled_buys.empty else 0.0
    total_slippage_cost = 0.0
    if not paper_filled_buys.empty and 'virtual_fill_price' in paper_filled_buys.columns and 'signal_price' in paper_filled_buys.columns:
        total_slippage_cost = (
            (paper_filled_buys['virtual_fill_price'] - paper_filled_buys['signal_price']) * paper_filled_buys['executed_qty']
        ).sum()

    # 4. 실현 손익 분석 (매도 기준)
    paper_sells = df_paper[df_paper['side'] == 'SELL']
    total_paper_realized_pnl = paper_sells['realized_pnl'].sum() if not paper_sells.empty else 0.0
    total_paper_trades = len(paper_sells)
    paper_win_trades = len(paper_sells[paper_sells['realized_pnl'] > 0]) if not paper_sells.empty else 0
    paper_win_rate = (paper_win_trades / total_paper_trades * 100.0) if total_paper_trades > 0 else 0.0

    # 백테스트 예상 실현손익
    bt_pnl_sum = sum(float(s.get('return_pct', 0.0)) for s in bt_signals)  # 단순 지표 비교용

    # 5. 콘솔 리포트 테이블 출력
    summary_rows = [
        ["백테스트 감지 신호 수 (Backtest Expected)", f"{len(bt_buy_keys)} 건"],
        ["가상 매매 감지 신호 수 (Paper Observed)", f"{len(paper_buy_keys)} 건"],
        ["신호 일치율 (Signal Match Rate)", f"{match_rate:.1f}% ({len(matched_keys)}/{total_unique_signals})"],
        ["지연/괴리로 인한 백테스트 누락 신호", f"{len(bt_only_keys)} 건"],
        ["실시간 노이즈/추가 신호", f"{len(paper_only_keys)} 건"],
        ["가상 실제 체결 건수 (Filled Buys)", f"{len(paper_filled_buys)} 건"],
        ["FIFO 자본 부족 스킵 건수 (Skipped)", f"{len(paper_skipped_buys)} 건"],
        ["평균 매수 슬리피지 (Avg Buy Slippage)", f"{avg_buy_slippage_pct:+.3f}%"],
        ["최대 매수 슬리피지 (Max Buy Slippage)", f"{max_buy_slippage_pct:+.3f}%"],
        ["슬리피지로 인한 자본 손실액", f"${total_slippage_cost:,.2f}"],
        ["가상 매매 총 실현손익 (Paper PnL)", f"${total_paper_realized_pnl:+,.2f}"],
        ["가상 매매 승률 (Paper Win Rate)", f"{paper_win_rate:.1f}% ({paper_win_trades}/{total_paper_trades}건)"]
    ]
    print(tabulate(summary_rows, headers=["괴리 추적 지표 (Drift Metric)", "결과 수치"], tablefmt="github"))

    # 상세 체결 및 스킵 리스트 출력
    print("\n### [상세 거래 및 스킵 내역 대조]")
    detail_rows = []
    for _, row in paper_buys.iterrows():
        is_matched = "✅ 일치" if (row['timestamp'][:10], row['ticker'], row['strategy']) in matched_keys else "⚠️ 불일치"
        detail_rows.append([
            row['timestamp'],
            row['strategy'],
            row['ticker'],
            f"${row['signal_price']:.4f}",
            f"${row['virtual_fill_price']:.4f}" if row['virtual_fill_price'] > 0 else "0.0000",
            f"{row['slippage_pct']:+.2f}%",
            row['skip_reason'],
            is_matched
        ])
    print(tabulate(
        detail_rows,
        headers=["Timestamp", "Strategy", "Ticker", "Signal Price", "Fill Price", "Slippage", "Skip Reason", "BT Match"],
        tablefmt="github"
    ))

    # 마크다운 리포트 파일 저장
    if output_md:
        md_path = Path(output_md)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# EOD Execution Drift Report ({target_date or 'All'})\n\n")
            f.write("## 1. 종합 지표 요약\n\n")
            f.write(tabulate(summary_rows, headers=["괴리 추적 지표", "결과 수치"], tablefmt="github"))
            f.write("\n\n## 2. 상세 체결 내역\n\n")
            f.write(tabulate(
                detail_rows,
                headers=["Timestamp", "Strategy", "Ticker", "Signal Price", "Fill Price", "Slippage", "Skip Reason", "BT Match"],
                tablefmt="github"
            ))
            f.write("\n")
        print(f"\n[SAVE] 마크다운 리포트가 저장되었습니다: {md_path.resolve()}")

    return {
        'match_rate': match_rate,
        'total_paper_signals': len(paper_buy_keys),
        'total_bt_signals': len(bt_buy_keys),
        'avg_slippage_pct': avg_buy_slippage_pct,
        'total_slippage_cost': total_slippage_cost,
        'paper_pnl': total_paper_realized_pnl,
        'paper_win_rate': paper_win_rate
    }


def parse_args():
    parser = argparse.ArgumentParser(description="GapZone EOD Execution Drift Reporter")
    parser.add_argument("--date", type=str, default=None, help="분석 대상 일자 (YYYY-MM-DD, 미지정 시 전체)")
    parser.add_argument("--output-md", type=str, default=None, help="마크다운 리포트 저장 경로")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    generate_drift_report(target_date=args.date, output_md=args.output_md)
