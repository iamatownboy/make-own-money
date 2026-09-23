"""
scripts/factor_study.py
횡단면 팩터(기본: 12-1개월 가격 모멘텀) 검증 엔진.

설계 원칙
---------
1. 과거 시점 유니버스: 매월 말 그 시점의 실제 S&P 500 구성종목만 후보로 쓴다.
   (data/universes/sp500_monthly_membership.csv.gz, 출처 fja05680/sp500)
2. 파라미터 고정: 12-1개월, 10분위, 월 1회 교체, 동일가중. 학계 표준값이며 튜닝하지 않는다.
   튜닝은 과적합의 입구다. 변형(6-1개월, 5분위)은 '강건성 확인'용으로만 함께 보고한다.
3. 결손 편향 상쇄: yfinance에는 상장폐지 종목 시세가 대부분 없다. 그래서 1차 기준선은
   SPY가 아니라 '같은 결손을 가진 동일가중 유니버스'로 삼는다. 상위 분위와 기준선이
   같은 종목 풀에서 나오므로 결손 편향이 양쪽에 비슷하게 걸린다.
4. 관문(gate): 비용 차감 후 유니버스 대비 초과수익이 아래를 모두 통과해야 '채택 후보'.
     - Newey-West t > 1.96
     - 전반기·후반기 모두 플러스
     - 위약(무작위 포트폴리오) 대비 상위 5%
     - 분위 단조성(분위 번호와 평균 수익률의 순위상관) > 0.6
     - 결손이 적은 2019년 이후 구간에서도 플러스
5. 확률 출력: 분위별로 "다음 달 유니버스를 이길 확률"을 Wilson 신뢰구간과 함께 산출한다.
   스크리너가 최종적으로 보여줄 숫자의 형태다.

사용
----
    python scripts/factor_study.py                       # 12-1 모멘텀 기본 검증
    python scripts/factor_study.py --lookback 6          # 6-1 모멘텀
    python scripts/factor_study.py --start 2019-01       # 기간 제한
    python scripts/factor_study.py --csv out.csv         # 월별 시계열 저장
"""

import os
import sys
import time
import pickle
import argparse
import warnings

warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMBERSHIP_PATH = os.path.join(ROOT, 'data', 'universes', 'sp500_monthly_membership.csv.gz')
CACHE_DIR = os.path.join(ROOT, 'data', 'cache')
PRICE_CACHE = os.path.join(CACHE_DIR, 'sp500_adj_close.pkl')
BENCHMARKS = ['SPY', 'QQQ', 'RSP']


# ─────────────────────────────────────────────────────────────────────────────
# 데이터
# ─────────────────────────────────────────────────────────────────────────────
def load_membership(path: str = MEMBERSHIP_PATH) -> dict:
    """월말 날짜 -> 그 시점 S&P 500 구성종목 집합."""
    m = pd.read_csv(path)
    m['month_end'] = pd.to_datetime(m['month_end'])
    return {d: set(g['ticker']) for d, g in m.groupby('month_end')}


def load_prices(tickers, start='2012-11-01', cache_path=PRICE_CACHE, batch=80, verbose=True):
    """
    수정주가(배당·분할 반영) 종가를 받아 캐시한다. 이미 받은 종목은 다시 받지 않는다.
    시세가 없는 종목(상장폐지 등)은 빈 시리즈로 기록해 재요청하지 않는다.
    """
    import yfinance as yf

    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    closes = pickle.load(open(cache_path, 'rb')) if os.path.exists(cache_path) else {}
    todo = [t for t in tickers if t not in closes]
    if verbose and todo:
        print(f"시세 수집: {len(todo)}종목 (캐시 {len(closes)}종목)")

    for k in range(0, len(todo), batch):
        chunk = todo[k:k + batch]
        df = None
        for attempt in range(3):
            try:
                df = yf.download(chunk, start=start, auto_adjust=True, progress=False,
                                 threads=True, group_by='ticker')
                break
            except Exception:
                time.sleep(10)
        for t in chunk:
            try:
                s = df[t]['Close'] if len(chunk) > 1 else df['Close']
                s = s.dropna()
            except Exception:
                s = pd.Series(dtype=float)
            if getattr(s.index, 'tz', None) is not None:
                s.index = s.index.tz_localize(None)
            closes[t] = s
        pickle.dump(closes, open(cache_path, 'wb'))
        if verbose:
            print(f"  {min(k + batch, len(todo))}/{len(todo)}")
        time.sleep(2)
    return closes


def build_price_panel(closes: dict) -> pd.DataFrame:
    """
    SPY 거래일 달력에 맞춘 가격 패널.
    각 종목의 마지막 시세 이후로는 값을 채우지 않는다(상장폐지 이후 가짜 0% 수익 방지).
    시세 중간의 짧은 결측(최대 5거래일)만 직전 값으로 채운다.
    """
    cal = closes['SPY'].index
    panel = pd.DataFrame({t: s for t, s in closes.items() if len(s) > 0}).reindex(cal)
    last_valid = panel.apply(lambda s: s.last_valid_index())
    filled = panel.ffill(limit=5)
    alive = pd.DataFrame(
        cal.values[:, None] <= last_valid.values[None, :],
        index=cal, columns=panel.columns
    )
    return filled.where(alive)


# ─────────────────────────────────────────────────────────────────────────────
# 통계 도구
# ─────────────────────────────────────────────────────────────────────────────
def newey_west_t(x, lags: int = 3) -> float:
    """자기상관을 보정한 평균의 t통계량 (월간 수익률의 약한 자기상관 대비)."""
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < lags + 2:
        return float('nan')
    e = x - x.mean()
    s = e @ e / n
    for lag in range(1, lags + 1):
        w = 1 - lag / (lags + 1)
        s += 2 * w * (e[lag:] @ e[:-lag]) / n
    se = np.sqrt(s / n)
    return float(x.mean() / se) if se > 0 else float('nan')


def wilson_interval(wins: int, total: int, z: float = 1.96):
    if total == 0:
        return (float('nan'), float('nan'))
    p = wins / total
    denom = 1 + z ** 2 / total
    centre = p + z ** 2 / (2 * total)
    margin = z * np.sqrt(p * (1 - p) / total + z ** 2 / (4 * total ** 2))
    return ((centre - margin) / denom, (centre + margin) / denom)


def perf_stats(r: pd.Series) -> dict:
    """월간 수익률 시계열의 성과 요약 (무위험수익률은 0으로 가정)."""
    r = r.dropna()
    if r.empty:
        return {}
    growth = (1 + r).cumprod()
    years = len(r) / 12
    cagr = growth.iloc[-1] ** (1 / years) - 1
    vol = r.std(ddof=1) * np.sqrt(12)
    dd = (growth / growth.cummax() - 1).min()
    return {
        'cagr': cagr, 'vol': vol,
        'sharpe': (r.mean() * 12) / vol if vol > 0 else float('nan'),
        'max_dd': dd, 'worst_month': r.min(), 'months': len(r),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 백테스트
# ─────────────────────────────────────────────────────────────────────────────
def run_study(panel: pd.DataFrame, membership: dict, lookback: int = 12, skip: int = 1,
              n_bins: int = 10, cost_bps: float = 10.0, start: str = '2014-01',
              end: str = None, min_names: int = 100):
    """
    매월 말(formation) 모멘텀으로 분위를 나누고, 다음 달 수익률을 기록한다.

    모멘텀 = P(m-skip) / P(m-lookback) - 1   (기본 12-1: 직전 1개월 제외 11개월 수익률)
    다음 달 수익률 = P_last(m+1) / P(m) - 1
      P_last는 다음 달 말까지의 마지막 시세. 도중에 상장폐지되면 마지막 거래가에서
      현금화한 것으로 간주한다 (실제 상장폐지 정산가는 알 수 없음).
    """
    cal = panel.index
    month_ends = pd.Series(cal, index=cal).groupby(cal.to_period('M')).max()
    # 마지막 달이 아직 끝나지 않았으면 제외
    if month_ends.iloc[-1] < cal[-1] or month_ends.index[-1] == pd.Timestamp.today().to_period('M'):
        month_ends = month_ends.iloc[:-1]
    me = month_ends.values
    monthly = panel.loc[me]
    carried = panel.ffill().loc[me]   # 상장폐지 종목의 마지막 거래가 (exit용)

    mem_dates = sorted(membership)
    def members_asof(d):
        idx = np.searchsorted(np.array(mem_dates, dtype='datetime64[ns]'), np.datetime64(d), side='right') - 1
        return membership[mem_dates[idx]] if idx >= 0 else set()

    rows, stock_rows, holdings = [], [], {}
    prev_top = set()
    bench = {b: panel[b] for b in BENCHMARKS if b in panel.columns}

    for k in range(lookback, len(me) - 1):
        m = pd.Timestamp(me[k])
        if m < pd.Timestamp(start) or (end and m > pd.Timestamp(end)):
            continue
        nxt = pd.Timestamp(me[k + 1])
        members = members_asof(m) & set(panel.columns)

        p_now = monthly.iloc[k]
        p_skip = monthly.iloc[k - skip]
        p_base = monthly.iloc[k - lookback]
        mom = (p_skip / p_base - 1)
        eligible = [t for t in members
                    if pd.notna(p_now.get(t)) and pd.notna(p_skip.get(t)) and pd.notna(p_base.get(t))]
        if len(eligible) < min_names:
            continue

        mom = mom[eligible]
        fwd = (carried.iloc[k + 1][eligible] / p_now[eligible] - 1)
        delisted = int((monthly.iloc[k + 1][eligible].isna()).sum())

        ranks = mom.rank(method='first')
        bins = np.ceil(ranks / len(ranks) * n_bins).astype(int).clip(1, n_bins)

        rec = {'month': m, 'n': len(eligible), 'delisted_next': delisted,
               'universe': fwd.mean()}
        for b in range(1, n_bins + 1):
            rec[f'D{b}'] = fwd[bins == b].mean()

        top = set(bins[bins == n_bins].index)
        turnover = 1 - len(top & prev_top) / len(top) if prev_top else 1.0
        rec['top_turnover'] = turnover
        rec['top_cost'] = 2 * turnover * cost_bps / 10000     # 매도+매수
        rec['top_net'] = rec[f'D{n_bins}'] - rec['top_cost']
        prev_top = top
        holdings[m] = sorted(top)

        for bname, s in bench.items():
            a = s.loc[:m].dropna()
            b_ = s.loc[:nxt].dropna()
            rec[bname] = (b_.iloc[-1] / a.iloc[-1] - 1) if len(a) and len(b_) else np.nan

        rows.append(rec)
        u = rec['universe']
        spy = rec.get('SPY', np.nan)
        for t in eligible:
            stock_rows.append((m, t, int(bins[t]), float(mom[t]), float(fwd[t]),
                               float(fwd[t] > u), float(fwd[t] > spy) if pd.notna(spy) else np.nan))

    res = pd.DataFrame(rows).set_index('month')
    stocks = pd.DataFrame(stock_rows, columns=['month', 'ticker', 'bin', 'momentum', 'fwd',
                                               'beat_universe', 'beat_spy'])
    return res, stocks, holdings


def placebo_distribution(panel: pd.DataFrame, res: pd.DataFrame, stocks: pd.DataFrame,
                         n_bins: int = 10, sims: int = 1000, seed: int = 7) -> np.ndarray:
    """
    위약 대조: 매월 같은 유니버스에서 상위 분위와 같은 개수를 무작위로 뽑아 동일가중 보유.
    시뮬레이션마다 '유니버스 대비 연환산 초과수익'을 계산해 분포를 만든다.
    """
    rng = np.random.default_rng(seed)
    out = np.zeros(sims)
    by_month = {m: g['fwd'].values for m, g in stocks.groupby('month')}
    k_by_month = stocks[stocks['bin'] == n_bins].groupby('month').size().to_dict()
    months = [m for m in res.index if m in by_month]
    excess = np.zeros((sims, len(months)))
    for j, m in enumerate(months):
        f = by_month[m]
        k = k_by_month.get(m, max(1, len(f) // n_bins))
        idx = np.argsort(rng.random((sims, len(f))), axis=1)[:, :k]
        excess[:, j] = f[idx].mean(axis=1) - res.loc[m, 'universe']
    out = excess.mean(axis=1) * 12
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 리포트
# ─────────────────────────────────────────────────────────────────────────────
def pct(x, d=1):
    return f"{x * 100:+.{d}f}%" if x == x else '-'


def report(res: pd.DataFrame, stocks: pd.DataFrame, placebo: np.ndarray, n_bins: int,
           label: str, lookback: int, skip: int, cost_bps: float) -> dict:
    top = f'D{n_bins}'
    print(f"\n{'=' * 78}\n{label}  |  {lookback}-{skip}개월 모멘텀, {n_bins}분위, 월 1회, "
          f"편도 비용 {cost_bps:.0f}bp\n{'=' * 78}")
    print(f"기간 {res.index.min():%Y-%m} ~ {res.index.max():%Y-%m} ({len(res)}개월), "
          f"월평균 후보 {res['n'].mean():.0f}종목, 상위 분위 {stocks[stocks.bin == n_bins].groupby('month').size().mean():.0f}종목, "
          f"상위 분위 월 교체율 {res['top_turnover'].iloc[1:].mean() * 100:.0f}%")

    # 1) 성과 요약
    print("\n■ 1. 성과 요약 (연환산, 무위험수익률 0 가정)")
    print(f"{'포트폴리오':<24}{'CAGR':>9}{'변동성':>9}{'샤프':>7}{'최대낙폭':>10}{'최악월':>9}")
    series = [('상위 분위 (비용 후)', res['top_net']), ('상위 분위 (비용 전)', res[top]),
              ('하위 분위', res['D1']), ('동일가중 유니버스', res['universe'])]
    series += [(b, res[b]) for b in BENCHMARKS if b in res]
    for name, s in series:
        p = perf_stats(s)
        print(f"{name:<24}{pct(p['cagr']):>9}{pct(p['vol']):>9}{p['sharpe']:>7.2f}"
              f"{pct(p['max_dd']):>10}{pct(p['worst_month']):>9}")

    # 2) 초과수익 검정
    print("\n■ 2. 월간 초과수익 검정 (상위 분위 비용 후 - 기준)")
    print(f"{'기준':<20}{'월평균':>9}{'연환산':>9}{'NW t':>8}{'이긴 달':>9}")
    tests = {}
    for bname in ['universe'] + [b for b in BENCHMARKS if b in res]:
        e = (res['top_net'] - res[bname]).dropna()
        t = newey_west_t(e)
        tests[bname] = (e.mean(), t)
        print(f"{('동일가중 유니버스' if bname == 'universe' else bname):<20}{pct(e.mean(), 2):>9}"
              f"{pct(e.mean() * 12):>9}{t:>+8.2f}{(e > 0).mean() * 100:>8.0f}%")
    spread = res[top] - res['D1']
    print(f"{'(참고) 상위-하위 분위':<20}{pct(spread.mean(), 2):>9}{pct(spread.mean() * 12):>9}"
          f"{newey_west_t(spread):>+8.2f}{(spread > 0).mean() * 100:>8.0f}%")

    # 3) 분위 단조성
    means = [res[f'D{b}'].mean() for b in range(1, n_bins + 1)]
    mono = pd.Series(range(1, n_bins + 1)).corr(pd.Series(means), method='spearman')
    print("\n■ 3. 분위별 다음 달 평균 수익률 (단조 증가면 신호가 순위 정보를 담고 있다는 뜻)")
    print('  ' + '  '.join(f"D{b}:{m * 100:+.2f}" for b, m in zip(range(1, n_bins + 1), means)))
    print(f"  순위상관(분위 번호 vs 평균 수익률) = {mono:+.2f}")

    # 4) 시기 안정성
    e_u = res['top_net'] - res['universe']
    half = len(e_u) // 2
    h1, h2 = e_u.iloc[:half], e_u.iloc[half:]
    print("\n■ 4. 시기 안정성 (유니버스 대비 초과, 연환산)")
    print(f"  전반기 {h1.index.min():%Y-%m}~{h1.index.max():%Y-%m}: {pct(h1.mean() * 12)} (t={newey_west_t(h1):+.2f})"
          f"   후반기 {h2.index.min():%Y-%m}~{h2.index.max():%Y-%m}: {pct(h2.mean() * 12)} (t={newey_west_t(h2):+.2f})")
    yearly = e_u.groupby(e_u.index.year).apply(lambda x: (1 + res.loc[x.index, 'top_net']).prod()
                                                - (1 + res.loc[x.index, 'universe']).prod())
    print('  연도별: ' + '  '.join(f"{y}:{v * 100:+.1f}" for y, v in yearly.items()))
    recent = e_u[e_u.index >= '2019-01-01']
    print(f"  결손이 적은 2019년 이후: {pct(recent.mean() * 12)} (t={newey_west_t(recent):+.2f}, {len(recent)}개월)")

    # 5) 위약
    real = e_u.mean() * 12
    pctl = (placebo < real).mean() * 100
    print("\n■ 5. 위약 대조 (같은 수의 종목을 매월 무작위로 뽑은 포트폴리오 1,000개)")
    print(f"  무작위 포트폴리오의 유니버스 대비 연환산 초과: 중앙값 {pct(np.median(placebo))}, "
          f"95분위 {pct(np.percentile(placebo, 95))}")
    print(f"  모멘텀 상위 분위(비용 후): {pct(real)} -> 무작위 대비 상위 {100 - pctl:.1f}% "
          f"(백분위 {pctl:.1f})")

    # 6) 확률 캘리브레이션
    print("\n■ 6. 종목 단위 확률 — '다음 달 동일가중 유니버스를 이길 확률' (95% Wilson 구간)")
    print(f"{'분위':<6}{'표본':>8}{'유니버스 이길 확률':>20}{'SPY 이길 확률':>18}{'평균 다음달 수익':>16}")
    for b in range(1, n_bins + 1):
        g = stocks[stocks['bin'] == b]
        w = int(g['beat_universe'].sum()); n = len(g)
        lo, hi = wilson_interval(w, n)
        gs = g.dropna(subset=['beat_spy'])
        ws = int(gs['beat_spy'].sum()); ns = len(gs)
        slo, shi = wilson_interval(ws, ns)
        print(f"D{b:<5}{n:>8}{w / n * 100:>9.1f}% [{lo * 100:.1f}~{hi * 100:.1f}]"
              f"{ws / ns * 100:>8.1f}% [{slo * 100:.1f}~{shi * 100:.1f}]{g['fwd'].mean() * 100:>+13.2f}%")

    # 7) 검정력: 이 크기의 엣지를 확인하는 데 필요한 기간
    te = e_u.std(ddof=1) * np.sqrt(12)
    ann = e_u.mean() * 12
    print("\n■ 7. 검정력 — 이 크기의 초과수익을 t=1.96으로 확인하려면 몇 년치 데이터가 필요한가")
    print(f"  관측 초과수익 {pct(ann)}/년, 추적오차 {te * 100:.1f}%/년, 정보비율 {ann / te:.2f}")
    if ann > 0:
        print(f"  -> 필요 기간 약 {(1.96 * te / ann) ** 2:.0f}년 (현재 {len(e_u) / 12:.1f}년)")
    print("  엣지 크기별: " + ", ".join(f"{a * 100:.0f}%p->{(1.96 * te / a) ** 2:.0f}년"
                                        for a in [0.01, 0.02, 0.03, 0.05]))
    print("  함의: 작은 엣지는 실전 로그로 입증할 수 없다. 근거는 수십 년 역사 데이터에서 찾고,")
    print("        실전 로그는 '백테스트대로 작동하는가(구현 충실도·비용·버그)'를 확인하는 데 쓴다.")

    # 8) 관문 판정
    t_u = tests['universe'][1]
    checks = {
        '유니버스 대비 NW t > 1.96 (비용 후)': bool(t_u > 1.96),
        '전반기·후반기 모두 플러스': bool(h1.mean() > 0 and h2.mean() > 0),
        '위약 대비 상위 5%': bool(pctl >= 95),
        '분위 단조성 > 0.6': bool(mono > 0.6),
        '2019년 이후 플러스': bool(recent.mean() > 0),
    }
    print("\n■ 8. 관문 판정")
    for k, v in checks.items():
        print(f"  [{'통과' if v else '실패'}] {k}")
    passed = all(checks.values())
    print(f"  => {'채택 후보 (실전 추적 단계로 진행 가능)' if passed else '기각 (현 증거로는 엣지 불충분)'}")
    return {'checks': checks, 'passed': passed, 'excess_universe': tests['universe'],
            'monotonicity': mono, 'placebo_pctl': pctl}


def main():
    ap = argparse.ArgumentParser(description='횡단면 모멘텀 팩터 검증')
    ap.add_argument('--lookback', type=int, default=12)
    ap.add_argument('--skip', type=int, default=1)
    ap.add_argument('--bins', type=int, default=10)
    ap.add_argument('--cost-bps', type=float, default=10.0, help='편도 거래비용 (bp)')
    ap.add_argument('--start', default='2014-01')
    ap.add_argument('--end', default=None)
    ap.add_argument('--csv', default=None, help='월별 결과 저장 경로')
    ap.add_argument('--stocks-csv', default=None, help='종목-월 단위 결과 저장 경로')
    args = ap.parse_args()

    membership = load_membership()
    tickers = sorted(set().union(*membership.values())) + BENCHMARKS
    closes = load_prices(tickers)
    panel = build_price_panel(closes)

    all_members = set().union(*membership.values())
    covered = {t for t in all_members if t in panel.columns}
    print(f"과거 시점 구성종목 {len(all_members)}개 중 시세 확보 {len(covered)}개 "
          f"({len(covered) / len(all_members) * 100:.0f}%) — 미확보는 대부분 상장폐지·피인수 종목")

    res, stocks, _ = run_study(panel, membership, lookback=args.lookback, skip=args.skip,
                               n_bins=args.bins, cost_bps=args.cost_bps,
                               start=args.start, end=args.end)
    cov = res['n'] / pd.Series({m: len(membership[max(d for d in membership if d <= m)])
                                for m in res.index})
    print(f"월별 후보 커버리지(구성종목 중 계산 가능 비율): 평균 {cov.mean() * 100:.0f}%, "
          f"최저 {cov.min() * 100:.0f}% ({cov.idxmin():%Y-%m})")

    placebo = placebo_distribution(panel, res, stocks, n_bins=args.bins)
    report(res, stocks, placebo, args.bins, 'S&P 500 과거 시점 유니버스',
           args.lookback, args.skip, args.cost_bps)

    if args.csv:
        res.to_csv(args.csv)
    if args.stocks_csv:
        stocks.to_csv(args.stocks_csv, index=False)


if __name__ == '__main__':
    main()
