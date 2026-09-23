"""
scripts/edge_analysis.py
전략 엣지 진단 도구.

실제 진입 규칙(evaluate_pattern_match + predict_price_scenarios + RR 게이트)으로
과거 독립 신호를 재생성하고, 각 거래를 실전과 동일한 triple-barrier로 정산한 뒤
벤치마크(QQQ) 대비 초과수익을 시장 레짐별/패턴별로 분해하여 t검정까지 수행한다.

핵심: 절대 승률이 아니라 '초과수익의 통계적 유의성'을 본다.
상승장 유니버스에서 절대 승률은 대부분 시장 베타이기 때문이다.

사용:
    python scripts/edge_analysis.py                 # 기본 유니버스 전체
    python scripts/edge_analysis.py --limit 15      # 앞 15종목만 (빠른 확인)
    python scripts/edge_analysis.py --holding 10    # 보유기간 변경
    python scripts/edge_analysis.py --exits         # 청산 정책 비교 + 견고성 검증
    python scripts/edge_analysis.py --placebo       # 무작위 진입 대조군과 비교

    # 생존 편향 없는 검증 (과거 시점 구성종목) — 가장 신뢰할 수 있는 설정
    python scripts/edge_analysis.py --universe data/universes/ndx100_2024-09.csv --exits --placebo
"""

import os
import sys
import argparse
import warnings

warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import yfinance as yf
from scipy import stats

from quant_core.data_loader import clean_ohlcv
from quant_core.indicators import calculate_all_indicators
from quant_core.prediction import predict_price_scenarios
from quant_core.screener import (
    CORE_UNIVERSE,
    BENCHMARK_TICKER,
    evaluate_pattern_match,
    resolve_triple_barrier,
    get_benchmark_series,
    benchmark_return_between,
)

PATTERNS = ['fibonacci', 'trendline', 'divergence', 'auto']


def load_universe(spec: str):
    """
    분석 유니버스를 불러온다.
      'core'          : screener.CORE_UNIVERSE (현행 82종목, 사후 선정 목록)
      <CSV 경로>      : Symbol 열을 가진 CSV. 과거 시점 구성종목(point-in-time)으로
                        생존 편향 없이 검증할 때 사용한다.
                        예) data/universes/ndx100_2024-09.csv
    """
    if spec == 'core':
        return CORE_UNIVERSE, 'CORE_UNIVERSE (사후 선정)'
    df = pd.read_csv(spec)
    col = 'Symbol' if 'Symbol' in df.columns else df.columns[0]
    tickers = [str(t).strip().replace('.', '-') for t in df[col].dropna().unique()]
    return [{'ticker': t} for t in tickers], os.path.basename(spec)

FEE_PCT = 0.25


# ─────────────────────────────────────────────────────────────────────────────
# 청산 정책 (실험용) — 동일 진입 신호에 서로 다른 청산 규칙을 적용해 페어 비교한다.
# 모든 정책은 갭 체결을 반영하고, 추적 손절 갱신에는 직전 봉 ATR만 사용한다(미래정보 차단).
# ─────────────────────────────────────────────────────────────────────────────
def _exit_fixed(df, i, entry, target, stop, max_days):
    fwd = df.iloc[i + 1: i + 1 + max_days]
    if fwd.empty:
        return None
    o = resolve_triple_barrier(entry, target, stop, fwd, FEE_PCT)
    return o['net_return'], o['exit_offset'], o['outcome']


def _exit_trailing(df, i, entry, stop, atr_mult, max_days, target=None):
    highest = entry
    for k in range(1, max_days + 1):
        j = i + k
        if j >= len(df):
            break
        o = float(df['Open'].iloc[j]); h = float(df['High'].iloc[j])
        l = float(df['Low'].iloc[j]); c = float(df['Close'].iloc[j])
        atr = float(df['ATR_14'].iloc[j - 1])
        if target and o >= target:
            return ((o / entry) - 1) * 100 - FEE_PCT, k, 'TARGET'
        if o <= stop:
            return ((o / entry) - 1) * 100 - FEE_PCT, k, 'STOP'
        if target and h >= target:
            return ((target / entry) - 1) * 100 - FEE_PCT, k, 'TARGET'
        if l <= stop:
            return ((stop / entry) - 1) * 100 - FEE_PCT, k, 'STOP'
        highest = max(highest, c)
        stop = max(stop, highest - atr * atr_mult)
    j = min(i + max_days, len(df) - 1)
    return ((float(df['Close'].iloc[j]) / entry) - 1) * 100 - FEE_PCT, j - i, 'TIME'


def _exit_supertrend(df, i, entry, stop, max_days):
    for k in range(1, max_days + 1):
        j = i + k
        if j >= len(df):
            break
        o = float(df['Open'].iloc[j]); l = float(df['Low'].iloc[j]); c = float(df['Close'].iloc[j])
        if o <= stop:
            return ((o / entry) - 1) * 100 - FEE_PCT, k, 'STOP'
        if l <= stop:
            return ((stop / entry) - 1) * 100 - FEE_PCT, k, 'STOP'
        if float(df['Supertrend_Direction'].iloc[j]) == -1:
            return ((c / entry) - 1) * 100 - FEE_PCT, k, 'TREND_EXIT'
    j = min(i + max_days, len(df) - 1)
    return ((float(df['Close'].iloc[j]) / entry) - 1) * 100 - FEE_PCT, j - i, 'TIME'


EXIT_POLICIES = {
    'fixed_t1_20d':     lambda df, i, e, t1, t2, st: _exit_fixed(df, i, e, t1, st, 20),
    'fixed_t2_40d':     lambda df, i, e, t1, t2, st: _exit_fixed(df, i, e, t2, st, 40),
    'trail_2atr_40d':   lambda df, i, e, t1, t2, st: _exit_trailing(df, i, e, st, 2.0, 40),
    'trail_3atr_60d':   lambda df, i, e, t1, t2, st: _exit_trailing(df, i, e, st, 3.0, 60),
    'trail_2atr_t1':    lambda df, i, e, t1, t2, st: _exit_trailing(df, i, e, st, 2.0, 40, target=t1),
    'supertrend_40d':   lambda df, i, e, t1, t2, st: _exit_supertrend(df, i, e, st, 40),
}
BASELINE_POLICY = 'fixed_t1_20d'
MAX_EXIT_HORIZON = 60  # EXIT_POLICIES 중 최장 보유 기간


def _t(x) -> float:
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) < 2:
        return float('nan')
    sd = x.std(ddof=1)
    return float(x.mean() / (sd / np.sqrt(len(x)))) if sd > 0 else float('nan')


def robustness_report(trades: pd.DataFrame, col: str) -> dict:
    """
    한 청산 정책의 초과수익이 '진짜 엣지'인지 판정하는 견고성 검증.
    거래 단위 t값 하나로는 부족하다. 다음을 모두 통과해야 채택 후보가 된다.
      1) 시기 클러스터 t : 같은 시기 거래는 서로 상관 -> 월 단위로 묶어 재검정
      2) 시기 안정성     : 전반기/후반기 부호가 같아야 함
      3) 이상치 의존성   : 중앙값·절사평균이 평균과 같은 부호여야 함
      4) 생존 편향       : 종목의 사후 2년 성과 하위 1/3(패자)에서 유의한 손실이 없어야 함
    """
    x = trades[col]
    mean = x.mean()
    median = x.median()
    trimmed = float(stats.trim_mean(x, 0.05))
    top_share = (x.nlargest(max(1, len(x) // 100)).sum() / x.sum() * 100) if x.sum() > 0 else float('nan')

    month_means = trades.groupby(trades['entry_date'].dt.to_period('M'))[col].mean()
    month_t = _t(month_means)

    mid = trades['entry_date'].median()
    first, second = x[trades['entry_date'] <= mid], x[trades['entry_date'] > mid]

    losers = x[trades['bh_group'] == '패자']
    loser_t = _t(losers)

    checks = {
        '월클러스터 |t|>1.96': bool(abs(month_t) > 1.96) if month_t == month_t else False,
        '전후반 부호 일치': bool(np.sign(first.mean()) == np.sign(second.mean()) == np.sign(mean)),
        '중앙값·절사평균 부호 일치': bool(np.sign(median) == np.sign(trimmed) == np.sign(mean)),
        '패자 종목 유의 손실 없음': bool(not (loser_t == loser_t and loser_t < -1.96)),
    }
    return {
        'mean': mean, 'median': median, 'trimmed': trimmed, 'top1pct_share': top_share,
        'trade_t': _t(x), 'month_t': month_t,
        'first_half': first.mean(), 'second_half': second.mean(),
        'loser': losers.mean(), 'loser_t': loser_t,
        'winner': x[trades['bh_group'] == '승자'].mean(),
        'checks': checks, 'passed': all(checks.values()),
    }


def load_benchmark_close(period: str) -> pd.Series:
    """레짐 판정용 벤치마크 종가 시계열."""
    bdf = clean_ohlcv(yf.Ticker(BENCHMARK_TICKER).history(period=period, interval='1d'))
    if bdf is None or bdf.empty:
        return None
    bdf.index = pd.to_datetime(bdf.index).normalize()
    return bdf['Close']


def regime_at(bench_close: pd.Series, date) -> str:
    """
    진입 시점까지의 정보만으로 시장 레짐을 분류한다.
    screener.get_market_context_regime()과 동일한 규칙 (미래 정보 없음).
    """
    if bench_close is None:
        return None
    s = bench_close.loc[:pd.Timestamp(date).normalize()]
    if len(s) < 50:
        return None
    price = float(s.iloc[-1])
    ma20 = float(s.tail(20).mean())
    ma50 = float(s.tail(50).mean())
    ret_20d = (price / float(s.iloc[-20]) - 1) * 100

    if price >= ma20 and ma20 >= ma50:
        return '강세 상승장'
    if price < ma20 and price < ma50 and ret_20d < -5.0:
        return '약세 조정장'
    return '박스권 횡보장'


def _collect_placebo(df, ticker, bench_series, holding_days, min_rr, fee_pct,
                     with_exits, sink):
    """
    위약(placebo) 대조군: 패턴 없이 고정 간격(holding_days)의 무작위 스케줄로 진입한다.
    시작 오프셋을 5가지로 달리해 표본을 늘린다. 손절/목표/RR 게이트/정산 규칙은
    패턴 진입과 완전히 동일하므로, 패턴 진입과의 차이가 곧 '패턴이 주는 정보'다.
    동시에 같은 진입 시점의 '단순 보유(holding_days)' 결과도 기록해,
    손절/목표 구조 자체의 효과를 분리한다.
    """
    horizon = MAX_EXIT_HORIZON if with_exits else holding_days
    last_i = len(df) - horizon - 1
    close = df['Close']
    for offset in range(0, holding_days, max(1, holding_days // 5)):
        for i in range(30 + offset, last_i, holding_days):
            entry = float(close.iloc[i])
            entry_date = df.index[i]

            hold_net = (float(close.iloc[i + holding_days]) / entry - 1) * 100 - fee_pct
            hold_bench = benchmark_return_between(bench_series, entry_date, df.index[i + holding_days])

            scen = predict_price_scenarios(df.iloc[:i + 1], days_ahead=5)
            target = float(scen.get('bull_target_1') or 0.0)
            stop = float(scen.get('stop_loss') or 0.0)
            barrier_ok = (0 < stop < entry < target) and (target - entry) / (entry - stop) >= min_rr

            rec = {'ticker': ticker, 'entry_date': entry_date,
                   'hold_net': hold_net,
                   'hold_excess': hold_net - hold_bench if hold_bench is not None else np.nan,
                   'barrier_net': np.nan, 'barrier_excess': np.nan}
            if barrier_ok:
                o = resolve_triple_barrier(entry, target, stop,
                                           df.iloc[i + 1: i + 1 + holding_days], fee_pct)
                b = benchmark_return_between(bench_series, entry_date,
                                             df.index[i + o['exit_offset']])
                rec['barrier_net'] = o['net_return']
                rec['barrier_excess'] = o['net_return'] - b if b is not None else np.nan
            sink.append(rec)


def print_placebo_comparison(trades: pd.DataFrame, placebo: pd.DataFrame):
    """패턴 진입 vs 무작위 진입, 그리고 단순 보유 대비 손절/목표 구조의 효과를 분해한다."""
    placebo = placebo.copy()
    placebo['entry_date'] = pd.to_datetime(placebo['entry_date'])
    months_p = placebo['entry_date'].dt.to_period('M')
    months_t = trades['entry_date'].dt.to_period('M')

    hold = placebo.dropna(subset=['hold_excess'])
    barrier = placebo.dropna(subset=['barrier_excess'])

    print("\n■ 위약(무작위 진입) 대조 — 수익이 어디서 오고 어디서 새는가")
    print('-' * 72)
    print(f"{'구성':<30}{'n':>7}{'순수익':>9}{'QQQ초과':>10}{'거래t':>8}{'월t':>7}")
    print('-' * 72)
    specs = [
        ('① 무작위 진입 + 단순 보유', hold, 'hold_net', 'hold_excess', months_p.loc[hold.index]),
        ('② 무작위 진입 + 현행 손절/목표', barrier, 'barrier_net', 'barrier_excess', months_p.loc[barrier.index]),
        ('③ 패턴 진입 + 현행 손절/목표', trades, 'net_return', 'excess_return', months_t),
    ]
    for label, df_, net_col, exc_col, months in specs:
        exc = df_[exc_col]
        print(f"{label:<30}{len(df_):>7}{df_[net_col].mean():>+9.2f}{exc.mean():>+10.2f}"
              f"{_t(exc):>+8.2f}{_t(exc.groupby(months).mean()):>+7.2f}")
    print('-' * 72)
    print("  ①→② 차이 = 손절/목표 구조의 효과,  ②→③ 차이 = 패턴이 주는 정보")

    # 패턴의 증분 정보: 같은 종목 안에서 [패턴 초과 - 무작위 초과]
    g = pd.DataFrame({
        'pattern': trades.groupby('ticker')['excess_return'].mean(),
        'random': barrier.groupby('ticker')['barrier_excess'].mean(),
    }).dropna()
    diff = g['pattern'] - g['random']
    pm = trades.groupby(months_t)['excess_return'].mean()
    rm = barrier.groupby(months_p.loc[barrier.index])['barrier_excess'].mean()
    mdiff = (pm - rm).dropna()
    print(f"\n■ 패턴의 증분 정보 (같은 종목 내 [패턴 - 무작위], {len(g)}종목 페어)")
    print(f"  평균 {diff.mean():+.2f}%p  중앙값 {diff.median():+.2f}%p  t={_t(diff):+.2f}  "
          f"패턴이 나은 종목 {(diff > 0).mean() * 100:.0f}%")
    print(f"  월 단위 페어: 평균 {mdiff.mean():+.2f}%p  t={_t(mdiff):+.2f}  ({len(mdiff)}개월)")
    verdict = '패턴에 정보가 있음' if abs(_t(diff)) > 1.96 and diff.mean() > 0 else '무작위 진입과 구별되지 않음'
    print(f"  -> {verdict}")


def collect_trades(tickers, period='2y', holding_days=20, min_rr=1.20,
                   fee_slippage_pct=0.25, verbose=True, with_exits=False,
                   placebo_sink=None):
    """실전 규칙으로 과거 독립 신호를 재생성하고 거래 단위로 정산한다."""
    bench_series = get_benchmark_series(period=period)
    bench_close = load_benchmark_close(period)
    trades = []
    missing = []

    for idx, item in enumerate(tickers, start=1):
        ticker = item['ticker']
        try:
            df = clean_ohlcv(yf.Ticker(ticker).history(period=period, interval='1d'))
            if df is None or df.empty or len(df) < 60:
                missing.append(ticker)
                continue
            df = calculate_all_indicators(df)
            # 종목 자체의 기간 단순보유 수익률 (생존 편향 검증용, 사후 정보이므로 분류에만 사용)
            buy_hold = (float(df['Close'].iloc[-1]) / float(df['Close'].iloc[0]) - 1) * 100
        except Exception as exc:
            missing.append(ticker)
            if verbose:
                print(f"  [{ticker}] 수집 실패: {exc}")
            continue

        if placebo_sink is not None:
            _collect_placebo(df, ticker, bench_series, holding_days, min_rr,
                             fee_slippage_pct, with_exits, placebo_sink)

        for pattern in PATTERNS:
            last_signal = -10 ** 9
            for i in range(30, len(df) - holding_days):
                # 보유기간 중복 신호 제거 (표본 독립성)
                if i - last_signal < holding_days:
                    continue
                df_slice = df.iloc[:i + 1]
                if not evaluate_pattern_match(df_slice, pattern):
                    continue

                entry = float(df['Close'].iloc[i])
                scen = predict_price_scenarios(df_slice, days_ahead=5)
                target = float(scen.get('bull_target_1') or 0.0)
                stop = float(scen.get('stop_loss') or 0.0)
                if not (0 < stop < entry < target):
                    continue
                if (target - entry) / (entry - stop) < min_rr:
                    continue

                last_signal = i
                forward = df.iloc[i + 1: i + 1 + holding_days]
                if forward.empty:
                    continue

                outcome = resolve_triple_barrier(
                    entry, target, stop, forward, fee_slippage_pct
                )
                entry_date = df.index[i]
                exit_date = forward.index[outcome['exit_offset'] - 1]
                bench_ret = benchmark_return_between(bench_series, entry_date, exit_date)
                regime = regime_at(bench_close, entry_date)
                if bench_ret is None or regime is None:
                    continue

                rec = {
                    'ticker': ticker,
                    'pattern': pattern,
                    'regime': regime,
                    'entry_date': entry_date,
                    'buy_hold_return': buy_hold,
                    'held_days': outcome['exit_offset'],
                    'net_return': outcome['net_return'],
                    'benchmark_return': bench_ret,
                    'excess_return': outcome['net_return'] - bench_ret,
                    'outcome': outcome['outcome'],
                }

                if with_exits:
                    # 우측 절단(right-censoring) 방지: 가장 긴 정책(60일)의 창이 데이터 안에
                    # 온전히 들어오는 신호만 비교한다. 그렇지 않으면 최근 신호일수록 장기 정책이
                    # 강제 조기 청산되어 정책 간 비교가 불공정해진다.
                    if i + MAX_EXIT_HORIZON >= len(df):
                        continue
                    target_2 = float(scen.get('bull_target_2') or 0.0)
                    if target_2 <= target:
                        target_2 = target * 1.05
                    valid = True
                    for name, fn in EXIT_POLICIES.items():
                        res = fn(df, i, entry, target, target_2, stop)
                        if res is None:
                            valid = False
                            break
                        net, off, oc = res
                        b = benchmark_return_between(
                            bench_series, entry_date, df.index[min(i + off, len(df) - 1)]
                        )
                        if b is None:
                            valid = False
                            break
                        rec[f'x_{name}'] = net - b
                        rec[f'x_{name}_days'] = off
                    if not valid:
                        continue

                trades.append(rec)

        if verbose and idx % 10 == 0:
            print(f"  ... {idx}/{len(tickers)} 종목 처리 (누적 거래 {len(trades)}건)")

    out = pd.DataFrame(trades)
    if not out.empty:
        per_ticker = out.drop_duplicates('ticker').set_index('ticker')['buy_hold_return']
        q1, q2 = per_ticker.quantile([1 / 3, 2 / 3])
        out['bh_group'] = np.where(
            out.buy_hold_return <= q1, '패자',
            np.where(out.buy_hold_return <= q2, '중위', '승자')
        )
        out['entry_date'] = pd.to_datetime(out['entry_date'])
    out.attrs['missing_tickers'] = missing
    return out


def summarize(df: pd.DataFrame, by: str, min_samples: int = 1) -> pd.DataFrame:
    """그룹별 초과수익 평균과 t통계량, 95% 신뢰구간을 산출한다."""
    rows = []
    for key, grp in df.groupby(by):
        n = len(grp)
        if n < min_samples:
            continue
        mean = grp.excess_return.mean()
        sd = grp.excess_return.std(ddof=1) if n > 1 else np.nan
        se = sd / np.sqrt(n) if n > 1 and sd > 0 else np.nan
        tstat = mean / se if se == se and se > 0 else np.nan
        rows.append({
            'group': key,
            'n': n,
            'strategy': round(grp.net_return.mean(), 2),
            'benchmark': round(grp.benchmark_return.mean(), 2),
            'excess': round(mean, 2),
            't': round(tstat, 2) if tstat == tstat else None,
            'ci_low': round(mean - 1.96 * se, 2) if se == se else None,
            'ci_high': round(mean + 1.96 * se, 2) if se == se else None,
            'target_hit_pct': round((grp.outcome == 'TARGET').mean() * 100, 1),
            'significant': bool(tstat == tstat and abs(tstat) > 1.96),
        })
    return pd.DataFrame(rows).sort_values('n', ascending=False)


def print_table(title: str, table: pd.DataFrame):
    print(f"\n■ {title}")
    print('-' * 86)
    print(f"{'구분':<16}{'n':>6}{'전략':>8}{'QQQ':>8}{'초과':>8}{'t':>7}"
          f"{'95% 신뢰구간':>22}{'목표도달':>9}{'유의':>6}")
    print('-' * 86)
    for _, r in table.iterrows():
        ci = f"[{r.ci_low:+.2f}, {r.ci_high:+.2f}]" if r.ci_low is not None else "-"
        t = f"{r.t:+.2f}" if r.t is not None else "-"
        mark = "예" if r.significant else "아니오"
        print(f"{str(r.group):<16}{r.n:>6}{r.strategy:>+8.2f}{r.benchmark:>+8.2f}"
              f"{r.excess:>+8.2f}{t:>7}{ci:>22}{r.target_hit_pct:>8.0f}%{mark:>6}")


def print_exit_comparison(trades: pd.DataFrame):
    """동일 진입 신호에 대한 청산 정책 페어 비교 + 견고성 판정."""
    base = trades[f'x_{BASELINE_POLICY}']
    print(f"\n■ 청산 정책 비교 — 동일 진입 신호 {len(trades)}건 (페어 비교)")
    print('-' * 96)
    print(f"{'정책':<17}{'초과평균':>9}{'중앙값':>8}{'절사평균':>9}{'거래t':>7}{'월t':>7}"
          f"{'전반':>7}{'후반':>7}{'패자':>7}{'승자':>7}{'보유일':>7}{'판정':>8}")
    print('-' * 96)
    reports = {}
    for name in EXIT_POLICIES:
        col = f'x_{name}'
        r = robustness_report(trades, col)
        reports[name] = r
        verdict = '채택후보' if r['passed'] else '기각'
        print(f"{name:<17}{r['mean']:>+9.2f}{r['median']:>+8.2f}{r['trimmed']:>+9.2f}"
              f"{r['trade_t']:>+7.2f}{r['month_t']:>+7.2f}{r['first_half']:>+7.2f}"
              f"{r['second_half']:>+7.2f}{r['loser']:>+7.2f}{r['winner']:>+7.2f}"
              f"{trades[col + '_days'].mean():>7.1f}{verdict:>8}")
    print('-' * 96)
    print("판정 기준: 월클러스터 |t|>1.96 · 전후반 부호 일치 · 중앙값/절사평균 부호 일치 · 패자 종목 유의 손실 없음")
    for name, r in reports.items():
        failed = [k for k, v in r['checks'].items() if not v]
        if failed and name != BASELINE_POLICY:
            share = r['top1pct_share']
            share_txt = f"상위1% 거래가 초과합의 {share:.0f}%" if share == share else "초과합이 0 이하"
            print(f"  {name:<17} 실패: {', '.join(failed)}  ({share_txt})")

    # 현행 청산 대비 페어 개선 — "QQQ를 이기는가"와 별개로 "지금보다 나은가"를 검정
    print(f"\n■ 현행 청산({BASELINE_POLICY}) 대비 개선폭 — 페어 차이")
    print('-' * 96)
    print(f"{'정책':<17}{'개선평균':>9}{'개선중앙값':>10}{'개선>0':>8}{'거래t':>7}{'월t':>7}"
          f"{'상위20제외':>11}{'그때 월t':>9}{'패자':>7}{'승자':>7}")
    print('-' * 96)
    months = trades['entry_date'].dt.to_period('M')
    for name in EXIT_POLICIES:
        if name == BASELINE_POLICY:
            continue
        diff = trades[f'x_{name}'] - base
        trimmed = diff.sort_values(ascending=False).iloc[20:]
        print(f"{name:<17}{diff.mean():>+9.2f}{diff.median():>+10.2f}{(diff > 0).mean() * 100:>7.0f}%"
              f"{_t(diff):>+7.2f}{_t(diff.groupby(months).mean()):>+7.2f}"
              f"{trimmed.mean():>+11.2f}{_t(trimmed.groupby(months.loc[trimmed.index]).mean()):>+9.2f}"
              f"{diff[trades.bh_group == '패자'].mean():>+7.2f}{diff[trades.bh_group == '승자'].mean():>+7.2f}")
    print('-' * 96)
    print(f"다중비교 보정: 정책 {len(EXIT_POLICIES) - 1}개 동시 검정 -> Bonferroni 기준 |t| > 2.5")
    return reports


def main():
    parser = argparse.ArgumentParser(description='전략 엣지 진단 (초과수익 t검정)')
    parser.add_argument('--limit', type=int, default=None, help='유니버스 앞 N개만 분석')
    parser.add_argument('--period', default='2y', help="데이터 기간 (기본 '2y')")
    parser.add_argument('--holding', type=int, default=20, help='최대 보유 거래일 (기본 20)')
    parser.add_argument('--min-rr', type=float, default=1.20, help='진입 손익비 게이트')
    parser.add_argument('--csv', default=None, help='거래 단위 결과를 CSV로 저장할 경로')
    parser.add_argument('--exits', action='store_true', help='청산 정책 비교 및 견고성 검증 수행')
    parser.add_argument('--placebo', action='store_true',
                        help='무작위 진입 대조군과 비교 (패턴의 증분 정보 및 손절/목표 구조 효과 분해)')
    parser.add_argument('--universe', default='core',
                        help="'core'(기본) 또는 Symbol 열이 있는 CSV 경로 (과거 시점 구성종목 검증용)")
    args = parser.parse_args()

    universe, universe_label = load_universe(args.universe)
    universe = universe[:args.limit] if args.limit else universe
    print(f"유니버스: {universe_label}")
    print(f"엣지 진단 시작: 종목 {len(universe)}개 x 패턴 {len(PATTERNS)}종, "
          f"기간 {args.period}, 보유 {args.holding}일, RR 게이트 {args.min_rr}")

    placebo_rows = [] if args.placebo else None
    trades = collect_trades(
        universe, period=args.period, holding_days=args.holding, min_rr=args.min_rr,
        with_exits=args.exits, placebo_sink=placebo_rows
    )

    if trades.empty:
        print("\n독립 신호가 없습니다. 기간 또는 게이트 조건을 확인하세요.")
        return

    missing = trades.attrs.get('missing_tickers', [])
    if missing:
        print(f"\n시세 수집 실패 {len(missing)}종목 (상장폐지·피인수 등): {', '.join(missing)}")
        print("  -> 이 종목들은 분석에서 빠지므로 생존 편향이 일부 남는다.")

    n = len(trades)
    mean = trades.excess_return.mean()
    se = trades.excess_return.std(ddof=1) / np.sqrt(n)
    tstat = mean / se if se > 0 else float('nan')

    print(f"\n총 독립표본 {n}건 (종목 {trades.ticker.nunique()}개)")
    print(f"전체 초과수익 평균 {mean:+.3f}%p  t={tstat:+.2f}  "
          f"95%CI [{mean - 1.96 * se:+.2f}, {mean + 1.96 * se:+.2f}]  "
          f"-> {'통계적으로 유의' if abs(tstat) > 1.96 else '유의하지 않음'}")

    print_table('시장 레짐별 (진입 시점 기준)', summarize(trades, 'regime'))
    print_table('패턴별', summarize(trades, 'pattern'))

    trades['cell'] = trades.regime + ' / ' + trades.pattern
    print_table('레짐 x 패턴 (표본 15건 이상)', summarize(trades, 'cell', min_samples=15))

    if args.exits:
        print_exit_comparison(trades)

    if args.placebo and placebo_rows:
        print_placebo_comparison(trades, pd.DataFrame(placebo_rows))

    print("\n주의: 여러 그룹을 동시에 비교하면 우연히 t가 커지는 칸이 생긴다(다중비교).")
    print("      개별 칸의 t값만 보고 전략을 채택하지 말 것. 전체 t가 먼저 유의해야 한다.")

    if args.csv:
        trades.to_csv(args.csv, index=False, encoding='utf-8-sig')
        print(f"\n거래 단위 결과 저장: {args.csv}")


if __name__ == '__main__':
    main()
