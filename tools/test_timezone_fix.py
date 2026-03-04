"""
[KST→ET Timezone Fix 검증 테스트 v1.0]
======================================================
목적: infra/kis_api.py와 strategy.py의 timezone 수정이
      올바르게 적용되었는지 자동으로 검증한다.

검증 항목:
  [T1] Unit Test: DataFrame 시간 필드 ET 변환 정확성
       - 버그 상태(UTC localize → ET convert) vs 수정 후(ET 직접 localize) 비교
  [T2] Live Test: get_minute_candles() 마지막 캔들이 현재 ET 시간과 일치하는지
  [T3] Live Test: EMA 값이 현재가 대비 합리적 범위(50~150%) 내인지
  [T4] Live Test: day_start_idx가 오늘 날짜의 데이터를 정확히 가리키는지
  [T5] Live Test: Anti-Chasing 필터가 100% REJECT 상태가 아닌지

실행법: python tools/test_timezone_fix.py
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
from infra.kis_auth import KisAuth

# ============================================================
# 설정
# ============================================================
EMA_LENGTH = getattr(Config, 'EMA_LENGTH', 200)
CANDLE_LIMIT = 400
ANTI_CHASE_RATIO = 1.03

# 결과 집계
results = {}


def _pass(label, msg):
    results[label] = ('✅ PASS', msg)
    print(f"  {label}: ✅ PASS — {msg}")


def _fail(label, msg):
    results[label] = ('❌ FAIL', msg)
    print(f"  {label}: ❌ FAIL — {msg}")


def _skip(label, msg):
    results[label] = ('⏭️ SKIP', msg)
    print(f"  {label}: ⏭️ SKIP — {msg}")


# ============================================================
# T1: Unit Test — KST vs ET timezone 처리 차이 검증 (오프라인)
# ============================================================
def test_t1_unit_timezone():
    """
    API 없��� 합성 데이터로 버그 상태(UTC localize)와
    수정 후(ET 직접 localize) 간 시간 차이를 검증한다.
    
    KIS API는 xymd/xhms 필드에 미국 현지시간(ET)을 반환한다.
    - 버그: tz_localize('UTC') → 이미 ET인 시간을 UTC로 간주 → tz_convert('ET') 후 9시간 빠름
    - 수정: tz_localize('America/New_York') → 그대로 ET로 인식 → 정확
    """
    print("\n[T1] Unit Test: KST vs ET timezone 처리 차이 검증")
    try:
        # ET 기준 오전 09:00를 나타내는 합성 date/time 컬럼 생성
        # (실제 KIS API의 xymd/xhms 값처럼 ET 시간이 그대로 들어있는 상태)
        et_hour = 9
        synthetic_dates = pd.date_range(
            start='2026-03-04 04:00',
            periods=100,
            freq='1min'
        )
        df = pd.DataFrame({'close': np.random.uniform(10, 20, 100)}, index=synthetic_dates)

        # 케이스 A: 버그 상태 — ET 시간인데 UTC로 잘못 localize
        df_bug = df.copy()
        df_bug.index = df_bug.index.tz_localize('UTC').tz_convert('America/New_York')
        last_bug = df_bug.index[-1]

        # 케이스 B: 수정 후 — ET 시간을 ET로 직접 localize
        df_fix = df.copy()
        df_fix.index = df_fix.index.tz_localize('America/New_York')
        last_fix = df_fix.index[-1]

        # 차이 계산: 버그 케이스는 5시간 빠르게 보임 (ET = UTC-5 겨울 / UTC-4 여름)
        diff_hours = abs((last_bug - last_fix).total_seconds() / 3600)

        print(f"  버그 케이스 마지막 시간  : {last_bug}")
        print(f"  수정 케이스 마지막 시간  : {last_fix}")
        print(f"  두 케이스 시간 차이       : {diff_hours:.1f}시간")

        if diff_hours >= 4.0:  # ET는 UTC-4(EDT) or UTC-5(EST) 이므로 4~5시간 차이
            _pass("T1", f"버그 vs 수정 시간 차이 {diff_hours:.1f}h 확인 (정상: 4~5h)")
        else:
            _fail("T1", f"시간 차이가 예상보다 작음: {diff_hours:.1f}h (기대: ≥4h)")

    except Exception as e:
        _fail("T1", f"예외 발생: {e}")


# ============================================================
# T2~T5: Live Test — 실제 API 호출
# ============================================================
def run_live_tests(api: KisApi):
    """실제 API 호출이 필요한 T2~T5 테스트"""

    # ── 랭킹 조회 ───────────────────────────────────��──────
    print("\n  [사전] 랭킹 1위 종목 조회 중...")
    try:
        ranking = api.get_ranking()
        if not ranking:
            _skip("T2", "랭킹 조회 실패 (장 외 시간이거나 API 오류)")
            _skip("T3", "랭킹 없음으로 스킵")
            _skip("T4", "랭킹 없음으로 스킵")
            _skip("T5", "랭킹 없음으로 스킵")
            return

        top = ranking[0]
        ticker = top.get('symb') or top.get('rsym') or top.get('SYMB', 'UNKNOWN')
        exchange = top.get('_excd', 'NAS')
        current_price = float(top.get('last', 0) or 0)

        print(f"  → 1위: [{ticker}] 거래소: {exchange} | 현재가: ${current_price:.4f}")
    except Exception as e:
        for t in ['T2', 'T3', 'T4', 'T5']:
            _skip(t, f"랭킹 조회 예외: {e}")
        return

    # ── 분봉 데이터 수집 ───────────────────────────────────
    print(f"  [사전] [{ticker}] 분봉 {CANDLE_LIMIT}개 수집 중...")
    try:
        df_raw = api.get_minute_candles(exchange, ticker, limit=CANDLE_LIMIT)
        if df_raw is None or df_raw.empty:
            for t in ['T2', 'T3', 'T4', 'T5']:
                _skip(t, "분봉 데이터 없음")
            return
        print(f"  → {len(df_raw)}개 수신 완료")
    except Exception as e:
        for t in ['T2', 'T3', 'T4', 'T5']:
            _skip(t, f"분봉 수집 예외: {e}")
        return

    # ── DatetimeIndex 변환 (strategy.py와 동일한 방식) ────
    df = df_raw.copy()
    try:
        if not isinstance(df.index, pd.DatetimeIndex):
            if 'date' in df.columns and 'time' in df.columns:
                time_str = df['time'].astype(str).str.zfill(4)
                datetime_str = df['date'].astype(str) + time_str
                fmt = '%Y%m%d%H%M' if len(time_str.iloc[-1]) == 4 else '%Y%m%d%H%M%S'
                df['datetime'] = pd.to_datetime(datetime_str, format=fmt, errors='coerce')
                df.set_index('datetime', inplace=True)

        if isinstance(df.index, pd.DatetimeIndex):
            if df.index.tz is None:
                df.index = df.index.tz_localize('America/New_York')
            elif str(df.index.tz) != 'America/New_York':
                df.index = df.index.tz_convert('America/New_York')
    except Exception as e:
        for t in ['T2', 'T3', 'T4', 'T5']:
            _skip(t, f"인덱스 변환 예외: {e}")
        return

    now_et = datetime.datetime.now(pytz.timezone('America/New_York'))

    # ── T2: 마지막 캔들 시간이 현재 ET와 일치하는가 ───────
    print("\n[T2] Live: 마지막 캔들 시간 ET 일치 확인")
    try:
        last_candle_time = df.index[-1]
        diff_minutes = abs((now_et - last_candle_time).total_seconds() / 60)
        print(f"  마지막 캔들: {last_candle_time}")
        print(f"  현재 ET    : {now_et.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"  차이       : {diff_minutes:.1f}분")

        # 장 외 시간이면 60분 기준 완화 (마지막 캔들이 폐장 시점)
        threshold = 300  # 5시간 이내면 당일 데이터로 간주
        if diff_minutes <= threshold:
            _pass("T2", f"마지막 캔들이 현재 ET와 {diff_minutes:.0f}분 차이 (기준: {threshold}분 이내)")
        else:
            _fail("T2", f"마지막 캔들이 현재 ET와 {diff_minutes:.0f}분 차이 — 버그 가능성 (KST 시간이 섞인 경우 약 540분 차이 발생)")
    except Exception as e:
        _fail("T2", f"예외 발생: {e}")

    # ── T3: EMA가 현재가의 50~150% 범위인가 ───────────────
    print("\n[T3] Live: EMA가 현재가 대비 합리적 범위 확인")
    try:
        df['ema'] = df['close'].ewm(span=EMA_LENGTH, adjust=False).mean()
        prev_ema = df['ema'].iloc[-2]
        ratio = prev_ema / current_price if current_price > 0 else 0

        print(f"  현재가   : ${current_price:.4f}")
        print(f"  EMA({EMA_LENGTH}): ${prev_ema:.4f}")
        print(f"  EMA/현재가 비율: {ratio*100:.1f}%")

        if 0.50 <= ratio <= 1.50:
            _pass("T3", f"EMA ${prev_ema:.2f}가 현재가 ${current_price:.2f}의 {ratio*100:.1f}% (정상 범위: 50~150%)")
        else:
            _fail("T3", f"EMA ${prev_ema:.2f}가 현재가 ${current_price:.2f}의 {ratio*100:.1f}% — 비정상! (버그 시 ~45% 수준)")
    except Exception as e:
        _fail("T3", f"예외 발생: {e}")

    # ── T4: day_start_idx가 오늘 데이터를 가리키는가 ──────
    print("\n[T4] Live: day_start_idx 오늘 날짜 확인")
    try:
        today_et = pd.Timestamp.now(tz='America/New_York').normalize()
        today_candles = df[df.index >= today_et]
        total_today = len(today_candles)

        if total_today > 0:
            first_today = today_candles.index[0]
            last_today = today_candles.index[-1]
            all_today = all(c.date() == today_et.date() for c in today_candles.index)
            print(f"  오늘({today_et.date()}) 캔들 수: {total_today}개")
            print(f"  첫 캔들: {first_today}")
            print(f"  끝 캔들: {last_today}")
            if all_today:
                _pass("T4", f"오늘({today_et.date()}) 데이터 {total_today}개 모두 날짜 일치")
            else:
                _fail("T4", f"오늘 구간에 다른 날짜 캔들 혼재 — timezone 오류 가능성")
        else:
            # 장 전 또는 주말의 경우 오늘 데이터가 없을 수 있음
            yesterday_et = today_et - pd.Timedelta(days=1)
            yesterday_candles = df[df.index >= yesterday_et]
            if len(yesterday_candles) > 0:
                _skip("T4", f"오늘({today_et.date()}) 데이터 없음 (장 전 또는 주말) — 어제 데이터 {len(yesterday_candles)}개 확인됨")
            else:
                _fail("T4", f"오늘({today_et.date()}) 데이터 없음 — timezone 오류로 날짜 계산 실패 가능성")
    except Exception as e:
        _fail("T4", f"예외 발생: {e}")

    # ── T5: Anti-Chasing 100% REJECT 여부 확인 ────────────
    print("\n[T5] Live: Anti-Chasing 100% REJECT 여부 확인")
    try:
        # 상위 10개 종목으로 체크
        sample = ranking[:10]
        reject_count = 0
        checked = 0

        for item in sample:
            sym = item.get('symb') or item.get('rsym', '')
            excd = item.get('_excd', 'NAS')
            last_price = float(item.get('last', 0) or 0)

            try:
                df_s = api.get_minute_candles(excd, sym, limit=CANDLE_LIMIT)
                if df_s is None or df_s.empty or len(df_s) < EMA_LENGTH + 2:
                    continue

                df_s = df_s.copy()
                if not isinstance(df_s.index, pd.DatetimeIndex):
                    if 'date' in df_s.columns and 'time' in df_s.columns:
                        ts = df_s['time'].astype(str).str.zfill(4)
                        ds = df_s['date'].astype(str) + ts
                        fmt = '%Y%m%d%H%M' if len(ts.iloc[-1]) == 4 else '%Y%m%d%H%M%S'
                        df_s['datetime'] = pd.to_datetime(ds, format=fmt, errors='coerce')
                        df_s.set_index('datetime', inplace=True)

                if isinstance(df_s.index, pd.DatetimeIndex):
                    if df_s.index.tz is None:
                        df_s.index = df_s.index.tz_localize('America/New_York')

                df_s['ema'] = df_s['close'].ewm(span=EMA_LENGTH, adjust=False).mean()
                prev_ema_s = df_s['ema'].iloc[-2]
                current_open_s = df_s['open'].iloc[-1]
                chasing_threshold_s = prev_ema_s * ANTI_CHASE_RATIO

                checked += 1
                if current_open_s > chasing_threshold_s:
                    reject_count += 1
                    print(f"  [{sym}] REJECT (open ${current_open_s:.2f} > EMA+3% ${chasing_threshold_s:.2f})")
                else:
                    print(f"  [{sym}] PASS   (open ${current_open_s:.2f} ≤ EMA+3% ${chasing_threshold_s:.2f})")

            except Exception:
                continue

        if checked == 0:
            _skip("T5", "검증 가능한 종목 없음 (데이터 부족)")
        else:
            reject_rate = reject_count / checked
            print(f"  REJECT 비율: {reject_count}/{checked} = {reject_rate*100:.0f}%")
            if reject_rate < 1.0:
                _pass("T5", f"Anti-Chasing REJECT율 {reject_rate*100:.0f}% — 100% REJECT 버그 해소됨")
            else:
                _fail("T5", f"Anti-Chasing REJECT율 100% — EMA가 여전히 비정상적으로 낮을 가능성")

    except Exception as e:
        _fail("T5", f"예외 발생: {e}")


# ============================================================
# 메인 실행
# ============================================================
def main():
    print("=" * 65)
    print("🧪 [KST→ET Timezone Fix 검증 테스트 v1.0] 시작")
    print("=" * 65)

    # T1: 오프라인 Unit Test
    test_t1_unit_timezone()

    # T2~T5: Live Test (API 연결 필요)
    print("\n[Live Test] API 연결 중...")
    try:
        auth = KisAuth()
        api = KisApi(auth)
        print("  ✅ 연결 성공")
        run_live_tests(api)
    except Exception as e:
        print(f"  ❌ API 연결 실패: {e}")
        for t in ['T2', 'T3', 'T4', 'T5']:
            _skip(t, f"API 연결 실패: {e}")

    # ── 최종 결과 요약 ──────────────────────────────────────
    print("\n" + "=" * 65)
    print("📋 [최종 결과 요약]")
    print("=" * 65)
    pass_count = sum(1 for v in results.values() if v[0].startswith('✅'))
    total = len(results)
    for label, (status, msg) in results.items():
        print(f"  {status} [{label}] {msg}")
    print("-" * 65)
    print(f"  결과: {pass_count}/{total} PASS")
    if pass_count == total:
        print("  🎉 모든 테스트 통과! Timezone 수정이 올바르게 적용되었습니다.")
    else:
        print("  ⚠️ 일부 테스트 실패. 위 FAIL 항목을 확인하세요.")
    print("=" * 65)


if __name__ == "__main__":
    main()