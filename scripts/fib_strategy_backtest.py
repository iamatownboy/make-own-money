"""
scripts/fib_strategy_backtest.py
사용자가 쓰는 피보나치 스윙 매매 규칙(매수 쪽)을 봉 단위로 그대로 재현해 검증한다.

규칙 (사용자 설명 + 참고 글 기준)
--------------------------------
스윙      : 저점 L(=1) 이후 고점 H(=0)가 형성되고 하락. 상승폭이 최소 기준(--swing) 이상.
            그 시점까지의 봉만 사용한다(미래 정보 없음). 되돌림이 매수 구간에 닿기 전에
            조정 저점에서 다시 기준 이상 상승하면 더 최근 스윙(조정 저점 -> 새 고점)으로 교체.
매수      : 0.786 에 절반, 0.886 에 절반 분할 지정가 매수. (0.886 은 1차 익절 전까지만 유효)
손절      : 1(=원래 저점 L) 이탈 시 잔량 전부 손절. 기본은 장중 터치, --stop-close 면 종가 이탈.
익절      : 체결 수량의 25%씩 네 단계
            T1 = 원래 피보나치 0.5            (L + 0.5 x (H - L))
            T2 = 고점 -> 매수 후 바닥 B 로 다시 그은 피보나치 0.5   (B + 0.5 x (H - B))
            T3 = 같은 피보나치 0.786                              (B + 0.786 x (H - B))
            T4 = 같은 피보나치 1.31 (고점 위 오버슈팅)             (B + 1.31 x (H - B))
            B 는 '직전 봉까지'의 최저가로 계산(보수적).
기간 만료 : --max-bars 봉이 지나면 잔량을 종가로 청산.
체결      : 시가가 이미 가격을 넘겨 출발하면 시가 체결(갭 반영). 한 봉 안에서 손절과 익절이
            모두 가능하면 손절을 먼저 처리(보수적). 수수료는 매수·매도 각각 --fee.

수익률 정의
-----------
- ret      : '계획한 전체 자금(1.0)' 대비 수익률. 0.786 만 체결되면 자금의 절반만 투입된 것.
- R        : 손절까지의 위험 대비 몇 배를 벌었나 (R = 손익 / (평균 매수가 - L) x 수량).
- excess   : 같은 보유 기간 벤치마크(코인=BTC, 주식=SPY)를 투입 자금만큼 샀을 때 대비 초과.

위약 대조
---------
실제 거래 1건마다 같은 종목의 무작위 봉 5개를 골라, 그 봉 종가를 0.786 체결가로 삼아
실제 거래와 '같은 비율의 가격표'(0.886 추가 매수, L 손절, T1~T4 익절)를 붙여 똑같이 관리한다.
차이 = 피보나치 스윙 자리에서 샀다는 것의 순수한 효과.

사용
----
    python scripts/fib_strategy_backtest.py --market crypto --tf 4h --swing 0.30
    python scripts/fib_strategy_backtest.py --market crypto --tf 1d --swing 0.50
    python scripts/fib_strategy_backtest.py --market stocks --tf 1d --swing 0.20
    python scripts/fib_strategy_backtest.py --market stocks --tf 1w --swing 0.30
"""

import os
import sys
import pickle
import argparse
import warnings

warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'data', 'cache')

ENTRY1, ENTRY2 = 0.786, 0.886
T1_ORIG = 0.5
T2_R, T3_R, T4_R = 0.5, 0.786, 1.31
EXIT_FRACS = (0.25, 0.25, 0.25, 0.25)


# ─────────────────────────────────────────────────────────────────────────────
# 데이터
# ─────────────────────────────────────────────────────────────────────────────
MEMBERSHIP_PATH = os.path.join(ROOT, 'data', 'universes', 'sp500_monthly_membership.csv.gz')


def split_on_gaps(df: pd.DataFrame, max_gap_days: float):
    """
    시세가 오래 끊긴 지점에서 자산을 나눈다. 같은 이름으로 재상장된 다른 자산(예: LUNA 2.0)이나
    거래 정지 전후를 하나로 이어 붙이면 가짜 급등락이 생기기 때문이다.
    """
    gaps = df.index.to_series().diff() > pd.Timedelta(days=max_gap_days)
    seg_id = gaps.cumsum()
    return [g for _, g in df.groupby(seg_id)]


def apply_pit_membership(data: dict) -> dict:
    """
    주식: 각 티커를 실제 S&P 500 구성종목이던 기간에만 매수할 수 있게 한다('member' 열).
    구성종목에서 빠진 뒤 6개월 이후의 시세는 버린다. 사라진 회사의 티커를 다른 회사가 재사용하면
    yfinance 가 엉뚱한 종목의 시세를 돌려주는 문제(예: SBNY, STI)를 차단한다.
    """
    m = pd.read_csv(MEMBERSHIP_PATH)
    m['month_end'] = pd.to_datetime(m['month_end'])
    months = m.groupby('ticker')['month_end'].apply(lambda s: set(s.dt.to_period('M')))
    last = m.groupby('ticker')['month_end'].max()
    out = {}
    for k, v in data.items():
        if k in ('SPY', 'QQQ', 'RSP'):
            out[k] = v
            continue
        if k not in months.index:
            continue
        v = v.loc[:last[k] + pd.Timedelta(days=183)].copy()
        v['member'] = v.index.to_period('M').isin(months[k])
        out[k] = v
    return out


def load_market(market: str, tf: str):
    if market == 'crypto':
        iv = {'4h': '4h', '1d': '1d', '1w': '1d'}[tf]
        data = pickle.load(open(os.path.join(CACHE, f'binance_{iv}.pkl'), 'rb'))
        bench_key = 'BTCUSDT'
    else:
        data = pickle.load(open(os.path.join(CACHE, 'sp500_ohlc.pkl'), 'rb'))
        bench_key = 'SPY'
    data = {k: v for k, v in data.items() if v is not None and len(v) > 0}
    if market == 'stocks':
        data = apply_pit_membership(data)
    if tf == '1w':
        rule = 'W-SUN' if market == 'crypto' else 'W-FRI'
        agg = {'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum', 'member': 'max'}
        data = {k: v.resample(rule).agg({c: f for c, f in agg.items() if c in v.columns})
                     .dropna(subset=['Open', 'High', 'Low', 'Close'])
                for k, v in data.items()}
    bench = data.get(bench_key)
    max_gap = 10 if market == 'stocks' else 4
    if tf == '1w':
        max_gap = 21
    assets = {}
    for k, v in data.items():
        if k in ('SPY', 'QQQ', 'RSP'):
            continue
        segs = split_on_gaps(v, max_gap)
        for j, seg in enumerate(segs):
            if len(seg) >= 120:
                assets[k if len(segs) == 1 else f'{k}#{j}'] = seg
    return assets, bench, bench_key


# ─────────────────────────────────────────────────────────────────────────────
# 포지션 관리 (실제·위약 공용)
# ─────────────────────────────────────────────────────────────────────────────
HOLD_ONLY = False   # --hold-only: 손절·익절 없이 max_bars 보유 (매도 방식의 효과를 분리하는 대조 실험)


def manage_position(o, h, l, c, t_start, pos, max_bars, fee, stop_close=False):
    """
    t_start 봉부터 포지션을 관리한다. pos 에는 L, H, z2, units, cost, filled, e2_open, B 가 들어 있다.
    반환: (종료 봉 인덱스, 결과 dict)
    """
    L, H = pos['L'], pos['H']
    R = H - L
    targets_hit = 0
    sold_value = 0.0
    fees = pos['cost'] * fee
    units_left = pos['units']
    units_total = pos['units']
    n = len(c)
    t_end = min(n - 1, t_start + max_bars)
    outcome = 'timeout'

    def target_price(k, B):
        if HOLD_ONLY:
            return np.inf
        if k == 0:
            return L + T1_ORIG * R
        r = (T2_R, T3_R, T4_R)[k - 1]
        return B + r * (H - B)

    t = t_start
    while t <= t_end:
        # 0.886 추가 매수 (1차 익절 전까지)
        if pos['e2_open'] and targets_hit == 0:
            z2 = pos['z2']
            if l[t] <= z2 and not (o[t] <= L):
                px = min(o[t], z2)
                u = 0.5 / px
                units_left += u; units_total += u
                pos['cost'] += 0.5; fees += 0.5 * fee
                pos['filled'] += 0.5
                pos['e2_open'] = False

        B = pos['B']
        # 시가 갭: 익절가 위에서 출발
        while targets_hit < 4 and o[t] >= target_price(targets_hit, B):
            q = units_total * EXIT_FRACS[targets_hit]
            sold_value += q * o[t]; fees += q * o[t] * fee
            units_left -= q; targets_hit += 1
        # 손절
        stop_px = None
        if units_left > 1e-12 and not HOLD_ONLY:
            if stop_close:
                if c[t] < L:
                    stop_px = c[t]
            else:
                if o[t] <= L:
                    stop_px = o[t]
                elif l[t] <= L:
                    stop_px = L
        if stop_px is not None:
            sold_value += units_left * stop_px; fees += units_left * stop_px * fee
            units_left = 0.0
            outcome = 'stop'
            break
        # 장중 익절
        while targets_hit < 4 and units_left > 1e-12 and h[t] >= target_price(targets_hit, B):
            px = target_price(targets_hit, B)
            q = units_total * EXIT_FRACS[targets_hit]
            q = min(q, units_left)
            sold_value += q * px; fees += q * px * fee
            units_left -= q; targets_hit += 1
        if units_left <= 1e-12 or targets_hit >= 4:
            outcome = 'all_targets'
            break
        pos['B'] = min(pos['B'], l[t])
        t += 1

    if units_left > 1e-12:
        t = min(t, n - 1)
        sold_value += units_left * c[t]; fees += units_left * c[t] * fee
        units_left = 0.0
    t = min(t, n - 1)

    cost = pos['cost']
    pnl = sold_value - cost - fees
    avg_entry = cost / units_total
    risk = units_total * (avg_entry - L)
    return t, {
        'ret': pnl,                                   # 계획 자금 1.0 대비
        'R': pnl / risk if risk > 0 else np.nan,
        'filled': pos['filled'],
        'targets_hit': targets_hit,
        't1': targets_hit >= 1,
        'stopped': outcome == 'stop',
        'outcome': outcome,
        'bars': t - t_start + 1,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 실제 규칙: 스윙 탐지 + 진입
# ─────────────────────────────────────────────────────────────────────────────
def run_rules(df: pd.DataFrame, swing: float, max_bars: int, fee: float, stop_close=False):
    o, h, l, c = (df[k].values.astype(float) for k in ('Open', 'High', 'Low', 'Close'))
    member = df['member'].values.astype(bool) if 'member' in df.columns else np.ones(len(df), bool)
    n = len(c)
    trades = []
    state = 'seek'
    L = l[0]
    H = None
    pull = np.inf
    t = 1
    while t < n - 1:
        if state == 'seek':
            if l[t] < L:
                L = l[t]
            elif h[t] >= L * (1 + swing):
                state, H, pull = 'armed', h[t], np.inf
            t += 1
            continue

        # armed
        if h[t] > H:
            H, pull = h[t], np.inf      # 고점 갱신 봉에서는 진입 판정하지 않음
            t += 1
            continue
        R = H - L
        z1, z2 = H - ENTRY1 * R, H - ENTRY2 * R
        if o[t] <= L:                   # 매수 구간을 건너뛰고 저점 아래로 갭 -> 스윙 무효
            state, L = 'seek', l[t]
            t += 1
            continue
        if l[t] <= z1 and member[t]:
            px = min(o[t], z1)
            pos = {'L': L, 'H': H, 'z2': z2, 'units': 0.5 / px, 'cost': 0.5,
                   'filled': 0.5, 'e2_open': True, 'B': l[t]}
            # 같은 봉에서 0.886 체결 가능
            if l[t] <= z2:
                px2 = min(o[t], z2)
                pos['units'] += 0.5 / px2; pos['cost'] += 0.5; pos['filled'] = 1.0
                pos['e2_open'] = False
            entry_t = t
            # 진입 봉에서 바로 손절선까지 밀리면 손절 (보수적)
            if not HOLD_ONLY and ((not stop_close and l[t] <= L) or (stop_close and c[t] < L)):
                exit_px = L if not stop_close else c[t]
                pnl = pos['units'] * exit_px - pos['cost'] - (pos['cost'] + pos['units'] * exit_px) * fee
                avg = pos['cost'] / pos['units']
                trades.append({'entry_t': entry_t, 'exit_t': t, 'L': L, 'H': H, 'z1': z1,
                               'ret': pnl, 'R': pnl / (pos['units'] * (avg - L)),
                               'filled': pos['filled'], 'targets_hit': 0, 't1': False,
                               'stopped': True, 'outcome': 'stop', 'bars': 1})
                state, L = 'seek', l[t]
                t += 1
                continue
            t_exit, res = manage_position(o, h, l, c, t + 1, pos, max_bars, fee, stop_close)
            res.update({'entry_t': entry_t, 'exit_t': t_exit, 'L': L, 'H': H, 'z1': z1})
            trades.append(res)
            state, L = 'seek', l[t_exit]
            t = t_exit + 1
            continue
        # 매수 구간에 닿기 전 조정 저점에서 다시 스윙 기준 이상 상승 -> 최근 스윙으로 교체
        pull = min(pull, l[t])
        if pull < H * (1 - swing / 2) and h[t] >= pull * (1 + swing) and pull > z1:
            L, H, pull = pull, h[t], np.inf
        t += 1
    return trades


def run_placebo(df, trades, max_bars, fee, draws, rng, stop_close=False):
    """
    실제 거래와 같은 비율의 가격표를 무작위 봉에 붙여 똑같이 관리한다.
    진입도 실제와 같은 '지정가' 방식: 무작위 봉 t0 의 종가에 매수 지정가를 걸고,
    다음 봉 저가가 그 가격에 닿으면 체결(시가가 더 낮으면 시가). 닿지 않으면 다른 봉을 다시 뽑는다.
    (종가 매수로 두면 장중 눌림에 지정가로 사는 실제 규칙이 구조적으로 유리해진다)
    """
    o, h, l, c = (df[k].values.astype(float) for k in ('Open', 'High', 'Low', 'Close'))
    member = df['member'].values.astype(bool) if 'member' in df.columns else np.ones(len(df), bool)
    n = len(c)
    out = []
    lo, hi = 60, n - 3
    if hi <= lo:
        return out
    for tr in trades:
        rel_R = (tr['H'] - tr['L']) / tr['z1']       # 스윙 폭 / 0.786 가격
        res_list = []
        tries = 0
        while len(res_list) < draws and tries < draws * 50:
            tries += 1
            t0 = int(rng.integers(lo, hi))
            limit = c[t0]
            t1 = t0 + 1
            if not member[t1] or l[t1] > limit:
                continue                      # 지정가 미체결 -> 다시 뽑기
            z1 = limit
            px = min(o[t1], limit)
            R = rel_R * z1
            H = z1 + ENTRY1 * R
            L = H - R
            z2 = H - ENTRY2 * R
            pos = {'L': L, 'H': H, 'z2': z2, 'units': 0.5 / px, 'cost': 0.5,
                   'filled': 0.5, 'e2_open': True, 'B': l[t1]}
            if l[t1] <= z2:
                pos['units'] += 0.5 / min(o[t1], z2); pos['cost'] += 0.5
                pos['filled'] = 1.0; pos['e2_open'] = False
            if not HOLD_ONLY and ((not stop_close and l[t1] <= L) or (stop_close and c[t1] < L)):
                exit_px = L if not stop_close else c[t1]
                pnl = pos['units'] * exit_px - pos['cost'] - (pos['cost'] + pos['units'] * exit_px) * fee
                res = {'ret': pnl, 'R': -1.0, 'filled': pos['filled'], 'targets_hit': 0, 't1': False,
                       'stopped': True, 'outcome': 'stop', 'bars': 1, 'entry_t': t1, 'exit_t': t1}
            else:
                t_exit, res = manage_position(o, h, l, c, t1 + 1, pos, max_bars, fee, stop_close)
                res['entry_t'], res['exit_t'] = t1, t_exit
            res_list.append(res)
        out.append(res_list)
    return out


def bench_return(bench: pd.DataFrame, t_in, t_out):
    if bench is None:
        return np.nan
    s = bench['Close']
    a = s.loc[:t_in]
    b = s.loc[:t_out]
    if a.empty or b.empty:
        return np.nan
    return b.iloc[-1] / a.iloc[-1] - 1


# ─────────────────────────────────────────────────────────────────────────────
# 리포트
# ─────────────────────────────────────────────────────────────────────────────
def summarize(df_: pd.DataFrame, label: str):
    r = df_['ret']
    wins = r[r > 0].sum(); losses = -r[r < 0].sum()
    print(f"  {label:<10}{len(df_):>7,}건  승률 {(r > 0).mean() * 100:5.1f}%  "
          f"1차익절 도달 {df_['t1'].mean() * 100:5.1f}%  손절 {df_['stopped'].mean() * 100:5.1f}%  "
          f"평균 {r.mean() * 100:+6.2f}%  중앙값 {r.median() * 100:+6.2f}%  "
          f"평균 {df_['R'].mean():+5.2f}R  손익비율(PF) {wins / losses if losses > 0 else np.nan:4.2f}  "
          f"보유 {df_['bars'].mean():5.1f}봉")


def cluster_bootstrap(diff: pd.Series, groups: pd.Series, n_boot=2000, seed=5):
    rng = np.random.default_rng(seed)
    g = pd.DataFrame({'d': diff.values, 'g': groups.values}).groupby('g')['d'].agg(['sum', 'count'])
    arr = g.values
    boots = []
    for _ in range(n_boot):
        s = arr[rng.integers(0, len(arr), len(arr))].sum(axis=0)
        boots.append(s[0] / s[1])
    return arr[:, 0].sum() / arr[:, 1].sum(), np.percentile(boots, 2.5), np.percentile(boots, 97.5)


def main():
    ap = argparse.ArgumentParser(description='피보나치 스윙 매매 규칙 백테스트')
    ap.add_argument('--market', choices=['crypto', 'stocks'], default='crypto')
    ap.add_argument('--tf', choices=['4h', '1d', '1w'], default='4h')
    ap.add_argument('--swing', type=float, default=0.30, help='스윙 최소 상승폭 (0.30 = +30%%)')
    ap.add_argument('--max-bars', type=int, default=None, help='최대 보유 봉 수')
    ap.add_argument('--fee', type=float, default=None, help='편도 수수료+슬리피지')
    ap.add_argument('--stop-close', action='store_true', help='손절을 종가 기준으로 판정')
    ap.add_argument('--draws', type=int, default=5)
    ap.add_argument('--hold-only', action='store_true',
                    help='대조 실험: 같은 자리에서 사되 손절·익절 없이 --max-bars 동안 보유')
    ap.add_argument('--entry', type=float, nargs=2, default=None, metavar=('E1', 'E2'),
                    help='대조 실험용 매수 비율 (기본 0.786 0.886). 예: --entry 0.70 0.80')
    ap.add_argument('--csv', default=None)
    args = ap.parse_args()

    global ENTRY1, ENTRY2, HOLD_ONLY
    HOLD_ONLY = args.hold_only
    if args.entry:
        ENTRY1, ENTRY2 = args.entry
    max_bars = args.max_bars or {'4h': 180, '1d': 120, '1w': 52}[args.tf]
    fee = args.fee if args.fee is not None else (0.001 if args.market == 'crypto' else 0.0005)

    assets, bench, bench_key = load_market(args.market, args.tf)
    rng = np.random.default_rng(42)
    rows, prows = [], []
    for name, df in assets.items():
        trades = run_rules(df, args.swing, max_bars, fee, args.stop_close)
        if not trades:
            continue
        plc = run_placebo(df, trades, max_bars, fee, args.draws, rng, args.stop_close)
        idx = df.index
        for tr, pl in zip(trades, plc):
            t_in, t_out = idx[tr['entry_t']], idx[tr['exit_t']]
            b = bench_return(bench, t_in, t_out)
            rows.append({**{k: tr[k] for k in ('ret', 'R', 'filled', 'targets_hit', 't1', 'stopped', 'outcome', 'bars')},
                         'asset': name, 'entry': t_in, 'exit': t_out,
                         'swing_pct': tr['H'] / tr['L'] - 1,
                         'excess': tr['ret'] - tr['filled'] * b if b == b else np.nan,
                         'placebo_ret': np.mean([p['ret'] for p in pl]),
                         'placebo_t1': np.mean([p['t1'] for p in pl])})
            for p in pl:
                prows.append({**{k: p[k] for k in ('ret', 'R', 't1', 'stopped', 'bars', 'filled')}, 'asset': name,
                              'entry': idx[p['entry_t']], 'rel_R': (tr['H'] - tr['L']) / tr['z1']})

    tr = pd.DataFrame(rows)
    pl = pd.DataFrame(prows)
    if tr.empty:
        print('거래 없음'); return

    print(f"\n{'=' * 100}")
    print(f"피보나치 스윙 매매 (매수 {ENTRY1}/{ENTRY2}) | {args.market} {args.tf} | 스윙 +{args.swing * 100:.0f}% 이상 | "
          f"손절 {'종가' if args.stop_close else '장중 터치'} | 최대 {max_bars}봉 | 편도 비용 {fee * 100:.2f}%")
    print(f"종목 {tr.asset.nunique()}개 / 전체 {len(assets)}개, 기간 {tr.entry.min():%Y-%m} ~ {tr.entry.max():%Y-%m}, "
          f"스윙 크기 중앙값 +{tr.swing_pct.median() * 100:.0f}%")
    print('=' * 100)
    print("■ 1. 성과 (수익률은 계획 자금 전체 대비, 0.786만 체결되면 절반만 투입)")
    summarize(tr, '실제 규칙')
    summarize(pl, '위약')
    print(f"  0.886까지 체결된 비율 {(tr.filled == 1).mean() * 100:.0f}%, "
          f"익절 단계 도달: T1 {(tr.targets_hit >= 1).mean() * 100:.0f}% · T2 {(tr.targets_hit >= 2).mean() * 100:.0f}% · "
          f"T3 {(tr.targets_hit >= 3).mean() * 100:.0f}% · T4(1.31) {(tr.targets_hit >= 4).mean() * 100:.0f}%")

    print("\n■ 2. 1차 익절(0.5)에 손절(1)보다 먼저 닿을 확률")
    print(f"  실제 규칙 {tr.t1.mean() * 100:.1f}%   vs   위약(같은 가격표, 무작위 날짜) {pl.t1.mean() * 100:.1f}%")
    print("  (이론값 33%는 0.886까지 모두 체결된 경우에만 해당. 0.786만 체결되면 약 43%가 되므로 위약을 기준선으로 쓴다)")

    diff = tr.ret - tr.placebo_ret
    d, lo, hi = cluster_bootstrap(diff, tr.asset)
    month = tr.entry.dt.to_period('M').astype(str)
    dm, lom, him = cluster_bootstrap(diff, month)
    print("\n■ 3. 핵심 판정 — 실제 규칙이 같은 가격표를 무작위로 붙인 매매보다 나은가")
    print(f"  거래당 수익 차이 {d * 100:+.2f}%p")
    print(f"    종목 단위로 묶은 95% 구간 {lo * 100:+.2f} ~ {hi * 100:+.2f}%p")
    print(f"    진입 월 단위로 묶은 95% 구간 {lom * 100:+.2f} ~ {him * 100:+.2f}%p "
          f"(같은 달 동시 신호 = 사실상 한 사건, {month.nunique()}개월)")
    lo_c, hi_c = max(lo, lom), min(hi, him)
    verdict = ('피보나치 자리에 우위 있음' if lo > 0 and lom > 0 else
               ('오히려 불리' if hi < 0 and him < 0 else '무작위와 구별되지 않음 (두 묶음 방식 모두 통과해야 인정)'))
    print(f"  => {verdict}")
    d1, lo1, hi1 = cluster_bootstrap(tr.t1.astype(float) - tr.placebo_t1, tr.asset)
    print(f"  1차 익절 도달 확률 차이 {d1 * 100:+.1f}%p  (95% 구간 {lo1 * 100:+.1f} ~ {hi1 * 100:+.1f}%p)")

    ex = tr.excess.dropna()
    de, loe, hie = cluster_bootstrap(ex, tr.loc[ex.index, 'asset'])
    print(f"\n■ 4. 벤치마크({bench_key}) 대비 — 같은 기간 같은 금액을 {bench_key}에 넣었을 때보다")
    print(f"  거래당 초과 {de * 100:+.2f}%p  (95% 구간 {loe * 100:+.2f} ~ {hie * 100:+.2f}%p)")

    print("\n■ 5. 시기별 (실제 − 위약, 거래당)")
    tr['year'] = tr.entry.dt.year
    parts = []
    for y, g in tr.groupby('year'):
        parts.append(f"{y}:{(g.ret - g.placebo_ret).mean() * 100:+.1f}({len(g)})")
    print('  ' + '  '.join(parts))
    half = tr.entry.sort_values().iloc[len(tr) // 2]
    a, b_ = tr[tr.entry < half], tr[tr.entry >= half]
    print(f"  전반 {(a.ret - a.placebo_ret).mean() * 100:+.2f}%p ({len(a)}건)   "
          f"후반 {(b_.ret - b_.placebo_ret).mean() * 100:+.2f}%p ({len(b_)}건)")

    if args.csv:
        tr.to_csv(args.csv, index=False)
        pl.to_csv(args.csv.replace('.csv', '_placebo.csv'), index=False)


if __name__ == '__main__':
    main()
