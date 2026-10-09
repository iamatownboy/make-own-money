"""
scripts/etf_swing_check.py
패턴 구간 규칙이 1배 지수 ETF에서도 통하는지 보는 '빠른 점검' (정식 검증 아님).

스윙 기준(상승폭)을 바꿔가며 ETF 전체 과거 시세에 같은 규칙을 적용하고, 앱이 추천을 채점하는 방식 그대로
'1차 목표에 손절보다 먼저 닿은 비율'을 센다. 대조군은 같은 종목의 무작위 날짜 5개에 같은 모양의 가격표를 붙인 것.

한계: 대조군이 '같은 날짜의 다른 종목'이 아니라 같은 종목의 무작위 날짜라서 시장 국면이 맞춰져 있지 않고,
      QQQ·SMH·SOXX·XLK 처럼 서로 겹치는 ETF가 섞여 있어 표본이 보이는 것보다 적다. 방향만 보는 용도.

사용
----
    python scripts/etf_swing_check.py
    python scripts/etf_swing_check.py QQQ SPY GLD --swings 0.15 0.30
"""

import os
import sys
import argparse
import warnings

warnings.filterwarnings('ignore')

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import numpy as np

from quant_core.pattern_scanner import download_daily
from quant_core.pattern_setup import simulate, evaluate_recommendation, rule_levels

DEFAULT_ETFS = ['QQQ', 'SPY', 'SMH', 'SOXX', 'GLD', 'XLK', 'IWM', 'DIA']
PLACEBO_PER_TRADE = 5


def judge(df, date, L, H):
    rec = {'rec_date': str(date.date()), 'tf': '1d', 'L': L, 'H': H}
    return evaluate_recommendation(rec, df, None)['result']


def hit_rate(results):
    hit, stop = results.count('적중'), results.count('손절')
    return (hit / (hit + stop) * 100 if hit + stop else float('nan')), hit + stop


def check(full, swing):
    real, placebo, stop_dist, per_etf = [], [], [], {}
    for t, df in full.items():
        if df is None or len(df) < 500:
            continue
        trades, _ = simulate(df, swing, 120)
        rng = np.random.default_rng(1)
        n = 0
        for tr in trades:
            if not tr['closed'] or tr['entry_t'] < 5:
                continue
            p0 = float(df['Close'].iloc[tr['entry_t']])
            L, H = tr['L'], tr['H']
            real.append(judge(df, df.index[tr['entry_t']], L, H))
            n += 1
            stop_dist.append((L / min(p0, rule_levels(L, H)['entry1']) - 1) * 100)
            for j in rng.integers(260, len(df) - 130, PLACEBO_PER_TRADE):
                f = float(df['Close'].iloc[j]) / p0                  # 같은 비율의 가격표
                placebo.append(judge(df, df.index[j], L * f, H * f))
        per_etf[t] = n
    return real, placebo, stop_dist, per_etf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('etfs', nargs='*', default=DEFAULT_ETFS)
    ap.add_argument('--swings', nargs='+', type=float, default=[0.10, 0.15, 0.20, 0.30])
    a = ap.parse_args()
    full = download_daily(a.etfs, 'max', verbose=False)

    print(f"{'스윙':>5} {'거래':>5} {'실제 적중%':>10} {'무작위 적중%':>12} {'손절까지 평균%':>13}   종목별 거래 수")
    for sw in a.swings:
        real, placebo, stop_dist, per_etf = check(full, sw)
        (r, n), (p, _) = hit_rate(real), hit_rate(placebo)
        sd = np.mean(stop_dist) if stop_dist else float('nan')
        print(f"{sw:>5.2f} {n:>5} {r:>10.1f} {p:>12.1f} {sd:>13.1f}   {per_etf}")


if __name__ == '__main__':
    main()
