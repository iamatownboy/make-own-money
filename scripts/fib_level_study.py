"""
scripts/fib_level_study.py
피보나치 되돌림 레벨이 실제로 '반등이 잘 일어나는 자리'인가를 검증한다.

질문
----
주가가 상승 스윙(저점 L -> 고점 H) 이후 되돌림하다가 비율 r 지점에 처음 닿았을 때,
거기서 위로 튀는가(반등), 아래로 더 빠지는가?
이를 피보나치 비율과 '아무 의미 없는 비율' 모두에서 똑같이 측정해 비교한다.
피보나치 비율이 특별하다면 그 비율에서만 반등 확률이 뚜렷하게 높아야 한다.

정의
----
- 스윙: 종가 기준 저점 L에서 +20% 이상 상승해 형성된 고점 H.
  주가가 H를 경신하면 H를 갱신한다(새로운 되돌림 시작).
  H 이후 조정 저점 m에서 다시 +20% 이상 오르면 (m, 새 H)로 새 스윙이 시작된다.
  되돌림 비율이 1.7을 넘으면 스윙이 무너진 것으로 보고 새 저점을 찾는다.
  모든 판단은 그 시점까지의 종가만 사용한다(미래 정보 없음).
- 되돌림 비율 r = (H - 현재가) / (H - L).  r > 1 은 출발 저점 L 아래까지 내려간 상태.
- 터치 이벤트: 한 스윙에서 비율 k에 '처음' 도달한 날. 종가가 k를 크게 건너뛰면
  (|r - k| > 허용오차) 그 비율의 터치로 보지 않는다. 모든 비율에 같은 기준을 적용한다.
- 결과(반등 여부): 터치 가격에서 스윙 폭의 15%만큼 위/아래에 대칭 배리어를 두고,
  60거래일 안에 위를 먼저 닿으면 반등(1), 아래를 먼저 닿으면 이탈(0).
  위아래가 대칭이므로 정보가 없는 자리의 반등 확률은 약 50%다.
  (상승 추세 시장의 소폭 우위는 모든 비율에 똑같이 걸리므로 비교에 영향이 없다.)
- 보조 결과: 터치 후 20거래일 수익률과 같은 기간 SPY 대비 초과수익.

비교
----
- 피보나치 비율: 사용자가 쓰는 0.382, 0.5, 0.618, 0.786, 0.886, 1.236, 1.31, 1.382, 1.5
- 대조 비율: 0.30~1.60 사이 0.02 간격 중, 모든 피보나치 비율과 0.06 이상 떨어진 값
- 차이의 신뢰구간은 종목 단위 부트스트랩으로 산출(같은 종목·스윙의 이벤트끼리 상관 대비).

한계
----
- 종가만 사용(장중 고가/저가 미사용). 모든 비율에 같은 한계가 적용되므로 상대 비교에는 영향이 작다.
- 스윙 정의(+20%)는 하나의 기계적 정의다. 눈으로 고르는 스윙과 다를 수 있다.

사용
----
    python scripts/fib_level_study.py
    python scripts/fib_level_study.py --swing 0.3 --barrier 0.2
"""

import os
import sys
import bisect
import pickle
import argparse
import warnings

warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRICE_CACHE = os.path.join(ROOT, 'data', 'cache', 'sp500_adj_close.pkl')

FIB_RATIOS = [0.382, 0.5, 0.618, 0.786, 0.886, 1.236, 1.31, 1.382, 1.5]


def build_ratio_grid(min_gap: float = 0.06):
    grid = [float(g) for g in np.round(np.arange(0.30, 1.6001, 0.02), 3)]
    controls = [g for g in grid if min(abs(g - f) for f in FIB_RATIOS) >= min_gap]
    ratios = sorted(set(FIB_RATIOS) | set(grid))
    return ratios, set(FIB_RATIOS), set(controls)


def detect_events(close: np.ndarray, ratios, swing_min=0.20, tol=0.03, break_ratio=1.7):
    """
    종가 배열에서 (터치 인덱스, 비율, 스윙 저점, 스윙 고점) 이벤트를 인과적으로 추출한다.
    """
    n = len(close)
    events = []
    L, H = close[0], None
    iL = 0
    max_r = -1.0
    pull_low = None

    for t in range(1, n):
        p = close[t]
        if H is None:
            # 상승 스윙 형성을 기다리는 중: 저점 갱신 또는 +swing_min 상승 확인
            if p < L:
                L, iL = p, t
            elif p >= L * (1 + swing_min):
                H = p
                max_r = -1.0
                pull_low = p
            continue

        if p > H:
            # 고점 경신: 같은 저점 L에서 되돌림을 새로 측정
            H = p
            max_r = -1.0
            pull_low = p
            continue

        pull_low = min(pull_low, p)
        # 조정 저점에서 다시 +swing_min 상승 -> 새 스윙
        if pull_low < H * (1 - swing_min) and p >= pull_low * (1 + swing_min):
            L, H = pull_low, p
            max_r = -1.0
            pull_low = p
            continue

        rng = H - L
        if rng <= 0:
            continue
        r = (H - p) / rng

        if r > max_r:
            lo = bisect.bisect_right(ratios, max_r)
            hi = bisect.bisect_right(ratios, r)
            for k in ratios[lo:hi]:
                if r - k <= tol:
                    events.append((t, k, L, H))
            max_r = r

        if r > break_ratio:
            # 스윙 붕괴: 현재가부터 새 저점 탐색
            L, iL, H = p, t, None
    return events


def _first_touch(close, t, b_abs, horizon):
    n = len(close)
    p = close[t]
    path = close[t + 1: min(n, t + 1 + horizon)]
    if len(path) == 0:
        return None
    up = np.flatnonzero(path >= p + b_abs)
    dn = np.flatnonzero(path <= p - b_abs)
    iu = up[0] if len(up) else np.inf
    idn = dn[0] if len(dn) else np.inf
    if iu == np.inf and idn == np.inf:
        return np.nan
    return 1.0 if iu < idn else 0.0


def evaluate(close: np.ndarray, spy: np.ndarray, events, barrier_frac=0.15, horizon=60, fwd_days=20,
             rng=None):
    """
    각 이벤트의 반등 여부와 함께, 같은 종목의 무작위 날짜에 '같은 % 폭' 배리어를 적용한
    기준선 반등 여부(random_bounce)를 기록한다. 되돌림 자리 자체가 유리한지 확인하는 대조군이다.
    """
    rng = rng or np.random.default_rng(3)
    rows = []
    n = len(close)
    for t, k, L, H in events:
        p = close[t]
        b = barrier_frac * (H - L)
        bounce = _first_touch(close, t, b, horizon)
        if bounce is None:
            continue
        tr = int(rng.integers(0, max(1, n - horizon - 1)))
        random_bounce = _first_touch(close, tr, close[tr] * (b / p), horizon)
        if t + fwd_days < n and not np.isnan(spy[t]) and not np.isnan(spy[t + fwd_days]):
            fwd = close[t + fwd_days] / p - 1
            exc = fwd - (spy[t + fwd_days] / spy[t] - 1)
        else:
            fwd = exc = np.nan
        rows.append((t, k, bounce, fwd, exc, random_bounce))
    return rows


def bootstrap_diff(df, fib, controls, col='bounce', n_boot=1000, seed=11):
    """종목 단위 부트스트랩으로 [피보나치 평균 - 대조 평균]의 95% 구간."""
    rng = np.random.default_rng(seed)
    g = df.dropna(subset=[col]).assign(is_fib=lambda x: x.ratio.isin(fib),
                                       is_ctl=lambda x: x.ratio.isin(controls))
    per = g.groupby('ticker').apply(lambda x: pd.Series({
        'fs': x.loc[x.is_fib, col].sum(), 'fn': x.is_fib.sum(),
        'cs': x.loc[x.is_ctl, col].sum(), 'cn': x.is_ctl.sum()}))
    arr = per.values
    diffs = []
    for _ in range(n_boot):
        s = arr[rng.integers(0, len(arr), len(arr))].sum(axis=0)
        diffs.append(s[0] / s[1] - s[2] / s[3])
    point = arr[:, 0].sum() / arr[:, 1].sum() - arr[:, 2].sum() / arr[:, 3].sum()
    return point, np.percentile(diffs, 2.5), np.percentile(diffs, 97.5)


def depth_matched_test(df, fib, col='bounce', n_boot=1000, seed=11, lo_gap=0.04, hi_gap=0.08):
    """
    깊이 보정 검정: 각 피보나치 비율을 '바로 옆(±0.04~0.08) 비율'과만 비교하고,
    그 차이를 피보나치 비율 전체에 대해 평균낸다. 신뢰구간은 종목 단위 부트스트랩.

    [왜 필요한가] 되돌림 깊이 자체가 반등 확률에 영향을 준다(얕을수록 높음).
    피보나치 집합과 대조 집합의 깊이 분포가 다르면, 단순 합산 비교는 '피보나치 효과'가 아니라
    '깊이 차이'를 측정하게 된다. 이웃 비율과만 비교하면 깊이가 거의 같아 이 혼동이 제거된다.
    """
    rng = np.random.default_rng(seed)
    d = df.dropna(subset=[col])
    grid = sorted(d.ratio.unique())
    fibs = [f for f in sorted(fib) if f in grid]
    neigh = {f: [g for g in grid if lo_gap <= abs(g - f) <= hi_gap and g not in fib] for f in fibs}
    fibs = [f for f in fibs if neigh[f]]

    tickers = d.ticker.unique()
    tix = {t: i for i, t in enumerate(tickers)}
    arr = np.zeros((len(tickers), len(fibs), 4))   # [종목, 피보나치, (피보 합, 피보 수, 이웃 합, 이웃 수)]
    ti = d.ticker.map(tix).values
    rv = d.ratio.values
    vv = d[col].values.astype(float)
    for j, f in enumerate(fibs):
        m = rv == f
        np.add.at(arr[:, j, 0], ti[m], vv[m]); np.add.at(arr[:, j, 1], ti[m], 1)
        m = np.isin(rv, neigh[f])
        np.add.at(arr[:, j, 2], ti[m], vv[m]); np.add.at(arr[:, j, 3], ti[m], 1)

    def stat(a):
        s_ = a.sum(axis=0)
        with np.errstate(invalid='ignore', divide='ignore'):
            return np.nanmean(s_[:, 0] / s_[:, 1] - s_[:, 2] / s_[:, 3])

    point = stat(arr)
    boots = [stat(arr[rng.integers(0, len(arr), len(arr))]) for _ in range(n_boot)]
    return point, np.percentile(boots, 2.5), np.percentile(boots, 97.5), len(fibs)


def main():
    ap = argparse.ArgumentParser(description='피보나치 되돌림 레벨 반등 확률 검증')
    ap.add_argument('--swing', type=float, default=0.20, help='스윙 최소 상승폭 (기본 20%%)')
    ap.add_argument('--barrier', type=float, default=0.15, help='반등/이탈 배리어 = 스윙 폭 대비 비율')
    ap.add_argument('--tol', type=float, default=0.03, help='터치 허용 오차 (비율 단위)')
    ap.add_argument('--csv', default=None)
    args = ap.parse_args()

    closes = pickle.load(open(PRICE_CACHE, 'rb'))
    spy = closes['SPY']
    ratios, fib, controls = build_ratio_grid()

    all_rows = []
    for ticker, s in closes.items():
        if ticker in ('SPY', 'QQQ', 'RSP') or len(s) < 300:
            continue
        s = s.dropna()
        c = s.values.astype(float)
        sp = spy.reindex(s.index).ffill().values.astype(float)
        ev = detect_events(c, ratios, swing_min=args.swing, tol=args.tol)
        for t, k, b, f, e, rb in evaluate(c, sp, ev, barrier_frac=args.barrier):
            all_rows.append((ticker, s.index[t], k, b, f, e, rb))

    df = pd.DataFrame(all_rows, columns=['ticker', 'date', 'ratio', 'bounce', 'fwd20', 'excess20',
                                         'random_bounce'])
    print(f"종목 {df.ticker.nunique()}개, 터치 이벤트 {len(df):,}건 "
          f"(스윙 +{args.swing * 100:.0f}%, 배리어 스윙폭의 {args.barrier * 100:.0f}%, 허용오차 ±{args.tol})")

    stat = df.groupby('ratio').agg(n=('bounce', 'size'), resolved=('bounce', 'count'),
                                   bounce=('bounce', 'mean'), random=('random_bounce', 'mean'),
                                   fwd20=('fwd20', 'mean'), excess20=('excess20', 'mean'))
    stat['type'] = ['피보나치' if r in fib else ('대조' if r in controls else '-') for r in stat.index]

    print("\n■ 비율별 반등 확률 (위아래 대칭 배리어, 정보가 없으면 약 50%)")
    print(f"{'비율':>7}{'구분':>8}{'이벤트':>9}{'반등 확률':>10}{'무작위일':>10}{'20일 수익':>10}{'SPY 대비':>10}")
    for r, row in stat.iterrows():
        if row.type == '-':
            continue   # 피보나치 근처 격자는 이웃 비교에만 사용
        mark = ' ◀' if row.type == '피보나치' else ''
        print(f"{r:>7.3f}{row.type:>8}{int(row.resolved):>9,}{row.bounce * 100:>9.1f}%{row.random * 100:>9.1f}%"
              f"{row.fwd20 * 100:>+9.2f}%{row.excess20 * 100:>+9.2f}%{mark}")

    print("\n■ 각 피보나치 비율 vs 바로 옆 비율 (±0.04~0.08, 다른 피보나치 비율 제외)")
    print(f"{'피보나치':>9}{'반등':>8}{'이웃 비율':>10}{'차이':>8}")
    for f in FIB_RATIOS:
        nb = [g for g in stat.index if 0.04 <= abs(g - f) <= 0.08 and g not in fib]
        fb = stat.loc[f, 'bounce']
        cb = df[df.ratio.isin(nb)].bounce.mean()
        print(f"{f:>9.3f}{fb * 100:>7.1f}%{cb * 100:>9.1f}%{(fb - cb) * 100:>+7.1f}%p")

    print(f"\n■ 기준선: 같은 종목의 무작위 날짜, 같은 폭 배리어 -> 반등 확률 {df.random_bounce.mean() * 100:.1f}%")
    print(f"  되돌림 터치 전체의 반등 확률 {df.bounce.mean() * 100:.1f}% "
          f"(차이 {(df.bounce.mean() - df.random_bounce.mean()) * 100:+.1f}%p)")

    point, lo, hi, nf = depth_matched_test(df, fib, 'bounce')
    print(f"\n■ 종합 판정 (깊이 보정): 피보나치 비율 vs 바로 옆 비율, {nf}개 비율 평균")
    print(f"  반등 확률 차이 {point * 100:+.2f}%p  (종목 부트스트랩 95% 구간 {lo * 100:+.2f} ~ {hi * 100:+.2f}%p)")
    verdict = '피보나치 비율이 특별함' if lo > 0 else ('피보나치 비율이 오히려 불리' if hi < 0 else '의미 있는 차이 없음')
    print(f"  => {verdict}")
    pe, elo, ehi, _ = depth_matched_test(df, fib, 'excess20')
    print(f"  20일 SPY 대비 초과수익 차이 {pe * 100:+.2f}%p (95% 구간 {elo * 100:+.2f} ~ {ehi * 100:+.2f}%p)")

    # 참고: 깊이 보정 없는 단순 합산 비교 (판정에 쓰지 않음)
    rp, rlo, rhi = bootstrap_diff(df, fib, controls, 'bounce')
    print(f"\n  (참고) 깊이 보정 없는 단순 합산 비교: {rp * 100:+.2f}%p [{rlo * 100:+.2f} ~ {rhi * 100:+.2f}]")
    print("  얕은 되돌림일수록 반등이 잦아, 깊이 분포가 다른 두 집합을 그냥 합치면 왜곡되므로 판정에 쓰지 않는다.")

    if args.csv:
        stat.to_csv(args.csv)


if __name__ == '__main__':
    main()
