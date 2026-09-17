"""
tests/test_quant_engine.py
퀀트 엔진 핵심 로직 단위 테스트 스위트:
1. 백테스트 표본 부족 시 승률 None 반환 검증
2. 추적 엔진 목표가 우선 도달 판정 검증
3. 손절가 우선 도달 판정 검증
4. 동일봉 동시 도달 시 보수적 손절 우선 처리 검증
5. 20거래일 만료 청산 검증
6. 동적 손익비(1.49 고정 탈피) 산출 검증
7. 태그 정규화 버킷 분류 검증
8. 최근 손절 종목 쿨다운 및 페널티 검증
9. Supabase 미연결 시 로컬 JSON 폴백 검증
10. 중복 추천 등록 방지 검증
"""

import os
import json
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from quant_core.prediction import predict_price_scenarios
from quant_core.screener import backtest_pattern_reliability
from quant_core.tracker import (
    normalize_factor_tag,
    record_daily_recommendations,
    load_history,
    save_history,
    get_adaptive_factor_weights,
    evaluate_and_learn_from_history,
    HISTORY_FILE
)


def make_dummy_ohlcv(days: int = 100, base_price: float = 100.0, trend: float = 0.001) -> pd.DataFrame:
    """테스트용 합성 OHLCV 데이터프레임 생성"""
    np.random.seed(42)
    dates = pd.date_range(end=datetime.now(), periods=days, freq='B')
    closes = [base_price]
    for _ in range(1, days):
        change = np.random.normal(trend, 0.015)
        closes.append(max(closes[-1] * (1 + change), 1.0))
        
    closes = np.array(closes)
    highs = closes * (1 + np.abs(np.random.normal(0.008, 0.005, days)))
    lows = closes * (1 - np.abs(np.random.normal(0.008, 0.005, days)))
    opens = (closes + lows) / 2
    volumes = np.random.randint(100000, 5000000, days)
    
    df = pd.DataFrame({
        'Open': opens,
        'High': highs,
        'Low': lows,
        'Close': closes,
        'Volume': volumes
    }, index=dates)
    return df


# 1. 백테스트 표본 부족 시 승률 None 반환 검증
def test_backtest_pattern_insufficient_samples():
    short_df = make_dummy_ohlcv(days=40)
    res = backtest_pattern_reliability(short_df, pattern_type='fibonacci')
    assert res['win_rate'] is None
    assert res['sample_count'] == 0
    assert '데이터 부족' in res['status']

    # 60일 이상이지만 진입 시그널이 없는 패턴
    flat_df = pd.DataFrame({
        'Open': [100.0] * 80,
        'High': [100.0] * 80,
        'Low': [100.0] * 80,
        'Close': [100.0] * 80,
        'Volume': [1000] * 80
    }, index=pd.date_range(end=datetime.now(), periods=80, freq='B'))
    res_flat = backtest_pattern_reliability(flat_df, pattern_type='fibonacci')
    assert res_flat['win_rate'] is None
    assert res_flat['sample_count'] == 0


# 2. 동적 손익비 (1.49:1 고정 탈피) 산출 검증
def test_dynamic_risk_reward_calculation():
    from quant_core.indicators import calculate_all_indicators
    
    # 상방 매물대가 촘촘한 종목 vs 상방이 열려있는 종목
    df = make_dummy_ohlcv(days=120, base_price=150.0)
    df = calculate_all_indicators(df)
    scenarios = predict_price_scenarios(df, days_ahead=5)
    
    assert 'risk_reward_ratio' in scenarios
    assert 'bull_target_1' in scenarios
    assert 'stop_loss' in scenarios
    
    rr = scenarios['risk_reward_ratio']
    assert isinstance(rr, (float, int))
    assert rr > 0
    # 기존 ATR 고정 공식(1.49)에 묶여있지 않은지 검증
    # 구조적 매물대와 지지/저항에 따라 종목마다 달라짐
    t1 = scenarios['bull_target_1']
    sl = scenarios['stop_loss']
    cp = scenarios['current_price']
    expected_rr = round((t1 - cp) / (cp - sl), 2)
    assert abs(rr - expected_rr) <= 0.05


# 3. 태그 정규화 버킷 분류 검증
def test_normalize_factor_tag_buckets():
    assert normalize_factor_tag("월가목표+32%") == "월가괴리_25이상"
    assert normalize_factor_tag("월가목표+15%") == "월가괴리_12_25"
    assert normalize_factor_tag("월가목표+8%") == "월가괴리_12미만"
    assert normalize_factor_tag("백테스트승률78%(2년)") == "백테스트_승률70이상"
    assert normalize_factor_tag("백테스트승률62%") == "백테스트_승률55_70"
    assert normalize_factor_tag("백테스트승률40%") == "백테스트_승률55미만"
    assert normalize_factor_tag("피보나치골든포켓") == "피보나치골든포켓"


# 4. 목표가 우선 도달 판정 검증
def test_outcome_target_hit_first(monkeypatch, tmp_path):
    # 테스트용 임시 히스토리 파일 격리
    test_hist_file = str(tmp_path / "test_history.json")
    monkeypatch.setattr("quant_core.tracker.HISTORY_FILE", test_hist_file)
    monkeypatch.setattr("quant_core.tracker.is_supabase_enabled", lambda: False)

    rec_item = {
        'date': '2026-01-05',
        'ticker': 'TEST_TARGET',
        'name': '테스트종목',
        'current_price': 100.0,
        'bull_target_1': 110.0,
        'stop_loss': 95.0,
        'tags': ['골든크로스']
    }
    record_daily_recommendations([rec_item])

    # 주가 경로 모킹: 첫날 보합, 둘째 날 112불 터치(목표가 초과), 손절가(95불) 미도달
    dates = [
        pd.Timestamp('2026-01-05'),
        pd.Timestamp('2026-01-06'),
        pd.Timestamp('2026-01-07')
    ]
    mock_df = pd.DataFrame({
        'Open': [100.0, 101.0, 105.0],
        'High': [101.0, 104.0, 112.0],  # 1월 7일에 112 달성
        'Low': [99.0, 100.0, 103.0],
        'Close': [100.0, 103.0, 111.0],
        'Volume': [1000000, 1000000, 1000000]
    }, index=dates)

    class MockTicker:
        def __init__(self, ticker):
            pass
        def history(self, *args, **kwargs):
            return mock_df

    monkeypatch.setattr("yfinance.Ticker", MockTicker)
    res = evaluate_and_learn_from_history()
    items = res['history_items']
    target_item = next(i for i in items if i['ticker'] == 'TEST_TARGET')
    
    assert target_item['is_completed'] is True
    assert target_item['hit_success'] is True
    assert '🎯 목표가 도달' in target_item['status']
    assert target_item['exit_price'] == 110.0


# 5. 손절가 우선 도달 판정 검증
def test_outcome_stop_hit_first(monkeypatch, tmp_path):
    test_hist_file = str(tmp_path / "test_history.json")
    monkeypatch.setattr("quant_core.tracker.HISTORY_FILE", test_hist_file)
    monkeypatch.setattr("quant_core.tracker.is_supabase_enabled", lambda: False)

    rec_item = {
        'date': '2026-01-05',
        'ticker': 'TEST_STOP',
        'name': '테스트손절',
        'current_price': 100.0,
        'bull_target_1': 110.0,
        'stop_loss': 94.0,
        'tags': ['추세돌파']
    }
    record_daily_recommendations([rec_item])

    dates = [
        pd.Timestamp('2026-01-05'),
        pd.Timestamp('2026-01-06'),
        pd.Timestamp('2026-01-07')
    ]
    mock_df = pd.DataFrame({
        'Open': [100.0, 98.0, 95.0],
        'High': [101.0, 99.0, 96.0],
        'Low': [98.0, 96.0, 92.0],  # 1월 7일에 92로 손절선(94) 이탈
        'Close': [99.0, 96.5, 93.0],
        'Volume': [1000000, 1000000, 1000000]
    }, index=dates)

    class MockTicker:
        def __init__(self, ticker):
            pass
        def history(self, *args, **kwargs):
            return mock_df

    monkeypatch.setattr("yfinance.Ticker", MockTicker)
    res = evaluate_and_learn_from_history()
    items = res['history_items']
    stop_item = next(i for i in items if i['ticker'] == 'TEST_STOP')

    assert stop_item['is_completed'] is True
    assert stop_item['hit_success'] is False
    assert '⚠️ 손절선 이탈' in stop_item['status']
    assert stop_item['exit_price'] == 94.0


# 6. 동일봉 동시 도달 시 보수적 손절 우선 처리 검증
def test_outcome_simultaneous_bar_conservative_loss(monkeypatch, tmp_path):
    test_hist_file = str(tmp_path / "test_history.json")
    monkeypatch.setattr("quant_core.tracker.HISTORY_FILE", test_hist_file)
    monkeypatch.setattr("quant_core.tracker.is_supabase_enabled", lambda: False)

    rec_item = {
        'date': '2026-01-05',
        'ticker': 'TEST_SPIKE',
        'name': '동일봉스파이크',
        'current_price': 100.0,
        'bull_target_1': 110.0,
        'stop_loss': 94.0,
        'tags': ['급등주']
    }
    record_daily_recommendations([rec_item])

    dates = [
        pd.Timestamp('2026-01-05'),
        pd.Timestamp('2026-01-06')
    ]
    # 1월 6일 하루에 High는 112(목표가 초과), Low는 92(손절가 하회) 동시 발생
    mock_df = pd.DataFrame({
        'Open': [100.0, 102.0],
        'High': [101.0, 112.0],
        'Low': [99.0, 92.0],
        'Close': [100.0, 105.0],
        'Volume': [1000000, 2000000]
    }, index=dates)

    class MockTicker:
        def __init__(self, ticker):
            pass
        def history(self, *args, **kwargs):
            return mock_df

    monkeypatch.setattr("yfinance.Ticker", MockTicker)
    res = evaluate_and_learn_from_history()
    items = res['history_items']
    spike_item = next(i for i in items if i['ticker'] == 'TEST_SPIKE')

    # 보수적 손실 우선 원칙: hit_success는 False여야 함
    assert spike_item['is_completed'] is True
    assert spike_item['hit_success'] is False
    assert '동일봉' in spike_item['status']


# 7. 20거래일 만료 청산 검증
def test_outcome_20_day_expiration(monkeypatch, tmp_path):
    test_hist_file = str(tmp_path / "test_history.json")
    monkeypatch.setattr("quant_core.tracker.HISTORY_FILE", test_hist_file)
    monkeypatch.setattr("quant_core.tracker.is_supabase_enabled", lambda: False)

    rec_item = {
        'date': '2026-01-01',
        'ticker': 'TEST_EXPIRE',
        'name': '만료테스트',
        'current_price': 100.0,
        'bull_target_1': 120.0,  # 도달 안 함
        'stop_loss': 85.0,        # 도달 안 함
        'tags': ['박스권']
    }
    record_daily_recommendations([rec_item])

    # 22거래일 동안 박스권(95~105) 횡보
    dates = pd.date_range(start='2026-01-01', periods=22, freq='B')
    mock_df = pd.DataFrame({
        'Open': [100.0] * 22,
        'High': [103.0] * 22,
        'Low': [97.0] * 22,
        'Close': [102.0] * 22,
        'Volume': [500000] * 22
    }, index=dates)

    class MockTicker:
        def __init__(self, ticker):
            pass
        def history(self, *args, **kwargs):
            return mock_df

    monkeypatch.setattr("yfinance.Ticker", MockTicker)
    res = evaluate_and_learn_from_history()
    items = res['history_items']
    expire_item = next(i for i in items if i['ticker'] == 'TEST_EXPIRE')

    assert expire_item['is_completed'] is True
    assert '기간 만료' in expire_item['status']


# 8. Supabase 미연결 시 로컬 JSON 폴백 및 중복 방지 검증
def test_supabase_fallback_and_deduplication(monkeypatch, tmp_path):
    test_hist_file = str(tmp_path / "test_history.json")
    monkeypatch.setattr("quant_core.tracker.HISTORY_FILE", test_hist_file)
    monkeypatch.setattr("quant_core.tracker.is_supabase_enabled", lambda: False)

    item1 = {
        'date': '2026-03-01',
        'ticker': 'NVDA',
        'name': '엔비디아',
        'current_price': 120.0,
        'bull_target_1': 135.0,
        'stop_loss': 114.0
    }
    
    # 1회 등록
    res1 = record_daily_recommendations([item1])
    assert len(res1) == 1
    assert os.path.exists(test_hist_file)

    # 동일 날짜 동일 종목 재등록 시도 -> 중복 추가되지 않음
    res2 = record_daily_recommendations([item1])
    assert len(res2) == 1
    
    # 파일에서 직접 읽어서 1개인지 확인
    with open(test_hist_file, 'r', encoding='utf-8') as f:
        saved_data = json.load(f)
    assert len(saved_data) == 1
    assert saved_data[0]['ticker'] == 'NVDA'


# 9. 최근 14일 내 손절 종목 쿨다운 및 표본 단계별(Tiered) 팩터 가중치 보정 상한 검증
def test_cooldown_and_adaptive_weights(monkeypatch, tmp_path):
    test_hist_file = str(tmp_path / "test_history.json")
    monkeypatch.setattr("quant_core.tracker.HISTORY_FILE", test_hist_file)
    monkeypatch.setattr("quant_core.tracker.is_supabase_enabled", lambda: False)

    recent_date = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')
    
    mock_history = [
        # 최근 3일 전 손절 이탈 종목 -> 쿨다운 대상이어야 함
        {
            'date': recent_date,
            'ticker': 'BAD_STOCK',
            'name': '배드스톡',
            'rec_price': 100.0,
            'target_price': 115.0,
            'stop_loss': 92.0,
            'tags': ['급락패턴'],
            'is_completed': True,
            'hit_success': False,
            'failure_reason': '기술적 손절선 하방 이탈'
        }
    ]
    
    # 1. 20건 생성 (표본 < 30건): 가중치 동결 (adjustment == 0)
    for idx in range(20):
        mock_history.append({
            'date': '2026-01-01',
            'ticker': f'WIN_SMALL_{idx}',
            'name': f'윈_{idx}',
            'rec_price': 50.0,
            'target_price': 60.0,
            'stop_loss': 47.0,
            'tags': ['소표본태그'],
            'is_completed': True,
            'hit_success': True
        })

    # 2. 40건 생성 (30 <= 표본 < 100건): 최대 ±2점 제한 보정
    for idx in range(40):
        mock_history.append({
            'date': '2026-01-01',
            'ticker': f'WIN_MID_{idx}',
            'name': f'윈_{idx}',
            'rec_price': 50.0,
            'target_price': 60.0,
            'stop_loss': 47.0,
            'tags': ['중표본태그'],
            'is_completed': True,
            'hit_success': True  # 승률 100%이지만 40건이므로 최대 +2점
        })

    # 3. 120건 생성 (100 <= 표본 < 300건): 최대 ±5점 보정
    for idx in range(120):
        mock_history.append({
            'date': '2026-01-01',
            'ticker': f'WIN_LARGE_{idx}',
            'name': f'윈_{idx}',
            'rec_price': 50.0,
            'target_price': 60.0,
            'stop_loss': 47.0,
            'tags': ['대표본태그'],
            'is_completed': True,
            'hit_success': True
        })

    save_history(mock_history)
    weights = get_adaptive_factor_weights()

    # 1. 쿨다운 검증
    assert 'BAD_STOCK' in weights['cooldown_tickers']
    assert weights['cooldown_tickers']['BAD_STOCK']['penalty'] == -12

    # 2. 표본 단계별 보정 제한 검증
    # 표본 20건 (30미만) -> 가중치 0점 (동결)
    assert weights['factor_adjustments'].get('소표본태그', 0) == 0

    # 표본 40건 (30~99건) -> 100% 승률이라도 +2점으로 상한 제한
    assert weights['factor_adjustments']['중표본태그'] == 2

    # 표본 120건 (100~299건) -> 100% 승률 시 +5점으로 보정
    assert weights['factor_adjustments']['대표본태그'] == 5


# 10. 전략 버전 (v1.0.0) 및 룰셋 메타데이터 기록 검증
def test_strategy_version_and_metadata(monkeypatch, tmp_path):
    test_hist_file = str(tmp_path / "test_history.json")
    monkeypatch.setattr("quant_core.tracker.HISTORY_FILE", test_hist_file)
    monkeypatch.setattr("quant_core.tracker.is_supabase_enabled", lambda: False)

    from quant_core.tracker import CURRENT_STRATEGY_VERSION, CURRENT_STRATEGY_RULES

    assert CURRENT_STRATEGY_VERSION == "v1.0.0"
    assert CURRENT_STRATEGY_RULES['min_score'] == 68
    assert CURRENT_STRATEGY_RULES['min_rr'] == 1.20
    assert CURRENT_STRATEGY_RULES['holding_days'] == 20

    item = {
        'date': '2026-09-17',
        'ticker': 'TSM',
        'name': 'TSMC',
        'current_price': 417.0,
        'bull_target_1': 440.0,
        'stop_loss': 400.0,
        'tags': ['피보나치지지']
    }
    history = record_daily_recommendations([item])
    assert len(history) == 1
    rec = history[0]
    assert rec['strategy_version'] == "v1.0.0"
    assert 'rules' in rec
    assert rec['rules']['min_score'] == 68
    assert rec['fee_slippage_pct'] == 0.25
    assert 'recommended_at' in rec


# 11. 기대값 (Expected Value) 및 Profit Factor 정밀 계산 검증
def test_expectancy_and_profit_factor_calculation(monkeypatch, tmp_path):
    test_hist_file = str(tmp_path / "test_history.json")
    monkeypatch.setattr("quant_core.tracker.HISTORY_FILE", test_hist_file)
    monkeypatch.setattr("quant_core.tracker.is_supabase_enabled", lambda: False)

    # 10개 표본 생성: 7승 (+10%씩), 3패 (-5%씩)
    # 총 이익: +70%, 총 손실: 15% -> Profit Factor = 70 / 15 = 4.67
    # 승률: 70%, 패배율: 30%
    # 기대값 = (0.7 * 10%) - (0.3 * 5%) - 0.25%(수수료) = 7.0 - 1.5 - 0.25 = +5.25%
    mock_history = []
    for idx in range(7):
        mock_history.append({
            'date': '2026-01-01',
            'ticker': f'WIN_{idx}',
            'name': f'윈_{idx}',
            'rec_price': 100.0,
            'target_price': 110.0,
            'stop_loss': 95.0,
            'is_completed': True,
            'hit_success': True,
            'realized_pnl_pct': 10.0,
            'fee_slippage_pct': 0.25
        })
    for idx in range(3):
        mock_history.append({
            'date': '2026-01-01',
            'ticker': f'LOSS_{idx}',
            'name': f'로스_{idx}',
            'rec_price': 100.0,
            'target_price': 110.0,
            'stop_loss': 95.0,
            'is_completed': True,
            'hit_success': False,
            'realized_pnl_pct': -5.0,
            'fee_slippage_pct': 0.25
        })

    save_history(mock_history)
    
    class MockTicker:
        def __init__(self, ticker):
            pass
        def history(self, *args, **kwargs):
            return pd.DataFrame()

    monkeypatch.setattr("yfinance.Ticker", MockTicker)
    
    # 완료 표본 평가 (네트워크 호출 없이 순수 수학 계산 검증)
    res = evaluate_and_learn_from_history()
    
    assert res['completed_count'] == 10
    assert res['wins'] == 7
    assert res['win_rate'] == 70.0
    assert res['profit_factor'] == 4.67
    assert res['expected_value'] == 5.25
    assert '실험 단계' in res['sample_tier']
    assert res['avg_win'] == 10.0
    assert res['avg_loss'] == -5.0
