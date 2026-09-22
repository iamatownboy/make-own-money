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


def load_benchmark_close(period: str) -> pd.Series:
    """레짐 판정용 벤치마크 종가 시계열."""
    bdf = yf.Ticker(BENCHMARK_TICKER).history(period=period, interval='1d')
    if bdf.empty:
        return None
    if bdf.index.tz is not None:
        bdf.index = bdf.index.tz_localize(None)
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


def collect_trades(tickers, period='2y', holding_days=20, min_rr=1.20,
                   fee_slippage_pct=0.25, verbose=True):
    """실전 규칙으로 과거 독립 신호를 재생성하고 거래 단위로 정산한다."""
    bench_series = get_benchmark_series(period=period)
    bench_close = load_benchmark_close(period)
    trades = []

    for idx, item in enumerate(tickers, start=1):
        ticker = item['ticker']
        try:
            df = yf.Ticker(ticker).history(period=period, interval='1d')
            if df.empty or len(df) < 60:
                continue
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            df = calculate_all_indicators(df)
        except Exception as exc:
            if verbose:
                print(f"  [{ticker}] 수집 실패: {exc}")
            continue

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

                trades.append({
                    'ticker': ticker,
                    'pattern': pattern,
                    'regime': regime,
                    'entry_date': entry_date,
                    'held_days': outcome['exit_offset'],
                    'net_return': outcome['net_return'],
                    'benchmark_return': bench_ret,
                    'excess_return': outcome['net_return'] - bench_ret,
                    'outcome': outcome['outcome'],
                })

        if verbose and idx % 10 == 0:
            print(f"  ... {idx}/{len(tickers)} 종목 처리 (누적 거래 {len(trades)}건)")

    return pd.DataFrame(trades)


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


def main():
    parser = argparse.ArgumentParser(description='전략 엣지 진단 (초과수익 t검정)')
    parser.add_argument('--limit', type=int, default=None, help='유니버스 앞 N개만 분석')
    parser.add_argument('--period', default='2y', help="데이터 기간 (기본 '2y')")
    parser.add_argument('--holding', type=int, default=20, help='최대 보유 거래일 (기본 20)')
    parser.add_argument('--min-rr', type=float, default=1.20, help='진입 손익비 게이트')
    parser.add_argument('--csv', default=None, help='거래 단위 결과를 CSV로 저장할 경로')
    args = parser.parse_args()

    universe = CORE_UNIVERSE[:args.limit] if args.limit else CORE_UNIVERSE
    print(f"엣지 진단 시작: 종목 {len(universe)}개 x 패턴 {len(PATTERNS)}종, "
          f"기간 {args.period}, 보유 {args.holding}일, RR 게이트 {args.min_rr}")

    trades = collect_trades(
        universe, period=args.period, holding_days=args.holding, min_rr=args.min_rr
    )

    if trades.empty:
        print("\n독립 신호가 없습니다. 기간 또는 게이트 조건을 확인하세요.")
        return

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

    print("\n주의: 여러 그룹을 동시에 비교하면 우연히 t가 커지는 칸이 생긴다(다중비교).")
    print("      개별 칸의 t값만 보고 전략을 채택하지 말 것. 전체 t가 먼저 유의해야 한다.")

    if args.csv:
        trades.to_csv(args.csv, index=False, encoding='utf-8-sig')
        print(f"\n거래 단위 결과 저장: {args.csv}")


if __name__ == '__main__':
    main()
