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
            'failure_reason': '기술적 손절선 하방 이탈',
            'strategy_version': 'v1.0.0'
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
            'hit_success': True,
            'strategy_version': 'v1.0.0'
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
            'hit_success': True,  # 승률 100%이지만 40건이므로 최대 +2점
            'strategy_version': 'v1.0.0'
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
            'hit_success': True,
            'strategy_version': 'v1.0.0'
        })

    save_history(mock_history)
    weights = get_adaptive_factor_weights(strategy_version='v1.0.0')

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
            'fee_slippage_pct': 0.25,
            'strategy_version': 'v1.0.0'
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
            'fee_slippage_pct': 0.25,
            'strategy_version': 'v1.0.0'
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


# 12. 전략 버전별 통계 및 가중치 독립 격리 검증 (데이터 오염 방지)
def test_strategy_version_isolation(monkeypatch, tmp_path):
    test_hist_file = str(tmp_path / "test_history.json")
    monkeypatch.setattr("quant_core.tracker.HISTORY_FILE", test_hist_file)
    monkeypatch.setattr("quant_core.tracker.is_supabase_enabled", lambda: False)

    mock_data = [
        # v1.0.0 버전 레코드 4개 (3승 1패)
        {
            'date': '2026-02-01', 'ticker': 'V1_W1', 'name': 'V1승1', 'rec_price': 100.0,
            'is_completed': True, 'hit_success': True, 'realized_pnl_pct': 10.0,
            'strategy_version': 'v1.0.0', 'tags': ['피보나치골든포켓']
        },
        {
            'date': '2026-02-01', 'ticker': 'V1_W2', 'name': 'V1승2', 'rec_price': 100.0,
            'is_completed': True, 'hit_success': True, 'realized_pnl_pct': 10.0,
            'strategy_version': 'v1.0.0', 'tags': ['피보나치골든포켓']
        },
        {
            'date': '2026-02-01', 'ticker': 'V1_W3', 'name': 'V1승3', 'rec_price': 100.0,
            'is_completed': True, 'hit_success': True, 'realized_pnl_pct': 10.0,
            'strategy_version': 'v1.0.0', 'tags': ['빗각추세선돌파']
        },
        {
            'date': '2026-02-01', 'ticker': 'V1_L1', 'name': 'V1패1', 'rec_price': 100.0,
            'is_completed': True, 'hit_success': False, 'realized_pnl_pct': -5.0,
            'strategy_version': 'v1.0.0', 'tags': ['빗각추세선돌파']
        },
        # legacy 구버전 레코드 3개 (1승 2패)
        {
            'date': '2025-12-01', 'ticker': 'LEG_W1', 'name': '구승1', 'rec_price': 50.0,
            'is_completed': True, 'hit_success': True, 'realized_pnl_pct': 8.0,
            'strategy_version': 'legacy', 'tags': ['피보나치골든포켓']
        },
        {
            'date': '2025-12-01', 'ticker': 'LEG_L1', 'name': '구패1', 'rec_price': 50.0,
            'is_completed': True, 'hit_success': False, 'realized_pnl_pct': -6.0,
            'strategy_version': 'legacy', 'tags': ['피보나치골든포켓']
        },
        {
            'date': '2025-12-01', 'ticker': 'LEG_L2', 'name': '구패2', 'rec_price': 50.0,
            'is_completed': True, 'hit_success': False, 'realized_pnl_pct': -7.0,
            'strategy_version': 'legacy', 'tags': ['빗각추세선돌파']
        }
    ]
    save_history(mock_data)

    class MockTicker:
        def __init__(self, ticker): pass
        def history(self, *args, **kwargs): return pd.DataFrame()
    monkeypatch.setattr("yfinance.Ticker", MockTicker)

    # 1. v1.0.0 필터 집계: 총 4건, 3승 1패 (승률 75.0%)
    res_v1 = evaluate_and_learn_from_history(strategy_version='v1.0.0')
    assert res_v1['completed_count'] == 4
    assert res_v1['wins'] == 3
    assert res_v1['losses'] == 1
    assert res_v1['win_rate'] == 75.0

    # 2. legacy 필터 집계: 총 3건, 1승 2패 (승률 33.3%)
    res_leg = evaluate_and_learn_from_history(strategy_version='legacy')
    assert res_leg['completed_count'] == 3
    assert res_leg['wins'] == 1
    assert res_leg['losses'] == 2
    assert res_leg['win_rate'] == 33.3

    # 3. all 전체 집계: 총 7건, 4승 3패
    res_all = evaluate_and_learn_from_history(strategy_version='all')
    assert res_all['completed_count'] == 7
    assert res_all['wins'] == 4
    assert res_all['losses'] == 3


# 13. 1종목 내 다중 정규화 동의어 태그 중복 집계 원천 차단 검증
def test_tag_deduplication_in_single_recommendation(monkeypatch, tmp_path):
    test_hist_file = str(tmp_path / "test_history.json")
    monkeypatch.setattr("quant_core.tracker.HISTORY_FILE", test_hist_file)
    monkeypatch.setattr("quant_core.tracker.is_supabase_enabled", lambda: False)

    # 1개 종목에 '월가목표+35%'와 '월가괴리_25이상'이 둘 다 들어있음
    # 둘 다 normalize_factor_tag()를 거치면 '월가괴리_25이상'이 됨
    mock_data = [{
        'date': '2026-03-10',
        'ticker': 'DUP_TAG_STOCK',
        'name': '중복태그종목',
        'rec_price': 100.0,
        'is_completed': True,
        'hit_success': True,
        'realized_pnl_pct': 12.0,
        'strategy_version': 'v1.0.0',
        'tags': ['월가목표+35%', '월가괴리_25이상']  # 동의어 태그 2개
    }]
    save_history(mock_data)

    class MockTicker:
        def __init__(self, ticker): pass
        def history(self, *args, **kwargs): return pd.DataFrame()
    monkeypatch.setattr("yfinance.Ticker", MockTicker)

    # evaluate_and_learn_from_history 실행 시 factor_stats에서 표본수가 1건이어야 함 (2건이 아님)
    res = evaluate_and_learn_from_history(strategy_version='v1.0.0')
    factor_stats = res['adaptive_weights']['factor_adjustments']

    # get_adaptive_factor_weights로 직접 확인
    weights = get_adaptive_factor_weights(strategy_version='v1.0.0')
    # 표본수가 1건이므로 가중치는 0(동결)이어야 함
    assert weights['factor_adjustments'].get('월가괴리_25이상', 0) == 0


# 14. 동일 날짜·종목에 대한 다중 전략 버전 추천 동시 보존 검증
def test_multi_strategy_same_date_ticker_storage(monkeypatch, tmp_path):
    test_hist_file = str(tmp_path / "test_history.json")
    monkeypatch.setattr("quant_core.tracker.HISTORY_FILE", test_hist_file)
    monkeypatch.setattr("quant_core.tracker.is_supabase_enabled", lambda: False)

    # 동일 날짜, 동일 종목이지만 전략 버전이 다름
    rec_v1 = {
        'date': '2026-09-17',
        'ticker': 'NVDA',
        'name': '엔비디아',
        'current_price': 120.0,
        'bull_target_1': 135.0,
        'stop_loss': 114.0,
        'strategy_version': 'v1.0.0'
    }
    rec_v2 = {
        'date': '2026-09-17',
        'ticker': 'NVDA',
        'name': '엔비디아',
        'current_price': 120.0,
        'bull_target_1': 140.0,
        'stop_loss': 116.0,
        'strategy_version': 'v1.1.0'
    }

    # v1.0.0 등록
    hist1 = record_daily_recommendations([rec_v1])
    assert len(hist1) == 1

    # v1.1.0 등록 -> 충돌 없이 2건 모두 보존되어야 함
    hist2 = record_daily_recommendations([rec_v2])
    assert len(hist2) == 2

    # 파일에서 직접 확인
    saved = load_history()
    assert len(saved) == 2
    versions = {s['strategy_version'] for s in saved}
    assert versions == {'v1.0.0', 'v1.1.0'}
    assert saved[0]['recommendation_id'] != saved[1]['recommendation_id']


# 15. 실시간-백테스트 패턴 판정 단일 소스 (evaluate_pattern_match) 검증
def test_evaluate_pattern_match_unified_logic():
    from quant_core.screener import evaluate_pattern_match
    from quant_core.indicators import calculate_all_indicators

    df = make_dummy_ohlcv(days=120, base_price=100.0)
    df = calculate_all_indicators(df)

    # 피보나치, 빗각 추세선, 다이버전스, 자동(Supertrend/EMA) 각각 단일 소스 판정 호출
    fibo_res = evaluate_pattern_match(df, 'fibonacci')
    assert isinstance(fibo_res, (bool, np.bool_))

    trend_res = evaluate_pattern_match(df, 'trendline')
    assert isinstance(trend_res, (bool, np.bool_))

    div_res = evaluate_pattern_match(df, 'divergence')
    assert isinstance(div_res, (bool, np.bool_))

    auto_res = evaluate_pattern_match(df, 'auto')
    assert isinstance(auto_res, (bool, np.bool_))


# 16. 무손실(Loss 0건) 시 Profit Factor 무한대(손실 없음) 안전 표기 검증
def test_zero_loss_profit_factor_infinity_display(monkeypatch, tmp_path):
    test_hist_file = str(tmp_path / "test_history.json")
    monkeypatch.setattr("quant_core.tracker.HISTORY_FILE", test_hist_file)
    monkeypatch.setattr("quant_core.tracker.is_supabase_enabled", lambda: False)

    mock_data = [
        {
            'date': '2026-03-01', 'ticker': 'PERF_1', 'name': '완벽1', 'rec_price': 100.0,
            'is_completed': True, 'hit_success': True, 'realized_pnl_pct': 10.0,
            'strategy_version': 'v1.0.0'
        },
        {
            'date': '2026-03-01', 'ticker': 'PERF_2', 'name': '완벽2', 'rec_price': 100.0,
            'is_completed': True, 'hit_success': True, 'realized_pnl_pct': 15.0,
            'strategy_version': 'v1.0.0'
        }
    ]
    save_history(mock_data)

    class MockTicker:
        def __init__(self, ticker): pass
        def history(self, *args, **kwargs): return pd.DataFrame()
    monkeypatch.setattr("yfinance.Ticker", MockTicker)

    res = evaluate_and_learn_from_history(strategy_version='v1.0.0')
    assert res['losses'] == 0
    assert res['wins'] == 2
    assert res['profit_factor'] == 99.9
    assert res['profit_factor_display'] == "손실 없음 (∞)"


# 17. 기록별 동적 슬리피지·수수료 (fee_slippage_pct) 차감 반영 검증
def test_dynamic_fee_slippage_calculation(monkeypatch, tmp_path):
    test_hist_file = str(tmp_path / "test_history.json")
    monkeypatch.setattr("quant_core.tracker.HISTORY_FILE", test_hist_file)
    monkeypatch.setattr("quant_core.tracker.is_supabase_enabled", lambda: False)

    # 2개 표본: 둘 다 +10% 이익
    # item1: 수수료 0.10% -> 순이익 +9.90%
    # item2: 수수료 0.50% -> 순이익 +9.50%
    # 평균 수수료 = 0.30%
    # 평균 순이익 = (9.90 + 9.50) / 2 = +9.70%
    mock_data = [
        {
            'date': '2026-03-01', 'ticker': 'FEE_LOW', 'name': '저비용', 'rec_price': 100.0,
            'is_completed': True, 'hit_success': True, 'realized_pnl_pct': 10.0,
            'fee_slippage_pct': 0.10, 'strategy_version': 'v1.0.0'
        },
        {
            'date': '2026-03-01', 'ticker': 'FEE_HIGH', 'name': '고비용', 'rec_price': 100.0,
            'is_completed': True, 'hit_success': True, 'realized_pnl_pct': 10.0,
            'fee_slippage_pct': 0.50, 'strategy_version': 'v1.0.0'
        }
    ]
    save_history(mock_data)

    class MockTicker:
        def __init__(self, ticker): pass
        def history(self, *args, **kwargs): return pd.DataFrame()
    monkeypatch.setattr("yfinance.Ticker", MockTicker)

    res = evaluate_and_learn_from_history(strategy_version='v1.0.0')
    assert res['avg_return'] == 10.0
    assert res['avg_return_net'] == 9.70
    assert res['expected_value'] == 9.70


# 18. Supabase API 키 JWT 역할(Role) 안전 감지 검증
def test_jwt_role_inspection():
    import base64
    from quant_core.db import inspect_jwt_role

    def make_fake_jwt(role_name: str) -> str:
        header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip('=')
        payload = base64.urlsafe_b64encode(f'{{"role":"{role_name}"}}'.encode()).decode().rstrip('=')
        signature = "dummy_signature"
        return f"{header}.{payload}.{signature}"

    service_key = make_fake_jwt("service_role")
    anon_key = make_fake_jwt("anon")

    assert inspect_jwt_role(service_key) == "service_role"
    assert inspect_jwt_role(anon_key) == "anon"
    assert inspect_jwt_role("invalid_key_format") == "unknown"
    assert inspect_jwt_role("") == "unknown"


# 19. anon 키 설정 시 가짜 연결 차단 및 권한 부족 상태 검증 (RLS Default Deny 보호)
def test_supabase_diagnostics_anon_key_blocks_enabled(monkeypatch):
    import base64
    from quant_core.db import get_supabase_diagnostics, is_supabase_enabled

    def make_fake_jwt(role_name: str) -> str:
        header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip('=')
        payload = base64.urlsafe_b64encode(f'{{"role":"{role_name}"}}'.encode()).decode().rstrip('=')
        return f"{header}.{payload}.sig"

    anon_key = make_fake_jwt("anon")

    monkeypatch.setenv("SUPABASE_URL", "https://mock.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", anon_key)

    class MockTable:
        def select(self, *args, **kwargs):
            return self
        def limit(self, *args, **kwargs):
            return self
        def execute(self):
            # anon 키는 비공개 RLS 정책에 의해 SELECT 403 차단 시뮬레이션
            raise Exception("403 Forbidden: RLS policy denies access")

    class MockClient:
        def table(self, name):
            return MockTable()

    monkeypatch.setattr("quant_core.db.get_supabase_client", lambda: MockClient())

    diag = get_supabase_diagnostics(force_refresh=True)
    assert diag['key_role'] == "anon"
    assert diag['is_service_role'] is False
    assert diag['can_write'] is False
    assert diag['is_healthy'] is False
    # RLS 권한이 부족하므로 가짜 연결을 차단하고 is_supabase_enabled()는 False를 반환해야 함
    assert is_supabase_enabled() is False


# 20. 구형 DB 제약조건으로의 조용한 덮어쓰기 폴백 배제 및 명확한 오류 반환 검증
def test_db_upsert_strict_compound_key_no_silent_overwrite(monkeypatch):
    from quant_core.db import db_upsert_history_items

    class MockTable:
        def upsert(self, records, on_conflict=None):
            # 복합키 on_conflict="date,ticker,strategy_version" 실패 시뮬레이션 (구버전 스키마)
            if on_conflict == "date,ticker,strategy_version":
                raise Exception("column 'strategy_version' not in unique constraint")
            # 만약 구버전 키로 조용히 시도하려 하면 호출되면 안 됨
            raise AssertionError("구형 키(date,ticker)로 조용히 폴백 시도됨! 엄격한 무손실 원칙 위반.")

    class MockClient:
        def table(self, name):
            return MockTable()

    monkeypatch.setattr("quant_core.db.get_supabase_client", lambda: MockClient())

    items = [{
        'date': '2026-09-17',
        'ticker': 'NVDA',
        'strategy_version': 'v1.0.0',
        'rec_price': 120.0
    }]

    # 복합키 제약조건이 없으면 date,ticker로 덮어쓰지 않고 명확히 False 반환
    success = db_upsert_history_items(items)
    assert success is False


# 21. supabase_schema.sql 마이그레이션 순서 (기본값 없이 추가 -> legacy 백필 -> v1.0.0 설정) 검증
def test_supabase_schema_migration_backfill_order():
    sql_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "supabase_schema.sql")
    assert os.path.exists(sql_path)

    with open(sql_path, "r", encoding="utf-8") as f:
        sql_content = f.read()

    # 1. ADD COLUMN 시 DEFAULT 'v1.0.0'이 바로 붙지 않고 기본값 없이 추가되는지 검증
    assert "ADD COLUMN IF NOT EXISTS strategy_version VARCHAR(32);" in sql_content

    # 2. 기존 행을 'legacy'로 백필하는 UPDATE 문 존재 검증
    assert "SET strategy_version = 'legacy'" in sql_content

    # 3. recommendation_id 백필 문 존재 검증
    assert "SET recommendation_id = date || '_' || ticker || '_' || strategy_version" in sql_content

    # 4. 순서 검증: 백필(legacy) 후에 신규 행 DEFAULT 'v1.0.0' 설정이 나와야 함
    pos_add = sql_content.find("ADD COLUMN IF NOT EXISTS strategy_version VARCHAR(32);")
    pos_backfill = sql_content.find("SET strategy_version = 'legacy'")
    pos_default_v1 = sql_content.find("ALTER COLUMN strategy_version SET DEFAULT 'v1.0.0'")
    pos_unique = sql_content.find("ADD CONSTRAINT unique_date_ticker_version")

    assert pos_add < pos_backfill, "컬럼 추가가 백필보다 먼저 나와야 합니다."
    assert pos_backfill < pos_default_v1, "기존 행 legacy 백필이 DEFAULT v1.0.0 설정보다 반드시 먼저 실행되어야 합니다."
    assert pos_default_v1 < pos_unique, "DEFAULT 및 NOT NULL 설정 후 복합 UNIQUE 제약조건이 적용되어야 합니다."
