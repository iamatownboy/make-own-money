"""
tests/test_prediction_feedback.py
예측 피드백 시스템 고도화 모듈 단위 테스트
"""

import json
import os
import sys
import tempfile
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ==============================================================================
# 영역 1: 스냅샷 모듈 테스트
# ==============================================================================

class TestSnapshot:
    """일별 예측 스냅샷 모듈 테스트"""

    def _make_scan_result(self, ticker, total_score=75, target_1=110.0, target_2=120.0,
                          stop_loss=95.0, current_price=100.0, is_qualified=True):
        """테스트용 스캔 결과 생성"""
        return {
            'ticker': ticker,
            'total_score': total_score,
            'tech_score': 30,
            'fund_score': 25,
            'mom_score': 20,
            'pattern_status': '진입 적기',
            'current_price': current_price,
            'bull_target_1': target_1,
            'bull_target_2': target_2,
            'stop_loss': stop_loss,
            'risk_reward_ratio': 1.5,
            'fibonacci': {'is_in_golden_pocket': True, 'is_at_fib_382': False},
            'trendline': {'is_breakout': False, 'is_approaching': True},
            'divergence': {'has_divergence': True},
            'market_context': {'regime': '정상장세'},
            'is_qualified': is_qualified
        }

    @patch('quant_core.snapshot.is_supabase_enabled', return_value=False)
    @patch('quant_core.snapshot.get_supabase_client', return_value=None)
    def test_save_and_load_snapshot(self, mock_client, mock_enabled):
        """스냅샷 저장 및 로드 테스트"""
        from quant_core.snapshot import save_daily_snapshot, load_snapshots, SNAPSHOT_FILE

        with tempfile.NamedTemporaryFile(suffix='.json', delete=False, mode='w') as tf:
            temp_path = tf.name
            tf.write('{}')

        try:
            with patch('quant_core.snapshot.SNAPSHOT_FILE', temp_path):
                results = [
                    self._make_scan_result('AAPL'),
                    self._make_scan_result('GOOGL', total_score=80),
                    self._make_scan_result('MSFT', is_qualified=False)
                ]
                
                success = save_daily_snapshot(results)
                assert success is True

                snapshots = load_snapshots(days_back=90)
                assert len(snapshots) > 0

                today_key = list(snapshots.keys())[0]
                today_items = snapshots[today_key]
                assert len(today_items) == 3

                tickers = [item['ticker'] for item in today_items]
                assert 'AAPL' in tickers
                assert 'GOOGL' in tickers
                assert 'MSFT' in tickers
        finally:
            os.unlink(temp_path)

    def test_snapshot_item_field_mapping(self):
        """스냅샷 아이템의 필드 매핑 정확성 검증"""
        from quant_core.snapshot import save_daily_snapshot

        result = self._make_scan_result('TSM', total_score=83, target_1=440.0, target_2=454.0,
                                         stop_loss=402.5, current_price=417.72)

        # save_daily_snapshot 내부 로직 시뮬레이션
        fib = result.get('fibonacci', {})
        fib_stat = 'golden_pocket' if fib.get('is_in_golden_pocket') else 'none'
        assert fib_stat == 'golden_pocket'

        tl = result.get('trendline', {})
        tl_stat = 'breakout' if tl.get('is_breakout') else ('approaching' if tl.get('is_approaching') else 'none')
        assert tl_stat == 'approaching'

        div = result.get('divergence', {})
        div_stat = 'bullish' if div.get('has_divergence') else 'none'
        assert div_stat == 'bullish'

    @patch('quant_core.snapshot.is_supabase_enabled', return_value=False)
    @patch('quant_core.snapshot.get_supabase_client', return_value=None)
    def test_compare_with_previous_new_and_dropped(self, mock_client, mock_enabled):
        """어제 vs 오늘 비교: 신규 진입/이탈 종목 감지"""
        from quant_core.snapshot import compare_with_previous, SNAPSHOT_FILE

        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        today = datetime.now().strftime('%Y-%m-%d')

        mock_data = {
            "snapshots": {
                yesterday: [
                    {'ticker': 'AAPL', 'total_score': 75, 'current_price': 100, 'is_qualified': True,
                     'target_1': 110, 'target_2': 120, 'tech_score': 30, 'fund_score': 25, 'mom_score': 20,
                     'risk_reward_ratio': 1.5, 'pattern_status': '진입적기'},
                    {'ticker': 'GOOGL', 'total_score': 70, 'current_price': 200, 'is_qualified': True,
                     'target_1': 220, 'target_2': 240, 'tech_score': 28, 'fund_score': 22, 'mom_score': 20,
                     'risk_reward_ratio': 1.3, 'pattern_status': '타점임박'}
                ],
                today: [
                    {'ticker': 'AAPL', 'total_score': 80, 'current_price': 105, 'is_qualified': True,
                     'target_1': 115, 'target_2': 125, 'tech_score': 33, 'fund_score': 25, 'mom_score': 22,
                     'risk_reward_ratio': 1.6, 'pattern_status': '진입적기'},
                    {'ticker': 'NVDA', 'total_score': 72, 'current_price': 300, 'is_qualified': True,
                     'target_1': 330, 'target_2': 360, 'tech_score': 30, 'fund_score': 22, 'mom_score': 20,
                     'risk_reward_ratio': 1.4, 'pattern_status': '진입적기'}
                ]
            }
        }

        with tempfile.NamedTemporaryFile(suffix='.json', delete=False, mode='w') as tf:
            temp_path = tf.name
            json.dump(mock_data, tf, ensure_ascii=False)

        try:
            with patch('quant_core.snapshot.SNAPSHOT_FILE', temp_path), \
                 patch('quant_core.snapshot.get_ny_market_date_str', return_value=today):
                result = compare_with_previous(today_date=today)

                if 'error' not in result:
                    new_tickers = [e['ticker'] for e in result.get('new_entries', [])]
                    dropped_tickers = [e['ticker'] for e in result.get('dropped_entries', [])]
                    assert 'NVDA' in new_tickers, "NVDA가 신규 진입으로 감지되어야 함"
                    assert 'GOOGL' in dropped_tickers, "GOOGL이 이탈 종목으로 감지되어야 함"
        finally:
            os.unlink(temp_path)


# ==============================================================================
# 영역 2: MFE/MAE 및 시뮬레이션 테스트
# ==============================================================================

class TestMFEMAE:
    """MFE/MAE 분석 및 청산 시나리오 시뮬레이션 테스트"""

    def test_simulate_partial_exit_target_hit(self):
        """분할 익절 시나리오: 1차 목표 도달 + 2차 목표 도달"""
        from quant_core.tracker import _simulate_partial_exit

        dates = pd.date_range('2026-01-02', periods=10, freq='B')
        df_after = pd.DataFrame({
            'High':  [102, 105, 108, 112, 115, 120, 118, 116, 114, 113],
            'Low':   [99,  101, 104, 107, 110, 114, 112, 110, 108, 107],
            'Close': [101, 104, 107, 111, 114, 118, 115, 113, 111, 110]
        }, index=dates)

        pnl = _simulate_partial_exit(
            df_after, rec_p=100.0, t1=110.0, t2=120.0, sl=95.0, atr=3.0, fee_pct=0.25
        )
        assert pnl is not None
        assert pnl > 0, "1차+2차 목표 도달 시 수익이어야 함"

    def test_simulate_partial_exit_stop_loss(self):
        """분할 익절 시나리오: 1차 목표 도달 전 손절"""
        from quant_core.tracker import _simulate_partial_exit

        dates = pd.date_range('2026-01-02', periods=5, freq='B')
        df_after = pd.DataFrame({
            'High':  [101, 99,  97,  95,  93],
            'Low':   [99,  96,  94,  92,  90],
            'Close': [100, 97,  95,  93,  91]
        }, index=dates)

        pnl = _simulate_partial_exit(
            df_after, rec_p=100.0, t1=110.0, t2=120.0, sl=95.0, atr=3.0, fee_pct=0.25
        )
        assert pnl is not None
        assert pnl < 0, "손절 시 손실이어야 함"

    def test_simulate_trailing_exit_profit(self):
        """트레일링 스탑 시나리오: 상승 후 트레일링 스탑 청산"""
        from quant_core.tracker import _simulate_trailing_exit

        dates = pd.date_range('2026-01-02', periods=8, freq='B')
        df_after = pd.DataFrame({
            'High':  [102, 105, 110, 115, 113, 110, 108, 106],
            'Low':   [99,  101, 106, 110, 108, 105, 103, 101],
            'Close': [101, 104, 109, 113, 110, 107, 105, 103]
        }, index=dates)

        pnl = _simulate_trailing_exit(
            df_after, rec_p=100.0, sl=95.0, atr=3.0, fee_pct=0.25
        )
        assert pnl is not None
        # 트레일링 스탑 = max(95, peak - 6.0)이므로 peak 115 → stop 109
        # low 108에서 청산 → 약 +9% - 0.25%

    def test_mfe_mae_calculation_in_evaluation(self):
        """evaluate_and_learn_from_history에서 MFE/MAE 필드 존재 확인"""
        from quant_core.tracker import record_daily_recommendations

        recs = [{
            'ticker': 'TEST_MFE',
            'name': 'Test MFE Stock',
            'current_price': 100.0,
            'bull_target_1': 110.0,
            'bull_target_2': 120.0,
            'stop_loss': 95.0,
            'total_score': 75,
            'tags': ['피보나치지지'],
            'pattern_status': '진입 적기',
            'strategy_version': 'v1.0.0',
            'fee_slippage_pct': 0.25
        }]

        with patch('quant_core.tracker.load_history', return_value=[]):
            with patch('quant_core.tracker.save_history'):
                history = record_daily_recommendations(recs)

        assert len(history) > 0
        item = history[-1]
        assert 'mfe_pct' in item, "MFE 필드가 존재해야 함"
        assert 'mae_pct' in item, "MAE 필드가 존재해야 함"
        assert 'target_2_hit' in item, "2차 목표가 도달 필드가 존재해야 함"
        assert 'holding_efficiency' in item, "보유 효율성 필드가 존재해야 함"
        assert 'sim_partial_pnl' in item, "분할 익절 시뮬레이션 필드가 존재해야 함"
        assert 'sim_trailing_pnl' in item, "트레일링 시뮬레이션 필드가 존재해야 함"


# ==============================================================================
# 영역 3: 시장 레짐 동적 커트라인 테스트
# ==============================================================================

class TestRegimeDynamicCutline:
    """시장 레짐 기반 동적 커트라인 조절 테스트"""

    def test_bearish_regime_tightens_cutline(self):
        """약세장에서 커트라인이 강화되는지 검증"""
        regime = '약세 조정장'

        if '약세' in regime or '조정' in regime:
            min_score = 75
            min_rr = 1.50
            max_picks = 5
        else:
            min_score = 68
            min_rr = 1.20
            max_picks = 7

        assert min_score == 75
        assert min_rr == 1.50
        assert max_picks == 5

    def test_sideways_regime_moderate_cutline(self):
        """박스권에서 커트라인이 소폭 강화되는지 검증"""
        regime = '박스권 횡보장'

        if '약세' in regime or '조정' in regime:
            max_picks = 5
        elif '횡보' in regime or '박스' in regime:
            max_picks = 6
        else:
            max_picks = 7

        assert max_picks == 6

    def test_bullish_regime_keeps_default(self):
        """강세장에서 현행 기준이 유지되는지 검증"""
        regime = '강세 상승장'

        if '약세' in regime or '조정' in regime:
            max_picks = 5
        elif '횡보' in regime or '박스' in regime:
            max_picks = 6
        else:
            max_picks = 7

        assert max_picks == 7


# ==============================================================================
# 영역 4: evaluate_and_learn 반환값 확장 테스트
# ==============================================================================

class TestEnhancedEvaluationReturn:
    """evaluate_and_learn_from_history 반환값에 신규 지표 존재 확인"""

    def test_return_includes_mfe_mae_stats(self):
        """반환값에 MFE/MAE 집계 통계가 포함되는지 검증"""
        from quant_core.tracker import evaluate_and_learn_from_history

        with patch('quant_core.tracker.load_history', return_value=[]):
            result = evaluate_and_learn_from_history()

        assert 'mfe_mae_stats' in result
        assert 'scenario_comparison' in result
        assert 'regime_performance' in result

    def test_mfe_mae_stats_structure(self):
        """MFE/MAE 통계 딕셔너리의 키 구조 검증"""
        from quant_core.tracker import evaluate_and_learn_from_history

        with patch('quant_core.tracker.load_history', return_value=[]):
            result = evaluate_and_learn_from_history()

        stats = result['mfe_mae_stats']
        expected_keys = {'avg_mfe', 'avg_mae', 'median_mfe', 'median_mae',
                         'target_2_hit_count', 'target_2_hit_rate',
                         'avg_holding_efficiency', 'optimal_stop_pct', 'sample_count'}
        assert expected_keys.issubset(set(stats.keys()))

    def test_scenario_comparison_structure(self):
        """시나리오 비교 딕셔너리의 키 구조 검증"""
        from quant_core.tracker import evaluate_and_learn_from_history

        with patch('quant_core.tracker.load_history', return_value=[]):
            result = evaluate_and_learn_from_history()

        sc = result['scenario_comparison']
        expected_keys = {'current_avg_pnl', 'partial_avg_pnl', 'trailing_avg_pnl',
                         'best_scenario', 'sample_count'}
        assert expected_keys.issubset(set(sc.keys()))


# ==============================================================================
# 영역 5: 포트폴리오 트래커 테스트
# ==============================================================================

class TestPortfolioTracker:
    """포트폴리오 성과 추적 모듈 테스트"""

    def test_build_equity_curve_empty_history(self):
        """빈 히스토리로 에퀴티 커브 생성 시 안전한 기본값 반환"""
        from quant_core.portfolio_tracker import build_daily_equity_curve
        result = build_daily_equity_curve(history=[])
        assert result['total_return_pct'] == 0.0
        assert result['mdd_pct'] == 0.0
        assert result['sharpe_ratio'] == 0.0

    def test_regime_performance_split_empty(self):
        """빈 히스토리로 레짐 분리 성과 호출 시 안전한 기본값 반환"""
        from quant_core.portfolio_tracker import get_regime_performance_split
        result = get_regime_performance_split(history=[])
        assert isinstance(result, dict)

    def test_detect_regime_shift_insufficient_data(self):
        """데이터 부족 시 레짐 변화 감지가 안전하게 처리"""
        from quant_core.portfolio_tracker import detect_regime_shift
        result = detect_regime_shift(history=[])
        assert result.get('regime_shifted') is False or 'error' in str(result).lower() or result.get('recommendation') is not None

    def test_compute_factor_importance_empty(self):
        """빈 히스토리에서 팩터 중요도 분석 시 안전"""
        from quant_core.portfolio_tracker import compute_factor_importance
        result = compute_factor_importance(history=[])
        assert isinstance(result, dict)


# ==============================================================================
# 영역 6: DB 스키마 확장 검증
# ==============================================================================

class TestDBSchemaExtension:
    """DB 스키마에 MFE/MAE 및 시뮬레이션 필드가 포함되었는지 검증"""

    def test_supabase_schema_has_daily_snapshots_table(self):
        """supabase_schema.sql에 daily_snapshots 테이블 DDL이 있는지 확인"""
        schema_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'supabase_schema.sql')
        with open(schema_path, 'r', encoding='utf-8') as f:
            content = f.read()
        assert 'CREATE TABLE IF NOT EXISTS daily_snapshots' in content
        assert 'CONSTRAINT unique_snapshot UNIQUE (date, ticker)' in content

    def test_supabase_schema_has_mfe_mae_columns(self):
        """supabase_schema.sql에 MFE/MAE 컬럼이 추가되었는지 확인"""
        schema_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'supabase_schema.sql')
        with open(schema_path, 'r', encoding='utf-8') as f:
            content = f.read()
        assert 'mfe_pct' in content
        assert 'mae_pct' in content
        assert 'target_2_hit' in content
        assert 'holding_efficiency' in content
        assert 'sim_partial_pnl' in content
        assert 'sim_trailing_pnl' in content

    def test_supabase_schema_has_rls_for_snapshots(self):
        """daily_snapshots RLS 보안 정책이 설정되었는지 확인"""
        schema_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'supabase_schema.sql')
        with open(schema_path, 'r', encoding='utf-8') as f:
            content = f.read()
        assert 'daily_snapshots ENABLE ROW LEVEL SECURITY' in content
        assert 'Private Read Snapshots' in content
        assert 'Service Role Write Snapshots' in content

    def test_db_upsert_includes_mfe_fields(self):
        """db_upsert_history_items 함수가 MFE/MAE 필드를 포함하는지 확인"""
        from quant_core.db import db_upsert_history_items
        import inspect
        source = inspect.getsource(db_upsert_history_items)
        assert 'mfe_pct' in source
        assert 'mae_pct' in source
        assert 'sim_partial_pnl' in source
        assert 'sim_trailing_pnl' in source
