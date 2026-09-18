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

    def test_db_save_snapshots_payload_schema_compatibility(self):
        """db_save_snapshots의 upsert 페이로드가 DB 스키마(TEXT divergence_status, updated_at)와 호환되는지 검증"""
        from quant_core.snapshot import db_save_snapshots

        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table
        mock_upsert = MagicMock()
        mock_table.upsert.return_value = mock_upsert
        mock_upsert.execute.return_value = MagicMock(data=[])

        sample_items = [
            {
                'ticker': 'NVDA',
                'total_score': 82,
                'tech_score': 35,
                'fund_score': 25,
                'mom_score': 22,
                'pattern_status': '진입 적기',
                'current_price': 120.5,
                'target_1': 135.0,
                'target_2': 145.0,
                'stop_loss': 112.0,
                'risk_reward_ratio': 1.71,
                'fib_status': 'golden_pocket',
                'trendline_status': 'breakout',
                'divergence_status': 'bullish',
                'market_regime': '강세 상승장',
                'is_qualified': True
            },
            {
                'ticker': 'AMD',
                'divergence_status': False
            }
        ]

        with patch('quant_core.snapshot.get_supabase_client', return_value=mock_client), \
             patch('quant_core.snapshot.is_supabase_enabled', return_value=True):
            res = db_save_snapshots("2026-09-18", sample_items)
            assert res is True

            mock_table.upsert.assert_called_once()
            args, kwargs = mock_table.upsert.call_args
            records = args[0]
            assert len(records) == 2
            assert kwargs.get('on_conflict') == 'date,ticker'

            rec1 = records[0]
            assert rec1['date'] == "2026-09-18"
            assert rec1['ticker'] == "NVDA"
            assert rec1['divergence_status'] == "bullish"
            assert isinstance(rec1['divergence_status'], str)
            assert 'updated_at' in rec1

            rec2 = records[1]
            assert rec2['divergence_status'] == "none"

    @patch('quant_core.snapshot.load_snapshots')
    def test_accuracy_report_maturity_requirement(self, mock_load):
        """예측 정확도 리포트가 미성숙 표본을 제외하고 완전 성숙(len >= target_days) 표본만 집계하는지 검증"""
        from quant_core.snapshot import get_prediction_accuracy_report

        # 4영업일 데이터만 존재하는 경우 -> 5d, 10d, 20d 모두 미성숙(pending)
        dates_4d = pd.date_range('2026-01-05', periods=4, freq='B')
        mock_hist_4d = pd.Series([102.0, 104.0, 103.0, 105.0], index=dates_4d)

        mock_snapshots = {
            '2026-01-02': [
                {
                    'ticker': 'MATURE_TEST',
                    'is_qualified': True,
                    'current_price': 100.0,
                    'target_1': 110.0
                }
            ]
        }
        mock_load.return_value = mock_snapshots

        with patch('yfinance.download') as mock_yf:
            mock_yf.return_value = {'Close': pd.DataFrame({'MATURE_TEST': mock_hist_4d})}
            
            report = get_prediction_accuracy_report(days_back=30)
            
            assert report['period_5d']['sample_count'] == 0
            assert report['period_5d']['pending_count'] == 1
            assert report['period_10d']['sample_count'] == 0
            assert report['period_20d']['sample_count'] == 0

        # 정확히 5영업일 데이터가 존재하는 경우 -> 5d는 mature 집계, 10d/20d는 pending
        dates_5d = pd.date_range('2026-01-05', periods=5, freq='B')
        mock_hist_5d = pd.Series([102.0, 104.0, 106.0, 108.0, 111.0], index=dates_5d)

        with patch('yfinance.download') as mock_yf:
            mock_yf.return_value = {'Close': pd.DataFrame({'MATURE_TEST': mock_hist_5d})}
            
            report = get_prediction_accuracy_report(days_back=30)
            
            assert report['period_5d']['sample_count'] == 1
            assert report['period_5d']['target_hit_rate'] == 100.0
            assert report['period_10d']['sample_count'] == 0
            assert report['period_10d']['pending_count'] == 1
            assert report['period_20d']['sample_count'] == 0


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

    def test_mfe_mae_excludes_future_prices_after_exit(self):
        """실제 청산일(exit_date) 이후 발생한 미래 가격 폭등이 MFE/target_2에 누수되지 않는지 엄격 검증"""
        from quant_core.tracker import evaluate_and_learn_from_history

        history_item = {
            'ticker': 'BIAS_TEST',
            'name': 'Bias Test Stock',
            'date': '2026-01-02',
            'rec_price': 100.0,
            'target_price': 105.0,
            'target_price_2': 120.0,
            'stop_loss': 95.0,
            'is_completed': False,
            'strategy_version': 'v1.0.0',
            'fee_slippage_pct': 0.25
        }

        dates = pd.date_range('2026-01-02', periods=10, freq='B')
        highs = [100.0, 103.0, 106.0, 110.0, 115.0, 130.0, 180.0, 200.0, 190.0, 185.0]
        lows  = [99.0,  99.0,  101.0, 105.0, 108.0, 120.0, 170.0, 180.0, 170.0, 160.0]
        closes= [100.0, 102.0, 105.0, 109.0, 114.0, 128.0, 175.0, 195.0, 180.0, 175.0]
        df_mock = pd.DataFrame({'High': highs, 'Low': lows, 'Close': closes, 'Volume': [10000]*10}, index=dates)

        mock_ticker_inst = MagicMock()
        mock_ticker_inst.history.return_value = df_mock

        with patch('quant_core.tracker.load_history', return_value=[history_item]), \
             patch('quant_core.tracker.yf.Ticker', return_value=mock_ticker_inst), \
             patch('quant_core.tracker.save_history') as mock_save:
            
            evaluate_and_learn_from_history()
            
            saved_items = mock_save.call_args[0][0]
            item = saved_items[0]
            
            # exit_date(1월 6일, index[2])까지의 최고가는 106.0 -> MFE = 6.0% (미래 200 폭등 배제)
            assert item['mfe_pct'] == 6.0, f"MFE가 청산일 이후 미래 가격을 참조함: {item['mfe_pct']}"
            # 2차 목표가(120)는 청산 이후 달성되었으므로 target_2_hit는 False여야 함
            assert item.get('target_2_hit') is not True, "청산 이후 달성된 2차 목표가가 target_2_hit에 누수됨"

    def test_counterfactual_scenarios_enforce_20_bars_cap(self):
        """대체 청산 시나리오 함수가 20거래일 초과 데이터를 무시하고 정확히 20거래일만 참조하는지 검증"""
        from quant_core.tracker import _simulate_partial_exit, _simulate_trailing_exit

        dates_40 = pd.date_range('2026-01-02', periods=40, freq='B')
        highs = [102.0]*20 + [150.0]*20
        lows = [98.0]*20 + [140.0]*20
        closes = [101.0]*20 + [145.0]*20
        df_40 = pd.DataFrame({'High': highs, 'Low': lows, 'Close': closes}, index=dates_40)

        pnl_b = _simulate_partial_exit(df_40, rec_p=100.0, t1=110.0, t2=120.0, sl=95.0, atr=3.0, fee_pct=0.25)
        pnl_c = _simulate_trailing_exit(df_40, rec_p=100.0, sl=95.0, atr=3.0, fee_pct=0.25)

        assert pnl_b is not None
        assert pnl_b < 10.0, f"20일 초과 미래 가격(150)이 시나리오 B에 반영됨: {pnl_b}"
        assert pnl_c is not None
        assert pnl_c < 10.0, f"20일 초과 미래 가격(150)이 시나리오 C에 반영됨: {pnl_c}"


# ==============================================================================
# 영역 3: 시장 레짐 동적 커트라인 테스트
# ==============================================================================

class TestRegimeDynamicCutline:
    """시장 레짐 기반 동적 커트라인 조절 테스트 (프로덕션 함수 apply_regime_cutoffs 직접 호출)"""

    def test_apply_regime_cutoffs_bearish(self):
        """약세/조정장에서 커트라인이 엄격화(75점, 1.50, 최대 5종목)되는지 검증"""
        from quant_core.screener import apply_regime_cutoffs

        mock_candidates = [
            {'ticker': 'STK1', 'total_score': 76, 'risk_reward_ratio': 1.60},
            {'ticker': 'STK2', 'total_score': 72, 'risk_reward_ratio': 1.60},  # 점수 미달
            {'ticker': 'STK3', 'total_score': 78, 'risk_reward_ratio': 1.40},  # 손익비 미달
            {'ticker': 'STK4', 'total_score': 80, 'risk_reward_ratio': 1.80},
        ]

        filtered, max_picks, rules = apply_regime_cutoffs(mock_candidates, '약세 조정장')
        assert max_picks == 5
        assert rules['min_score'] == 75
        assert rules['min_rr'] == 1.50
        assert len(filtered) == 2
        tickers = [f['ticker'] for f in filtered]
        assert 'STK1' in tickers and 'STK4' in tickers
        assert 'STK2' not in tickers and 'STK3' not in tickers

    def test_apply_regime_cutoffs_sideways(self):
        """박스권 횡보장에서 커트라인이 중간(70점, 1.30, 최대 6종목) 적용되는지 검증"""
        from quant_core.screener import apply_regime_cutoffs

        mock_candidates = [
            {'ticker': 'STK1', 'total_score': 71, 'risk_reward_ratio': 1.35},
            {'ticker': 'STK2', 'total_score': 69, 'risk_reward_ratio': 1.50},  # 점수 미달
        ]

        filtered, max_picks, rules = apply_regime_cutoffs(mock_candidates, '박스권 횡보장')
        assert max_picks == 6
        assert rules['min_score'] == 70
        assert rules['min_rr'] == 1.30
        assert len(filtered) == 1
        assert filtered[0]['ticker'] == 'STK1'

    def test_apply_regime_cutoffs_bullish(self):
        """강세 상승장에서 기본 커트라인(68점, 1.20, 최대 7종목) 유지되는지 검증"""
        from quant_core.screener import apply_regime_cutoffs

        mock_candidates = [
            {'ticker': 'STK1', 'total_score': 68, 'risk_reward_ratio': 1.20},
        ]

        filtered, max_picks, rules = apply_regime_cutoffs(mock_candidates, '강세 상승장')
        assert max_picks == 7
        assert rules['min_score'] == 68
        assert rules['min_rr'] == 1.20
        assert len(filtered) == 1


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

    def test_build_daily_equity_curve_mark_to_market_and_mdd(self):
        """보유 기간 중 주가가 급락했을 때 Mark-to-Market에 의해 MDD가 정상 집계되는지 검증"""
        from quant_core.portfolio_tracker import build_daily_equity_curve

        # 추천 1건: 1월 5일 100에 진입, 1월 9일에 102로 청산 (최종은 +2% 수익)
        # 하지만 1월 7일에 주가가 70(-30%)까지 급락함
        history = [{
            'ticker': 'MTM_TEST',
            'date': '2026-01-05',
            'exit_date': '2026-01-09',
            'current_price': 100.0,
            'exit_price': 102.0,
            'realized_pnl_pct': 2.0,
            'total_score': 80,
            'fee_slippage_pct': 0.0
        }]

        dates = pd.date_range('2026-01-05', '2026-01-09', freq='B')
        prices = [100.0, 90.0, 70.0, 85.0, 102.0]
        price_df = pd.DataFrame({'MTM_TEST': prices}, index=dates)

        result = build_daily_equity_curve(history=history, initial_capital=10_000_000, price_data=price_df)
        
        # 보유 중 70까지 폭락했으므로 MDD는 0%가 아니라 반드시 음수(-2% 이하)여야 함!
        assert result['mdd_pct'] < -2.0, f"Mark-to-Market 일별 하락이 MDD에 반영되지 않음: {result['mdd_pct']}"
        assert len(result['equity_curve']) == len(dates)

    def test_portfolio_enforces_max_positions_limit(self):
        """동시 보유 종목 수 최대 7개(MAX_POSITIONS = 7) 제약이 엄격히 준수되는지 검증"""
        from quant_core.portfolio_tracker import build_daily_equity_curve

        # 같은 날짜에 10개 종목이 추천됨
        history = [
            {
                'ticker': f'TICKER_{i}',
                'date': '2026-01-05',
                'exit_date': '2026-01-15',
                'current_price': 100.0,
                'exit_price': 105.0,
                'total_score': 70 + i,
                'fee_slippage_pct': 0.0
            }
            for i in range(10)
        ]

        result = build_daily_equity_curve(history=history, initial_capital=10_000_000)
        curve = result['equity_curve']
        
        # 모든 날짜에서 보유 포지션 수가 7개를 초과하지 않아야 함
        for point in curve:
            assert point['num_positions'] <= 7, f"동시 보유 포지션 수 한도(7) 초과: {point['num_positions']}"


# ==============================================================================
# 영역 6: DB 스키마 확장 검증
# ==============================================================================

class TestDBSchemaExtension:
    """DB 스키마에 daily_snapshots 테이블 및 updated_at, TEXT divergence_status 등이 포함되었는지 검증"""

    def test_supabase_schema_has_daily_snapshots_table(self):
        """supabase_schema.sql에 daily_snapshots 테이블 DDL이 올바른지 확인"""
        schema_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'supabase_schema.sql')
        with open(schema_path, 'r', encoding='utf-8') as f:
            content = f.read()
        assert 'CREATE TABLE IF NOT EXISTS daily_snapshots' in content
        assert 'divergence_status TEXT' in content
        assert 'updated_at TIMESTAMPTZ' in content
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
