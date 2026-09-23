"""
quant_core/pattern_setup.py
피보나치 스윙 되돌림 매수 구간 — 규칙의 단일 기준(Single Source of Truth).

탐지(스캐너)와 채점(추천 기록 평가)이 모두 이 모듈의 함수를 쓴다.
규칙은 scripts/fib_strategy_backtest.py 로 검증한 것과 같다.
(tests/test_pattern_setup.py 가 두 구현의 진입 시점이 일치하는지 확인한다)

규칙
----
스윙      : 저점 L(=1) 이후 고점 H(=0). 상승폭이 시간봉별 기준 이상.
            그 시점까지의 봉만 사용. 되돌림이 매수 구간에 닿기 전에 조정 저점에서 다시 기준 이상
            상승하면 더 최근 스윙으로 교체.
매수 구간 : 0.786 ~ 0.886 (절반씩 분할)
손절      : 1(= L) 이탈
목표      : 1차 = 원래 피보나치 0.5
            2~4차 = 고점 H 와 매수 후 바닥 B 로 다시 그은 피보나치 0.5 / 0.786 / 1.31
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

ENTRY1, ENTRY2 = 0.786, 0.886
T1_ORIG = 0.5
REDRAW_RATIOS = (0.5, 0.786, 1.31)
EXIT_FRACS = (0.25, 0.25, 0.25, 0.25)

# 시간봉별 설정. 과거 통계는 docs/FIB_STRATEGY_STUDY.md (S&P 500 과거 구성종목, 2013~2026)
TIMEFRAMES: Dict[str, Dict[str, Any]] = {
    '1d': {
        'label': '일봉', 'swing': 0.30,
        'recent_bars': 10,          # 최근 N봉 안에 매수 구간에 들어온 것만 추천
        'max_bars': 120,            # 규칙상 최대 보유 (봉)
        'eval_days': 120,           # 채점 시 최대 추적 거래일
        'base_rate': {'hit': 46.0, 'random': 39.4, 'n': 1959},
    },
    '1w': {
        'label': '주봉', 'swing': 0.50,
        'recent_bars': 3,
        'max_bars': 52,
        'eval_days': 260,
        'base_rate': {'hit': 48.1, 'random': 38.0, 'n': 801},
    },
}
ZONE_TOLERANCE = 0.02      # 현재가가 0.786 가격보다 2% 이상 위로 반등했으면 '구간 이탈'
WATCH_RANGE = (0.70, ENTRY1)   # 아직 구간 전이지만 근접한 되돌림 비율


def to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """
    주봉 변환. 각 주의 날짜는 그 주의 '실제 마지막 거래일'로 붙인다.
    (금요일 라벨을 쓰면 진행 중인 주가 아직 오지 않은 날짜로 표시된다)
    """
    agg = {'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}
    w = df.resample('W-FRI').agg({k: v for k, v in agg.items() if k in df.columns})
    last_day = df.index.to_series().resample('W-FRI').last()
    w.index = pd.DatetimeIndex(last_day.reindex(w.index).values)
    return w.dropna(subset=['Open', 'High', 'Low', 'Close'])


def rule_levels(L: float, H: float, B: Optional[float] = None) -> Dict[str, float]:
    """스윙과 바닥으로 매수·손절·목표 가격을 계산한다. B 가 없으면 0.886 가격을 바닥으로 가정."""
    R = H - L
    z1, z2 = H - ENTRY1 * R, H - ENTRY2 * R
    b = z2 if B is None else B
    return {
        'entry1': z1, 'entry2': z2, 'stop': L,
        't1': L + T1_ORIG * R,
        't2': b + REDRAW_RATIOS[0] * (H - b),
        't3': b + REDRAW_RATIOS[1] * (H - b),
        't4': b + REDRAW_RATIOS[2] * (H - b),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 포지션 관리 (검증 엔진과 같은 순서·같은 보수적 가정)
# ─────────────────────────────────────────────────────────────────────────────
def _manage(o, h, l, c, t_start, L, H, z2, e2_open, B, max_bars):
    """
    t_start 봉부터 규칙대로 관리한다. 반환: (종료 봉, 도달한 목표 수, 손절 여부, 종료 여부, 0.886 체결 여부)
    종료 여부가 False 면 데이터 끝까지 포지션이 살아 있다는 뜻(진행 중).
    """
    n = len(c)
    R = H - L
    hit = 0
    e2_filled = not e2_open
    t_end = t_start + max_bars          # 검증 엔진과 같게: t_end 다음 봉 종가에서 기간 만료

    def tp(k, b):
        return L + T1_ORIG * R if k == 0 else b + REDRAW_RATIOS[k - 1] * (H - b)

    t = t_start
    while t < n:
        if t > t_end:
            return t, hit, False, True, e2_filled
        if not e2_filled and hit == 0 and l[t] <= z2 and not (o[t] <= L):
            e2_filled = True
        while hit < 4 and o[t] >= tp(hit, B):
            hit += 1
        if o[t] <= L or l[t] <= L:
            return t, hit, True, True, e2_filled
        while hit < 4 and h[t] >= tp(hit, B):
            hit += 1
        if hit >= 4:
            return t, hit, False, True, e2_filled
        B = min(B, l[t])
        t += 1
    return n - 1, hit, False, False, e2_filled


def simulate(df: pd.DataFrame, swing: float, max_bars: int):
    """
    시계열 전체에 규칙을 적용해 (모든 진입 목록, 마지막 스윙 상태)를 돌려준다.
    검증 엔진(scripts/fib_strategy_backtest.run_rules)과 같은 상태 기계.
    """
    o, h, l, c = (df[k].values.astype(float) for k in ('Open', 'High', 'Low', 'Close'))
    n = len(c)
    out: List[Dict[str, Any]] = []
    state, L, H, iL, iH, pull = 'seek', l[0], None, 0, None, np.inf
    t = 1
    while t < n:
        if state == 'seek':
            if l[t] < L:
                L, iL = l[t], t
            elif h[t] >= L * (1 + swing):
                state, H, iH, pull = 'armed', h[t], t, np.inf
            t += 1
            continue
        if h[t] > H:
            H, iH, pull = h[t], t, np.inf
            t += 1
            continue
        R = H - L
        z1, z2 = H - ENTRY1 * R, H - ENTRY2 * R
        if o[t] <= L:
            state, L, iL = 'seek', l[t], t
            t += 1
            continue
        if l[t] <= z1:
            e2_open = not (l[t] <= z2)
            rec = {'entry_t': t, 'L': L, 'H': H, 'iL': iL, 'iH': iH, 'z1': z1, 'z2': z2}
            if l[t] <= L:                                   # 진입 봉에서 바로 손절
                rec.update(exit_t=t, targets_hit=0, stopped=True, closed=True, e2_filled=True)
                out.append(rec)
                state, L, iL = 'seek', l[t], t
                t += 1
                continue
            if t + 1 >= n:                                  # 마지막 봉에서 막 진입
                rec.update(exit_t=t, targets_hit=0, stopped=False, closed=False, e2_filled=not e2_open)
                out.append(rec)
                break
            te, hit, stopped, closed, e2f = _manage(o, h, l, c, t + 1, L, H, z2, e2_open, l[t], max_bars)
            rec.update(exit_t=te, targets_hit=hit, stopped=stopped, closed=closed, e2_filled=e2f)
            out.append(rec)
            if not closed:
                break
            state, L, iL = 'seek', l[te], te
            t = te + 1
            continue
        pull = min(pull, l[t])
        if pull < H * (1 - swing / 2) and h[t] >= pull * (1 + swing) and pull > z1:
            iL = int(np.argmin(l[iH + 1:t + 1]) + iH + 1) if t > iH else t
            L, H, iH, pull = pull, h[t], t, np.inf
        t += 1
    return out, {'state': state, 'L': L, 'H': H, 'iL': iL, 'iH': iH}


def find_live_setup(df: pd.DataFrame, tf: str) -> Dict[str, Any]:
    """
    가장 최근 봉 기준으로
      - 'zone'  : 매수 구간에 들어와 있고, 아직 1차 목표·손절 전이며, 현재가가 구간 근처
      - 'watch' : 매수 구간 직전(되돌림 0.70~0.786)
      - None    : 해당 없음
    을 판단한다.
    """
    cfg = TIMEFRAMES[tf]
    if len(df) < 60:
        return {'status': None}
    trades, st = simulate(df, cfg['swing'], cfg['max_bars'])
    n = len(df)
    last_close = float(df['Close'].iloc[-1])
    idx = df.index

    if trades and not trades[-1]['closed']:
        tr = trades[-1]
        bars_since = n - 1 - tr['entry_t']
        if (tr['targets_hit'] == 0 and bars_since <= cfg['recent_bars']
                and last_close <= tr['z1'] * (1 + ZONE_TOLERANCE) and last_close > tr['L']):
            B = float(df['Low'].iloc[tr['entry_t']:].min())
            lv = rule_levels(tr['L'], tr['H'], B)
            R = tr['H'] - tr['L']
            return {
                'status': 'zone', 'tf': tf,
                'L': tr['L'], 'H': tr['H'],
                'L_date': str(idx[tr['iL']].date()), 'H_date': str(idx[tr['iH']].date()),
                'entry_date': str(idx[tr['entry_t']].date()),
                'bars_since_entry': int(bars_since),
                'retrace': (tr['H'] - last_close) / R,
                'swing_pct': tr['H'] / tr['L'] - 1,
                'price': last_close,
                'levels': lv,
            }

    if st['state'] == 'armed' and st['H'] is not None and st['H'] > st['L']:
        R = st['H'] - st['L']
        r = (st['H'] - last_close) / R
        if WATCH_RANGE[0] <= r < WATCH_RANGE[1]:
            return {
                'status': 'watch', 'tf': tf,
                'L': st['L'], 'H': st['H'],
                'L_date': str(idx[st['iL']].date()), 'H_date': str(idx[st['iH']].date()),
                'retrace': r, 'swing_pct': st['H'] / st['L'] - 1,
                'price': last_close,
                'levels': rule_levels(st['L'], st['H']),
                'distance_to_zone': last_close / (st['H'] - ENTRY1 * R) - 1,
            }
    return {'status': None}


# ─────────────────────────────────────────────────────────────────────────────
# 참고 근거 (보조 지표) — 점수에 넣지 않고 표시만 한다
# ─────────────────────────────────────────────────────────────────────────────
def supporting_signals(df_daily_ind: pd.DataFrame) -> List[str]:
    """
    일봉 지표가 계산된 데이터에서 '있으면 표시'할 보조 근거를 짧은 문구로 돌려준다.
    과거 검증에서 이 지표들은 확률을 유의하게 올리지 못했으므로 참고용이다.
    """
    from .indicators import (detect_bullish_divergence, detect_trendline_breakout,
                             detect_candlestick_reversal)
    out = []
    try:
        rsi = float(df_daily_ind['RSI_14'].iloc[-1])
        if rsi <= 30:
            out.append(f'RSI 과매도 {rsi:.0f}')
    except Exception:
        pass
    try:
        if detect_bullish_divergence(df_daily_ind).get('has_divergence'):
            out.append('RSI 상승 다이버전스')
    except Exception:
        pass
    try:
        if detect_trendline_breakout(df_daily_ind).get('is_breakout'):
            out.append('하락 빗각 돌파')
    except Exception:
        pass
    try:
        close = df_daily_ind['Close']
        if len(close) >= 200 and float(close.iloc[-1]) > float(close.tail(200).mean()):
            out.append('200일선 위')
    except Exception:
        pass
    try:
        if detect_candlestick_reversal(df_daily_ind).get('has_reversal_candle'):
            out.append('반전 캔들')
    except Exception:
        pass
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 추천 채점 — 추천일 이후만 본다 (전방 검증)
# ─────────────────────────────────────────────────────────────────────────────
def evaluate_recommendation(rec: Dict[str, Any], daily: pd.DataFrame,
                            bench: Optional[pd.Series] = None) -> Dict[str, Any]:
    """
    추천 기록을 추천일 다음 거래일부터 일봉으로 채점한다.
    가정: 추천일 종가(0.786 가격보다 높으면 0.786 가격)에 절반 매수, 0.886 에 닿으면 나머지 절반.
    판정: 1차 목표(원래 0.5)에 손절(1)보다 먼저 닿으면 '적중'.
    """
    rec_date = pd.Timestamp(rec['rec_date'])
    d = daily.loc[daily.index > rec_date]
    base = daily.loc[daily.index <= rec_date]
    if base.empty:
        return {'result': '데이터 없음'}
    L, H = float(rec['L']), float(rec['H'])
    lv = rule_levels(L, H)
    z1, z2 = lv['entry1'], lv['entry2']
    p0 = min(float(base['Close'].iloc[-1]), z1)
    if d.empty:
        return {'result': '진행 중', 'days': 0, 'targets_hit': 0}

    o, h, l, c = (d[k].values.astype(float) for k in ('Open', 'High', 'Low', 'Close'))
    max_days = TIMEFRAMES.get(rec.get('tf', '1d'), TIMEFRAMES['1d'])['eval_days']
    B = min(float(base['Low'].iloc[-1]), p0)
    units = 0.5 / p0
    cost = 0.5
    e2 = False
    hit = 0
    sold = 0.0
    total_units = units
    R = H - L
    first_event = None
    t_last = 0

    def tp(k, b):
        return L + T1_ORIG * R if k == 0 else b + REDRAW_RATIOS[k - 1] * (H - b)

    for t in range(min(len(c), max_days)):
        t_last = t
        if not e2 and hit == 0 and l[t] <= z2 and o[t] > L:
            px = min(o[t], z2)
            units += 0.5 / px; total_units += 0.5 / px; cost += 0.5; e2 = True
        while hit < 4 and o[t] >= tp(hit, B):
            q = total_units * EXIT_FRACS[hit]; sold += q * o[t]; units -= q; hit += 1
            first_event = first_event or ('hit', t)
        if units > 1e-12 and (o[t] <= L or l[t] <= L):
            px = o[t] if o[t] <= L else L
            sold += units * px; units = 0.0
            first_event = first_event or ('stop', t)
            break
        while hit < 4 and units > 1e-12 and h[t] >= tp(hit, B):
            px = tp(hit, B); q = min(total_units * EXIT_FRACS[hit], units)
            sold += q * px; units -= q; hit += 1
            first_event = first_event or ('hit', t)
        if units <= 1e-12:
            break
        B = min(B, l[t])

    finished = units <= 1e-12 or t_last + 1 >= max_days
    mark = float(c[t_last])
    value = sold + units * mark
    ret = value / cost - 1                                  # 투입 자금 대비
    if first_event is None:
        result = '만료' if t_last + 1 >= max_days else '진행 중'
    else:
        result = '적중' if first_event[0] == 'hit' else '손절'

    bench_ret = None
    if bench is not None:
        b = bench.dropna()
        a0 = b.loc[:rec_date]
        a1 = b.loc[:d.index[t_last]]
        if len(a0) and len(a1):
            bench_ret = float(a1.iloc[-1] / a0.iloc[-1] - 1)

    return {
        'result': result,
        'finished': bool(finished),
        'targets_hit': int(hit),
        'ret': float(ret),
        'bench_ret': bench_ret,
        'days': int(t_last + 1),
        'event_date': str(d.index[first_event[1]].date()) if first_event else None,
        'last_date': str(d.index[t_last].date()),
        'entry_price': float(cost / total_units),
    }
