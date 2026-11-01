"""
추천 기록이 대상이 달랐던 시기(소형주 포함 등)와 섞여 있을 때, 지금 대상 종목만의 성적을 따로 내는지 테스트.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quant_core.pattern_scanner import history_summary, universe_subset
from pattern_tab import _score_line, RELIABLE_DECIDED


def _rec(i, ticker, result, tf='1d'):
    return {'id': f'{ticker}|{tf}|{i}', 'ticker': ticker, 'tf': tf, 'rec_date': '2026-10-01',
            'eval': {'result': result, 'finished': result != '진행 중', 'ret': 0.1 if result == '적중' else -0.08,
                     'bench_ret': 0.01}}


def _placebo(rec, result):
    return {'id': f'P|{rec["id"]}', 'rec_id': rec['id'], 'ticker': 'ZZZ', 'tf': rec['tf'],
            'rec_date': rec['rec_date'], 'eval': {'result': result}}


def _data():
    hist = [_rec(1, 'AAPL', '적중'), _rec(2, 'MSFT', '손절'), _rec(3, 'NVDA', '진행 중'),
            _rec(4, 'TINY', '손절'), _rec(5, 'SMALL', '손절'), _rec(6, 'MICRO', '적중')]
    placebo = [_placebo(h, '손절') for h in hist]
    return hist, placebo


def test_universe_subset_keeps_only_current_tickers_and_their_placebos():
    hist, placebo = _data()
    h, p = universe_subset(hist, placebo, ['AAPL', 'MSFT', 'NVDA'])
    assert [x['ticker'] for x in h] == ['AAPL', 'MSFT', 'NVDA']
    # 비교군은 비교군 종목(ZZZ)이 아니라 딸린 추천 기준으로 걸러진다
    assert {x['rec_id'] for x in p} == {x['id'] for x in h}


def test_subset_summary_ignores_other_universe_records():
    hist, placebo = _data()
    cur = history_summary(*universe_subset(hist, placebo, ['AAPL', 'MSFT', 'NVDA']))
    assert (cur['total'], cur['decided'], cur['hit'], cur['stop'], cur['open']) == (3, 2, 1, 1, 1)
    assert cur['hit_rate'] == 50.0
    assert cur['baseline']['total'] == 3 and cur['baseline_matched']['decided'] == 2
    full = history_summary(hist, placebo)
    assert full['total'] == 6 and full['decided'] == 5      # 전체는 그대로


def test_subset_with_no_records_is_empty_not_error():
    hist, placebo = _data()
    cur = history_summary(*universe_subset(hist, placebo, ['NOPE']))
    assert cur['total'] == 0 and cur['hit_rate'] is None


def test_score_line_without_current_is_unchanged_legacy_text():
    hist, placebo = _data()
    s = history_summary(hist, placebo)
    line = _score_line(s)
    assert '전체 기록' not in line and '지금까지 추천 <b>6건</b>' in line


def test_score_line_shows_current_universe_first_with_small_sample_warning():
    hist, placebo = _data()
    s = history_summary(hist, placebo)
    s['current'] = history_summary(*universe_subset(hist, placebo, ['AAPL', 'MSFT', 'NVDA']))
    line = _score_line(s, '나스닥100')
    assert line.index('나스닥100 종목 추천 <b>3건</b>') < line.index('전체 기록')
    assert '결과 난 추천이 2건뿐이라 오차가 큽니다' in line
    # 표본이 적으면 비교군 수치만 보이고 '차이 ±%p'는 숨긴다. 전체 기록(5건)도 같은 함수라 같은 기준은 아님
    cur_part = line.split('전체 기록')[0]
    assert '차이' not in cur_part


def test_score_line_shows_diff_once_sample_is_large_enough():
    cur = {'total': 150, 'decided': RELIABLE_DECIDED, 'hit': 40, 'stop': 60, 'hit_rate': 40.0,
           'baseline_matched': {'decided': 100, 'hit_rate': 30.0}}
    line = _score_line({'total': 150, 'decided': 100, 'hit': 40, 'stop': 60, 'hit_rate': 40.0, 'current': cur}, '나스닥100')
    cur_part = line.split('전체 기록')[0]
    assert '차이 <b>+10.0%p</b>' in cur_part and '오차가 큽니다' not in cur_part


def test_score_line_when_no_current_records_yet():
    hist, placebo = _data()
    s = history_summary(hist, placebo)
    s['current'] = history_summary(*universe_subset(hist, placebo, ['NOPE']))
    line = _score_line(s, '나스닥100')
    assert '추천 기록을 쌓기 시작했습니다' in line and '전체 기록' in line
