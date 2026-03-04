"""
[EMA 계산 오류 검증 도구 v1.0]
======================================================
목적: strategy.py의 EMA가 실제 값보다 낮게 계산되는 원인 규명
방법: 랭킹 1위 종목의 분봉 데이터를 가져와서
      strategy.py와 동일한 처리 방식으로 EMA를 계산하고
      데이터 구조의 이상을 진단한다.

실행법: python tools/ema_debug_test.py
======================================================
"""
import sys
import os
import pandas as pd
import numpy as np
import datetime
import pytz

# 프로젝트 루트 경로 추가
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
sys.path.append(root_dir)

from config import Config
from infra.kis_api import KisApi
from infra.token_manager import TokenManager

# ============================================================
# 설정
# ============================================================
EMA_LENGTH = getattr(Config, 'EMA_LENGTH', 200)   # strategy.py와 동일
CANDLE_LIMIT = 400                                  # get_minute_candles 기본값

def run_ema_debug():
    print("=" * 65)
    print("🔬 [EMA 계산 오류 검증 도구] 시작")
    print("=" * 65)

    # ----------------------------------------------------------
    # Step 1. API 연결
    # ----------------------------------------------------------
    print("\n[Step 1] API 연결 중...")
    try:
        tm = TokenManager()
        api = KisApi(tm)
        print("  ✅ 연결 성공")
    except Exception as e:
        print(f"  ❌ 연결 실패: {e}")
        return

    # ----------------------------------------------------------
    # Step 2. 랭킹 1위 종목 선택
    # ----------------------------------------------------------
    print("\n[Step 2] 랭킹 1위 종목 조회 중...")
    try:
        ranking = api.get_ranking()
        if not ranking:
            print("  ❌ 랭킹 조회 실패")
            return

        top = ranking[0]
        # 필드명은 KIS API 응답에 따라 다를 수 있음 (rsym 또는 symb)
        ticker = top.get('rsym') or top.get('symb') or top.get('SYMB') or top.get('stck_shrn_iscd')
        exchange = top.get('_excd', 'NAS')   # PR #1에서 추가한 태그
        price_str = top.get('last') or top.get('stck_prpr') or '0'
        change_str = top.get('diff') or top.get('prdy_ctrt') or '0'

        print(f"  ✅ 랭킹 1위: [{ticker}] 거래소: {exchange} | 현재가: ${price_str} | 등락: {change_str}%")
    except Exception as e:
        print(f"  ❌ 랭킹 처리 중 오류: {e}")
        return

    # ----------------------------------------------------------
    # Step 3. 분봉 데이터 수집 (두 가지 방법으로 비교)
    # ----------------------------------------------------------
    print(f"\n[Step 3] [{ticker}] 분봉 데이터 수집 중...")

    # 방법 A: get_minute_candles (pagination, 최대 400개)
    print(f"  [A] get_minute_candles({exchange}, {ticker}, limit={{CANDLE_LIMIT}}) 호출...")
    df_a = api.get_minute_candles(exchange, ticker, limit=CANDLE_LIMIT)

    # 방법 B: get_recent_candles (단순 1회, 최대 120개)
    print(f"  [B] get_recent_candles({ticker}, limit=120) 호출...")
    df_b = api.get_recent_candles(ticker, limit=120)

    print(f"\n  📊 데이터 개수: A={{len(df_a)}}개, B={{len(df_b)}}개")

    # ----------------------------------------------------------
    # Step 4. 데이터 구조 상세 진단
    # ----------------------------------------------------------
    for label, df in [("A (get_minute_candles)", df_a), ("B (get_recent_candles)", df_b)]:
        print(f"\n{{'=' * 65}}")
        print(f"🔍 [데이터셋 {{label}}] 진단")
        print(f"{{'=' * 65}}")

        if df.empty:
            print("  ❌ 데이터 없음 (빈 DataFrame)")
            continue

        print(f"  총 행 수        : {{len(df)}}")
        print(f"  컬럼 목록       : {{list(df.columns)}}")
        print(f"  인덱스 타입     : {{type(df.index)}}")

        # 인덱스가 DatetimeIndex인지 확인
        is_dt_index = isinstance(df.index, pd.DatetimeIndex)
        print(f"  DatetimeIndex  : {{'✅ 예' if is_dt_index else '❌ 아니오 (숫자/문자 인덱스)'}}")

        if is_dt_index:
            print(f"  인덱스 timezone: {{df.index.tz}}")
            print(f"  첫번째 시간     : {{df.index[0]}}")
            print(f"  마지막 시간     : {{df.index[-1]}}")

            # 정렬 방향 확인 (가장 중요한 체크)
            is_ascending = df.index.is_monotonic_increasing
            is_descending = df.index.is_monotonic_decreasing
            if is_ascending:
                direction = "✅ 오름차순 (과거→최신) ← 올바름"
            elif is_descending:
                direction = "❌ 내림차순 (최신→과거) ← 잘못됨! EMA 역산"
            else:
                direction = "⚠️ 비단조 (시간순 섞임) ← 잘못됨!"
            print(f"  시간 정렬 방향  : {{direction}}")
        else:
            print(f"  첫번째 행 인덱스: {{df.index[0]}}")
            print(f"  마지막 행 인덱스: {{df.index[-1]}}")

        # close 컬럼 확인
        if 'close' in df.columns:
            print(f"\n  --- close 가격 분포 ---")
            print(f"  iloc[0]  close : {{df['close'].iloc[0]:.4f}}  ← 첫번째 캔들")
            print(f"  iloc[-1] close : {{df['close'].iloc[-1]:.4f}}  ← 마지막 캔들")
            print(f"  평균             : {{df['close'].mean():.4f}}")
            print(f"  최소             : {{df['close'].min():.4f}}")
            print(f"  최대             : {{df['close'].max():.4f}}")

        # ----------------------------------------------------------
        # Step 5. strategy.py 완전 동일 처리 시뮬레이션
        # ----------------------------------------------------------
        print(f"\n  --- [strategy.py 처리 시뮬레이션] ---")
        df_sim = df.copy()

        # (1) 인덱스 보정 (strategy.py와 동일)
        if not isinstance(df_sim.index, pd.DatetimeIndex):
            if 'date' in df_sim.columns and 'time' in df_sim.columns:
                time_str = df_sim['time'].astype(str).str.zfill(4)
                datetime_str = df_sim['date'].astype(str) + time_str
                fmt = '%Y%m%d%H%M' if len(time_str.iloc[-1]) == 4 else '%Y%m%d%H%M%S'
                df_sim['datetime'] = pd.to_datetime(datetime_str, format=fmt, errors='coerce')
                df_sim.set_index('datetime', inplace=True)
                print(f"  인덱스 변환 완료 (date+time 조합)")

        if isinstance(df_sim.index, pd.DatetimeIndex):
            if df_sim.index.tz is None:
                df_sim.index = df_sim.index.tz_localize('UTC').tz_convert('America/New_York')
                print(f"  Timezone: UTC → America/New_York 변환 완료")
            elif str(df_sim.index.tz) != 'America/New_York':
                df_sim.index = df_sim.index.tz_convert('America/New_York')
                print(f"  Timezone: {{df.index.tz}} → America/New_York 변환 완료")

            print(f"  변환 후 첫번째 시간 : {{df_sim.index[0]}}")
            print(f"  변환 후 마지막 시간 : {{df_sim.index[-1]}}")

            # 변환 후 정렬 방향 재확인
            is_ascending_after = df_sim.index.is_monotonic_increasing
            print(f"  변환 후 정렬 방향   : {'✅ 오름차순' if is_ascending_after else '❌ 내림차순/혼조'}")

        # (2) EMA 계산 (strategy.py와 동일)
        if 'close' in df_sim.columns:
            df_sim['ema'] = df_sim['close'].ewm(span=EMA_LENGTH, adjust=False).mean()

            prev_ema   = df_sim['ema'].iloc[-2]    # strategy.py가 사용하는 값
            last_ema   = df_sim['ema'].iloc[-1]    # 진짜 마지막 값
            first_ema  = df_sim['ema'].iloc[0]     # 첫번째 EMA

            print(f"\n  --- EMA({EMA_LENGTH}) 계산 결과 ---")
            print(f"  ema.iloc[0]  (첫번째): {{first_ema:.4f}}")
            print(f"  ema.iloc[-2] (prev)  : {{prev_ema:.4f}}  ← strategy.py가 실제 사용하는 값")
            print(f"  ema.iloc[-1] (마지막): {{last_ema:.4f}}")

            current_open = df_sim['open'].iloc[-1] if 'open' in df_sim.columns else df_sim['close'].iloc[-1]
            chasing_threshold = prev_ema * 1.03

            print(f"\n  --- Anti-Chasing 필터 시뮬레이션 ---")
            print(f"  현재 open            : {{current_open:.4f}}")
            print(f"  prev_ema             : {{prev_ema:.4f}}")
            print(f"  chasing_threshold    : {{chasing_threshold:.4f}}  (prev_ema × 1.03)")
            if current_open > chasing_threshold:
                print(f"  판정: ❌ REJECT → open({{current_open:.4f}}) > EMA+3%({{chasing_threshold:.4f}})")
            else:
                print(f"  판정: ✅ PASS  → open({{current_open:.4f}}) ≤ EMA+3%({{chasing_threshold:.4f}})")

    # ----------------------------------------------------------
    # Step 6. 핵심 결론 출력
    # ----------------------------------------------------------
    print(f"\n{{'=' * 65}}")
    print("📋 [핵심 결론]")
    print(f"{{'=' * 65}}")

    results = {}
    for label, df in [("A", df_a), ("B", df_b)]:
        if df.empty or 'close' not in df.columns:
            continue
        df_c = df.copy()
        if isinstance(df_c.index, pd.DatetimeIndex):
            if df_c.index.tz is None:
                df_c.index = df_c.index.tz_localize('UTC').tz_convert('America/New_York')
        df_c['ema'] = df_c['close'].ewm(span=EMA_LENGTH, adjust=False).mean()
        results[label] = {
            'count': len(df_c),
            'prev_ema': df_c['ema'].iloc[-2],
            'is_ascending': df_c.index.is_monotonic_increasing if isinstance(df_c.index, pd.DatetimeIndex) else None
        }

    if 'A' in results and 'B' in results:
        ema_a = results['A']['prev_ema']
        ema_b = results['B']['prev_ema']
        diff  = abs(ema_a - ema_b)
        print(f"  데이터셋 A EMA({EMA_LENGTH}) : {{ema_a:.4f}}  ({{results['A']['count']}}개 기준)")
        print(f"  데이터셋 B EMA({EMA_LENGTH}) : {{ema_b:.4f}}  ({{results['B']['count']}}개 기준)")
        print(f"  EMA 차이              : {{diff:.4f}}  ({{diff/ema_a*100:.2f}}%)")

        if diff > ema_a * 0.01:
            print(f"\n  ⚠️  EMA 값에 1% 이상 차이 발생!")
            print(f"  → 데이터 개수 부족 또는 정렬 방향 오류가 원인일 가능성 높음")
        else:
            print(f"\n  ✅ EMA 차이 1% 미만 → 데이터 개수 문제 아님, 다른 원인 확인 필요")

        for lbl, res in results.items():
            asc = res.get('is_ascending')
            if asc is False:
                print(f"\n  🚨 [가설 확인됨] 데이터셋 {{lbl}}의 시간 정렬이 내림차순!")
                print(f"     → iloc[-1]이 과거 데이터를 가리키므로 EMA가 과거값으로 계산됨")
            elif asc is True:
                print(f"\n  ✅ 데이터셋 {{lbl}} 정렬 정상 (오름차순)")

    print(f"\n{{'=' * 65}}")
    print("🔬 [진단 완료]")
    print(f"{{'=' * 65}}\n")


if __name__ == "__main__":
    run_ema_debug()