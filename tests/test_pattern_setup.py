"""
패턴 구간 추천의 규칙·탐지·채점 테스트.
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quant_core.pattern_setup import (simulate, find_live_setup, evaluate_recommendation,
                                      rule_levels, to_weekly, ENTRY1, ENTRY2)
from quant_core.pattern_scanner import parse_nasdaq_listed, history_summary


def _bars(rows, start='2025-01-01'):
    a = np.array(rows, float)
    return pd.DataFrame(a, columns=['Open', 'High', 'Low', 'Close'],
                        index=pd.bdate_range(start, periods=len(a)))


def _swing_then_pullback(pullback_close):
    """저점 100 부근 -> 고점 200 -> 되돌림. 60봉 이상이 되도록 앞부분을 채운다."""
    rows = [(101, 102, 100, 101)] * 50                                   # 저점 100
    rows += [(100 + i * 10, 110 + i * 10, 99 + i * 10, 110 + i * 10) for i in range(10)]  # -> 200
    rows += [(200, 200, 185, 186), (186, 187, 160, 161), (161, 162, 140, 141)]
    p = pullback_close
    rows += [(141, 142, p - 1, p)]
    return _bars(rows)


# 1. 추천 규칙 = 검증 엔진 규칙 (진입 시점 완전 일치)
def test_simulate_matches_research_engine():
    from scripts.fib_strategy_backtest import run_rules
    total = 0
    for seed in range(40):
        r = np.random.default_rng(seed)
        n = 1200
        c = 100 * np.exp(np.cumsum(r.normal(0.0005, 0.03, n)))
        o = c * np.exp(r.normal(0, 0.01, n))
        h = np.maximum(o, c) * np.exp(np.abs(r.normal(0, 0.015, n)))
        l = np.minimum(o, c) * np.exp(-np.abs(r.normal(0, 0.015, n)))
        df = pd.DataFrame({'Open': o, 'High': h, 'Low': l, 'Close': c},
                          index=pd.bdate_range('2015-01-01', periods=n))
        a = [(t['entry_t'], round(t['L'], 6), round(t['H'], 6)) for t in run_rules(df, 0.30, 120, 0.0)]
        b = [(t['entry_t'], round(t['L'], 6), round(t['H'], 6)) for t in simulate(df, 0.30, 120)[0]]
        assert a == b
        total += len(a)
    assert total > 100


# 2. 매수 구간 탐지
def test_find_live_setup_detects_zone():
    df = _swing_then_pullback(120)     # 저점 100, 고점 200 -> 0.786 = 121.4
    res = find_live_setup(df, '1d')
    assert res['status'] == 'zone'
    assert res['L'] == pytest.approx(99.0)   # 상승 첫 봉 저가 99
    assert res['H'] == pytest.approx(200.0)
    lv = res['levels']
    R = 200 - 99
    assert lv['entry1'] == pytest.approx(200 - ENTRY1 * R)
    assert lv['entry2'] == pytest.approx(200 - ENTRY2 * R)
    assert lv['stop'] == pytest.approx(99.0)
    assert lv['t1'] == pytest.approx(99 + 0.5 * R)


# 3. 구간 직전은 '근접', 너무 얕으면 해당 없음
def test_find_live_setup_watch_and_none():
    watch = _swing_then_pullback(127)   # 되돌림 약 0.72
    assert find_live_setup(watch, '1d')['status'] == 'watch'
    shallow = _swing_then_pullback(170)
    assert find_live_setup(shallow, '1d')['status'] is None


# 4. 채점: 1차 목표 먼저 -> 적중
def test_evaluate_hit():
    df = _swing_then_pullback(120)
    rec = {'rec_date': str(df.index[-1].date()), 'tf': '1d', 'L': 99.0, 'H': 200.0}
    t1 = 99 + 0.5 * 101
    extra = _bars([(121, 130, 118, 129), (129, t1 + 1, 128, t1)], start=str((df.index[-1] + pd.Timedelta(days=1)).date()))
    ev = evaluate_recommendation(rec, pd.concat([df, extra]))
    assert ev['result'] == '적중'
    assert ev['targets_hit'] >= 1


# 5. 채점: 손절 먼저 -> 손절, 갭하락은 시가 체결
def test_evaluate_stop_with_gap():
    df = _swing_then_pullback(120)
    rec = {'rec_date': str(df.index[-1].date()), 'tf': '1d', 'L': 99.0, 'H': 200.0}
    extra = _bars([(90, 92, 88, 89)], start=str((df.index[-1] + pd.Timedelta(days=1)).date()))
    ev = evaluate_recommendation(rec, pd.concat([df, extra]))
    assert ev['result'] == '손절'
    # 추천일 종가 120에 절반 매수. 다음 날 시가 90이 이미 손절선(99) 아래라
    # 0.886 추가 매수 없이 시가 90에 전량 손절 (검증 엔진과 같은 규칙)
    assert ev['entry_price'] == pytest.approx(120.0)
    assert ev['ret'] == pytest.approx(90 / 120 - 1, abs=1e-9)


# 6. 채점: 추천일 이후 데이터가 없으면 진행 중
def test_evaluate_open():
    df = _swing_then_pullback(120)
    rec = {'rec_date': str(df.index[-1].date()), 'tf': '1d', 'L': 99.0, 'H': 200.0}
    assert evaluate_recommendation(rec, df)['result'] == '진행 중'


# 7. 주봉 날짜는 실제 마지막 거래일 (미래 날짜 금지)
def test_weekly_labels_are_actual_last_trading_day():
    idx = pd.bdate_range('2026-09-01', '2026-09-22')     # 9/22 화요일에서 끝
    df = pd.DataFrame({'Open': 1.0, 'High': 1.0, 'Low': 1.0, 'Close': 1.0, 'Volume': 1}, index=idx)
    w = to_weekly(df)
    assert w.index.max() == pd.Timestamp('2026-09-22')
    assert all(d in idx for d in w.index)


# 8. 유니버스 필터
def test_parse_nasdaq_listed_filters_non_common():
    raw = ("Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
           "AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N\n"
           "QQQ|Invesco QQQ Trust|G|N|N|100|Y|N\n"
           "ZZZT|Test Co - Common Stock|Q|Y|N|100|N|N\n"
           "ABCDW|ABC Corp - Warrants|S|N|N|100|N|N\n"
           "XYZP|XYZ Inc - Series A Preferred Stock|S|N|N|100|N|N\n"
           "BADF|Bad Fin - Common Stock|S|N|D|100|N|N\n"
           "PDD|PDD Holdings Inc. - American Depositary Shares|Q|N|N|100|N|N\n"
           "File Creation Time: 0922202621:31|||||||\n")
    out = parse_nasdaq_listed(raw)
    assert sorted(out.ticker) == ['AAPL', 'PDD']
    assert dict(zip(out.ticker, out.name))['AAPL'] == 'Apple Inc.'


# 9. 성적 요약
def test_history_summary_counts():
    hist = [
        {'tf': '1d', 'eval': {'result': '적중', 'ret': 0.10, 'bench_ret': 0.02, 'finished': True}},
        {'tf': '1d', 'eval': {'result': '손절', 'ret': -0.08, 'bench_ret': 0.01, 'finished': True}},
        {'tf': '1w', 'eval': {'result': '적중', 'ret': 0.05, 'bench_ret': 0.00, 'finished': False}},
        {'tf': '1w'},
    ]
    s = history_summary(hist)
    assert s['total'] == 4 and s['hit'] == 2 and s['stop'] == 1 and s['open'] == 1
    assert s['hit_rate'] == pytest.approx(66.7, abs=0.1)
    assert s['by_tf']['1d']['hit_rate'] == 50.0
    assert s['avg_ret'] == pytest.approx(1.0, abs=1e-6)


# 10. 무작위 비교군: 같은 모양의 가격표, 재현 가능, 변동성 비슷한 종목만
def test_placebos_same_geometry_deterministic_and_vol_matched(tmp_path, monkeypatch):
    import quant_core.pattern_scanner as ps
    monkeypatch.setattr(ps, 'PLACEBO_FILE', str(tmp_path / 'placebo.json'))

    idx = pd.bdate_range('2026-01-01', '2026-09-22')
    r = np.random.default_rng(0)

    def series(vol, start=50.0):
        c = start * np.exp(np.cumsum(r.normal(0, vol, len(idx))))
        return pd.DataFrame({'Open': c, 'High': c * 1.01, 'Low': c * 0.99, 'Close': c, 'Volume': 1e6}, index=idx)

    full = {'REAL': series(0.03)}
    for i in range(10):
        full[f'SIM{i}'] = series(0.03)      # 비슷한 변동성
        full[f'CALM{i}'] = series(0.003)    # 10분의 1 변동성 -> 제외되어야 함

    hist = [{'id': 'REAL|1d|2026-03-01|2026-06-01', 'ticker': 'REAL', 'tf': '1d',
             'rec_date': '2026-09-22', 'price': 20.0, 'L': 15.0, 'H': 40.0}]
    pl = ps.ensure_placebos(hist, full, list(full.keys()))

    assert len(pl) == ps.PLACEBO_PER_REC
    assert all(p['ticker'].startswith('SIM') for p in pl)          # 변동성 비슷한 종목만
    for p in pl:
        f = p['price'] / 20.0
        assert p['L'] == pytest.approx(15.0 * f)                    # 손절까지 거리 % 동일
        assert p['H'] == pytest.approx(40.0 * f)

    # 같은 입력이면 같은 비교군 (결과를 보고 고를 수 없음)
    os.remove(ps.PLACEBO_FILE)
    pl2 = ps.ensure_placebos(hist, full, list(full.keys()))
    assert [p['ticker'] for p in pl] == [p['ticker'] for p in pl2]
    # 이미 있으면 더 만들지 않음
    assert len(ps.ensure_placebos(hist, full, list(full.keys()))) == ps.PLACEBO_PER_REC


# 11. 요약: 결과 난 추천과 같은 날짜의 비교군끼리 비교
def test_history_summary_includes_matched_baseline():
    hist = [
        {'id': 'A', 'tf': '1d', 'eval': {'result': '적중'}},
        {'id': 'B', 'tf': '1d', 'eval': {'result': '손절'}},
        {'id': 'C', 'tf': '1d', 'eval': {'result': '진행 중'}},
    ]
    placebo = [
        {'rec_id': 'A', 'eval': {'result': '손절'}}, {'rec_id': 'A', 'eval': {'result': '적중'}},
        {'rec_id': 'B', 'eval': {'result': '손절'}},
        {'rec_id': 'C', 'eval': {'result': '적중'}},     # 진짜 추천이 아직 진행 중 -> 비교에서 제외
    ]
    s = history_summary(hist, placebo)
    assert s['hit_rate'] == 50.0
    assert s['baseline']['decided'] == 4
    assert s['baseline_matched']['decided'] == 3
    assert s['baseline_matched']['hit_rate'] == pytest.approx(33.3, abs=0.1)


# 12. 실적 발표일: 14일 이내만 경고, 조회 실패는 None
def test_attach_earnings_flags_only_near_dates():
    from quant_core.pattern_scanner import attach_earnings
    setups = [{'ticker': 'A'}, {'ticker': 'B'}, {'ticker': 'C'}, {'ticker': 'D'}]
    fake = lambda ts: {'A': '2026-09-30', 'B': '2026-10-20', 'C': None, 'D': '2026-09-22'}
    attach_earnings(setups, '2026-09-22', fetch=fake)
    by = {s['ticker']: s for s in setups}
    assert by['A']['earnings_days'] == 8 and by['A']['earnings_soon'] is True
    assert by['B']['earnings_days'] == 28 and by['B']['earnings_soon'] is False
    assert by['C']['earnings_date'] is None and by['C']['earnings_soon'] is False
    assert by['D']['earnings_days'] == 0 and by['D']['earnings_soon'] is True
