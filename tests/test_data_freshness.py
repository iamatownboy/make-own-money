"""
야후 일봉이 마지막 거래일까지 올라왔는지 기다려 확인하는 로직 테스트.
(UTC 자정 이후 스캔이 하루 전 종가로 계산되던 문제)
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quant_core import data_loader
from quant_core.data_loader import (last_session_date, latest_bar_dates, wait_for_latest_daily_bars,
                                    SESSION_PROBE)
from quant_core.pattern_scanner import data_staleness

FRI = pd.Timestamp('2026-10-30')
THU = pd.Timestamp('2026-10-29')


class FakeClock:
    """sleep 하면 시간이 흐르는 가짜 시계 (실제로 기다리지 않는다)."""
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, sec):
        self.sleeps.append(sec)
        self.now += sec


def _dates(n_fresh, fresh=FRI, old=THU):
    return {t: (fresh if i < n_fresh else old) for i, t in enumerate(SESSION_PROBE)}


def _wait(session, dates_seq, max_wait=5400, poll=300):
    fc = FakeClock()
    seq = iter(dates_seq)
    calls = []

    def dates_fn(_probe):
        calls.append(1)
        return next(seq)

    logs = []
    ok = wait_for_latest_daily_bars(max_wait_sec=max_wait, poll_sec=poll, session_fn=lambda: session,
                                    dates_fn=dates_fn, sleep=fc.sleep, clock=fc.clock, log=logs.append)
    return ok, fc, len(calls), logs


# 1. 이미 올라와 있으면 기다리지 않는다
def test_returns_immediately_when_bars_are_current():
    ok, fc, calls, _ = _wait(FRI, [_dates(6)])
    assert ok and fc.sleeps == [] and calls == 1


# 2. 처음엔 하루 전 봉만 있다가 확정되면 그때 통과 (실제 사고 상황)
def test_waits_until_latest_bar_is_published():
    ok, fc, calls, logs = _wait(FRI, [_dates(0), _dates(0), _dates(5)])
    assert ok
    assert fc.sleeps == [300, 300] and calls == 3
    assert len(logs) == 2                      # 기다린 횟수만큼만 로그


# 3. 끝내 안 올라오면 제한 시간 뒤 False, 그 이상 오래 기다리지 않는다
def test_gives_up_after_max_wait():
    ok, fc, calls, logs = _wait(FRI, [_dates(0)] * 100, max_wait=900, poll=300)
    assert ok is False
    assert fc.now <= 900
    assert fc.sleeps == [300, 300, 300] and calls == 4
    assert '아직 확정되지 않았습니다' in logs[-1]


# 4. 표본의 절반을 넘어야 통과 (6종목 중 3개는 부족, 4개부터 통과)
def test_requires_majority_of_probe():
    ok3, *_ = _wait(FRI, [_dates(3)] * 100, max_wait=300, poll=300)
    ok4, *_ = _wait(FRI, [_dates(4)])
    assert ok3 is False and ok4 is True


# 5. 마지막 거래일을 모르면 확인할 수 없으니 막지 않는다
def test_does_not_block_when_session_unknown():
    fc = FakeClock()
    ok = wait_for_latest_daily_bars(session_fn=lambda: None,
                                    dates_fn=lambda _p: pytest.fail('시세를 받으면 안 됨'),
                                    sleep=fc.sleep, clock=fc.clock, log=lambda _m: None)
    assert ok and fc.sleeps == []


# 6. 시세를 못 받아오는 중(빈 결과)에도 제한 시간 안에서만 기다린다
def test_empty_dates_keep_waiting_but_bounded():
    ok, fc, _, _ = _wait(FRI, [{}] * 100, max_wait=600, poll=300)
    assert ok is False and fc.now <= 600


# 7. 야후 마지막 거래일 해석: 뉴욕 시각의 tz-aware Timestamp / epoch 초 / 실패
def _patch_ticker(monkeypatch, metadata=None, history=None, boom=False):
    class FakeTicker:
        def __init__(self, _t):
            pass

        @property
        def history_metadata(self):
            if boom:
                raise RuntimeError('yahoo down')
            return metadata

        def history(self, **_kw):
            return history
    monkeypatch.setattr(data_loader.yf, 'Ticker', FakeTicker)


def test_last_session_date_from_tz_aware_timestamp(monkeypatch):
    ts = pd.Timestamp('2026-10-30 16:00:01', tz='America/New_York')
    _patch_ticker(monkeypatch, {'regularMarketTime': ts})
    assert last_session_date() == FRI


def test_last_session_date_from_epoch_seconds(monkeypatch):
    # 2026-10-30 16:00 ET = 20:00 UTC. UTC 로 읽어도 뉴욕 날짜(10/30)여야 한다
    epoch = int(pd.Timestamp('2026-10-30 20:00:00', tz='UTC').timestamp())
    _patch_ticker(monkeypatch, {'regularMarketTime': epoch})
    assert last_session_date() == FRI


def test_last_session_date_uses_new_york_date_not_utc(monkeypatch):
    # 뉴욕 22시 = 다음날 02시(UTC). 날짜는 뉴욕 기준 10/30 이어야 한다
    ts = pd.Timestamp('2026-10-31 02:00:00', tz='UTC')
    _patch_ticker(monkeypatch, {'regularMarketTime': ts})
    assert last_session_date() == FRI


@pytest.mark.parametrize('kwargs', [{'metadata': {}}, {'metadata': {'regularMarketTime': None}}, {'boom': True}])
def test_last_session_date_returns_none_on_failure(monkeypatch, kwargs):
    _patch_ticker(monkeypatch, **kwargs)
    assert last_session_date() is None


# 8. 당일 행이 OHLC NaN 으로 오면 마지막 봉 날짜는 하루 전 (사고의 원인 재현)
def test_unfinished_bar_with_nan_ohlc_is_not_counted(monkeypatch):
    idx = pd.to_datetime(['2026-10-28', '2026-10-29', '2026-10-30'])
    hist = pd.DataFrame({'Open': [1.0, 2.0, float('nan')], 'High': [1.0, 2.0, float('nan')],
                         'Low': [1.0, 2.0, float('nan')], 'Close': [1.0, 2.0, float('nan')],
                         'Volume': [10, 10, 5]}, index=idx)
    _patch_ticker(monkeypatch, history=hist)
    assert latest_bar_dates(['QQQ']) == {'QQQ': THU}


def test_latest_bar_dates_skips_failed_tickers(monkeypatch):
    _patch_ticker(monkeypatch, history=pd.DataFrame())
    assert latest_bar_dates(['QQQ', 'AAPL']) == {}


# 9. 결과 파일에 낡은 시세 표시
@pytest.mark.parametrize('rec_date, session, expected', [
    ('2026-10-29', FRI, {'latest_session': '2026-10-30', 'stale': True}),
    ('2026-10-30', FRI, {'latest_session': '2026-10-30', 'stale': False}),
    ('2026-10-30', None, {'latest_session': None, 'stale': False}),
])
def test_data_staleness(rec_date, session, expected):
    assert data_staleness(rec_date, session_fn=lambda: session) == expected
