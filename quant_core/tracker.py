"""
quant_core/tracker.py
퀀트 추천 종목 이력 기록, 실제 주가 성과 추적 및 규칙 기반 가중치 피드백(Feedback Loop) 모듈
"""

import os
import json
import pandas as pd
import numpy as np
import yfinance as yf
import re
import pytz
from datetime import datetime, timedelta
from typing import Dict, List, Any
from .db import db_load_history, db_upsert_history_items, is_supabase_enabled
from .data_loader import clean_ohlcv

HISTORY_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'recommendation_history.json')


CURRENT_STRATEGY_VERSION = "v1.0.0"
CURRENT_STRATEGY_RULES = {
    "min_score": 68,
    "min_rr": 1.20,
    "holding_days": 20,
    "risk_model": "dynamic_volume_profile",
    "fee_slippage_pct": 0.25
}

def get_iso_timestamp() -> str:
    """현재 시점의 ISO 8601 타임스탬프 (KST 기준) 반환"""
    try:
        kst = pytz.timezone('Asia/Seoul')
        return datetime.now(kst).isoformat()
    except Exception:
        return datetime.now().isoformat()


def get_ny_market_date_str() -> str:
    """미국 동부 뉴욕 증시(US/Eastern) 거래일 날짜 문자열 반환"""
    try:
        eastern = pytz.timezone('US/Eastern')
        return datetime.now(eastern).strftime('%Y-%m-%d')
    except Exception:
        return datetime.now().strftime('%Y-%m-%d')


def normalize_factor_tag(tag: str) -> str:
    """
    동적 수치가 포함된 태그를 정규화 버킷으로 분류하여
    통계적 표본이 안전하게 누적될 수 있도록 그룹화합니다.
    """
    m = re.search(r'월가목표\+?(-?\d+)', tag)
    if m:
        pct = int(m.group(1))
        if pct >= 25:
            return "월가괴리_25이상"
        elif pct >= 12:
            return "월가괴리_12_25"
        else:
            return "월가괴리_12미만"
            
    m_bt = re.search(r'백테스트승률(\d+)', tag)
    if m_bt:
        win = int(m_bt.group(1))
        if win >= 70:
            return "백테스트_승률70이상"
        elif win >= 55:
            return "백테스트_승률55_70"
        else:
            return "백테스트_승률55미만"
            
    return tag


def load_history() -> List[Dict[str, Any]]:
    """
    과거 추천 이력을 불러옵니다.
    - strategy_version이 없는 기존 레코드는 'legacy'로 명시 마이그레이션
    - recommendation_id 고유 식별자 보장
    1. Supabase 클라우드 DB 연결 가능 시 우선 조회
    2. 미연결 또는 빈 데이터 시 로컬 JSON 파일 폴백
    3. 로컬 데이터가 존재하고 DB가 비어있을 시 자동 클라우드 마이그레이션
    """
    def _sanitize_item(it: Dict[str, Any]) -> Dict[str, Any]:
        if not it.get('strategy_version'):
            it['strategy_version'] = 'legacy'
        if not it.get('recommendation_id'):
            it['recommendation_id'] = f"{it.get('date')}_{it.get('ticker')}_{it.get('strategy_version')}"
        if 'fee_slippage_pct' not in it:
            it['fee_slippage_pct'] = 0.25
        return it

    if is_supabase_enabled():
        db_items = db_load_history()
        if db_items:
            return [_sanitize_item(i) for i in db_items]

    # 로컬 JSON 폴백
    local_items = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                raw_items = json.load(f)
                local_items = [_sanitize_item(i) for i in raw_items]
        except Exception:
            local_items = []

    # Supabase가 활성화되어 있는데 DB가 비어있고 로컬 데이터가 있으면 즉시 클라우드로 동기화
    if is_supabase_enabled() and local_items:
        try:
            db_upsert_history_items(local_items)
        except Exception:
            pass

    return local_items


def save_history(history: List[Dict[str, Any]]):
    """
    추천 이력을 저장합니다.
    1. 항상 로컬 JSON 파일에 1차 안전 보존 (로컬 백업)
    2. Supabase 클라우드 DB 활성화 시 PostgreSQL 테이블에 동기화 업서트
    """
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"로컬 추천 이력 저장 실패: {e}")

    if is_supabase_enabled():
        try:
            db_upsert_history_items(history)
        except Exception as e:
            print(f"Supabase 클라우드 DB 동기화 실패: {e}")


def record_daily_recommendations(recs: List[Dict[str, Any]]):
    """
    오늘 퀀트 엔진이 선별한 추천 종목들을 성과 추적 데이터베이스에 기록합니다.
    (동일 날짜·종목이라도 전략 버전이 다르면 고유 저장 지원: date + ticker + strategy_version)
    """
    history = load_history()
    today_str = get_ny_market_date_str()
    existing_keys = {
        f"{item['date']}_{item['ticker']}_{item.get('strategy_version', 'legacy')}"
        for item in history
    }
    
    added_count = 0
    for r in recs:
        rec_date_val = r.get('date') or today_str
        strat_ver = r.get('strategy_version') or CURRENT_STRATEGY_VERSION
        rec_id = f"{rec_date_val}_{r['ticker']}_{strat_ver}"

        if rec_id not in existing_keys:
            history.append({
                'recommendation_id': rec_id,
                'strategy_version': strat_ver,
                'recommended_at': r.get('recommended_at', get_iso_timestamp()),
                'market_date': rec_date_val,
                'date': rec_date_val,
                'ticker': r['ticker'],
                'name': r['name'],
                'category': r.get('category', ''),
                'rec_price': r['current_price'],
                'target_price': r.get('bull_target_1', r['current_price'] * 1.08),
                'target_price_2': r.get('bull_target_2', r['current_price'] * 1.15),
                'stop_loss': r.get('stop_loss', r['current_price'] * 0.94),
                'expected_upside': r.get('upside_pct', 15.0),
                'ai_score': r.get('total_score', 80),
                'tags': r.get('tags', []),
                'pattern_status': r.get('pattern_status', '진입 적기'),
                'rules': r.get('rules', CURRENT_STRATEGY_RULES.copy()),
                'market_context': r.get('market_context', {'regime': '정상장세'}),
                'fee_slippage_pct': float(r.get('fee_slippage_pct', 0.25)),
                'status': 'HOLD',  # HOLD, WIN_TARGET1, WIN_TARGET2, STOP_LOSS
                'max_price': r['current_price'],
                'current_price': r['current_price'],
                'current_pnl_pct': 0.0,
                'realized_pnl_pct': None,
                'realized_pnl_net_pct': None,
                'hit_success': False,
                'is_completed': False
            })
            existing_keys.add(rec_id)
            added_count += 1
            
    if added_count > 0:
        save_history(history)
    return history


def diagnose_failure_reason(item: Dict[str, Any], df_after: pd.DataFrame, df_full: pd.DataFrame) -> Dict[str, Any]:

    """
    예측 실패(손절선 이탈 또는 손실 마감) 종목의 기술적 실패 원인을 분석합니다.
    """
    reasons = []
    try:
        if len(df_after) >= 1 and len(df_full) >= 20:
            avg_vol_20 = float(df_full['Volume'].iloc[-20:].mean())
            break_vol = float(df_after['Volume'].iloc[0])
            if break_vol < avg_vol_20 * 0.8:
                reasons.append("돌파 시 거래량 결핍으로 인한 가짜 돌파(Bull Trap)")
    except Exception:
        pass
        
    try:
        if 'Close' in df_after and len(df_after) >= 1:
            last_close = float(df_after['Close'].iloc[-1])
            if last_close < item['stop_loss']:
                reasons.append(f"기술적 손절선(${item['stop_loss']:.2f}) 하방 이탈")
    except Exception:
        pass

    primary_tag = item.get('tags', ['기술적반등'])[0] if item.get('tags') else '기술적반등'
    if not reasons:
        reasons.append(f"'{primary_tag}' 패턴 지지선 붕괴 및 단기 차익 실현 매물 출회")
        
    diagnosis_text = " · ".join(reasons)
    return {
        'primary_cause': reasons[0],
        'full_diagnosis': diagnosis_text,
        'failed_tag': primary_tag
    }


def get_adaptive_factor_weights(strategy_version: str = CURRENT_STRATEGY_VERSION) -> Dict[str, Any]:
    """
    과거 추천 이력의 성공/실패 데이터를 바탕으로:
    1. 각 팩터별 승률 및 가중치 보너스(+)/페널티(-) 계산 (1종목당 중복 태그 원천 제거)
    2. 최근 14일 내 손절 이탈 종목(쿨다운 대상) 식별
    3. 전략 버전(strategy_version)별 분리 필터링 적용
    """
    history = load_history()
    if not history:
        return {
            'strategy_version': strategy_version,
            'factor_adjustments': {},
            'cooldown_tickers': {},
            'failure_notes': [],
            'boosted_factors': [],
            'penalized_factors': []
        }

    factor_stats = {}
    cooldown_tickers = {}
    failure_notes = []
    today_date = datetime.now().date()

    for item in history:
        t = item['ticker']
        is_completed = item.get('is_completed', False)
        hit_success = item.get('hit_success', False)
        rec_date_str = item.get('date', '')
        
        try:
            rec_date = datetime.strptime(rec_date_str, '%Y-%m-%d').date()
            days_ago = (today_date - rec_date).days
        except Exception:
            days_ago = 999

        # 최근 14일(약 10거래일) 이내 손절 이탈 종목 쿨다운 등록 (포트폴리오 리스크 방어망: 전체 이력 대상)
        if is_completed and not hit_success and days_ago <= 14:
            cooldown_tickers[t] = {
                'ticker': t,
                'name': item.get('name', t),
                'date': rec_date_str,
                'days_ago': days_ago,
                'reason': item.get('failure_reason', '손절선 이탈'),
                'penalty': -12
            }
            if item.get('failure_reason'):
                failure_notes.append({
                    'ticker': t,
                    'name': item.get('name', t),
                    'date': rec_date_str,
                    'reason': item.get('failure_reason'),
                    'tag': item.get('tags', [''])[0]
                })

        # 전략 버전 분리: 요청된 전략 버전(v1.0.0 등)만 선별 집계 (서로 다른 룰셋 간 팩터 통계 오염 방지)
        if strategy_version and strategy_version != 'all':
            item_ver = item.get('strategy_version', 'legacy')
            if item_ver != strategy_version:
                continue

        # 팩터별 성패 집계 (정규화 버킷 적용 & 1추천당 중복 태그 set으로 원천 제거)
        if is_completed:
            normalized_tags = {
                normalize_factor_tag(tag)
                for tag in item.get('tags', [])
                if tag
            }
            for norm_tag in normalized_tags:
                if norm_tag not in factor_stats:
                    factor_stats[norm_tag] = {'total': 0, 'wins': 0, 'losses': 0}
                factor_stats[norm_tag]['total'] += 1
                if hit_success:
                    factor_stats[norm_tag]['wins'] += 1
                else:
                    factor_stats[norm_tag]['losses'] += 1

    # 팩터 가중치 조정값 산출 (통계적 과적합 방지: 표본 단계별 보정 상한 적용)
    # - 0~29건: 실험 단계, 가중치 변경 금지 (0점 고정)
    # - 30~99건: 참고 단계 (오차범위 ±18%p) -> 최대 ±2점 극히 제한 보정
    # - 100~299건: 1차 평가 가능 단계 (오차범위 ±10%p) -> 최대 ±5점 제한 보정
    # - 300건 이상: 통계적 안정 단계 -> 전체 반영
    factor_adjustments = {}
    boosted_factors = []
    penalized_factors = []

    for tag, stats in factor_stats.items():
        total = stats['total']
        if total < 30:
            # 0~29건: 실험 단계, 가중치 동결
            factor_adjustments[tag] = 0
            continue

        win_rate = (stats['wins'] / total) * 100
        if win_rate >= 70:
            raw_adj = 5 if win_rate >= 80 else 3
        elif win_rate <= 40:
            raw_adj = -10 if win_rate <= 25 else -6
        else:
            raw_adj = 0

        if total < 100:
            # 30~99건: 참고 단계 (오차 ±18%p, ±2점 제한)
            adj = max(-2, min(2, raw_adj))
        elif total < 300:
            # 100~299건: 1차 평가 단계 (오차 ±10%p, ±5점 제한)
            adj = max(-5, min(5, raw_adj))
        else:
            # 300건 이상: 통계적 안정 단계
            adj = raw_adj

        factor_adjustments[tag] = adj
        if adj > 0:
            boosted_factors.append({
                'tag': tag,
                'win_rate': round(win_rate, 1),
                'adj': f"+{adj}점 (표본 {total}건)",
                'samples': total
            })
        elif adj < 0:
            penalized_factors.append({
                'tag': tag,
                'win_rate': round(win_rate, 1),
                'adj': f"{adj}점 (표본 {total}건)",
                'samples': total
            })

    return {
        'strategy_version': strategy_version,
        'factor_adjustments': factor_adjustments,
        'cooldown_tickers': cooldown_tickers,
        'failure_notes': failure_notes[-5:],
        'boosted_factors': boosted_factors,
        'penalized_factors': penalized_factors
    }


def evaluate_and_learn_from_history(strategy_version: str = CURRENT_STRATEGY_VERSION) -> Dict[str, Any]:
    """
    과거에 추천했던 모든 종목들의 실제 주가를 야후 파이낸스로 재조회하여:
    1. 실제 목표가 도달/손절선 이탈 여부 실시간 판정
    2. 전략 버전(strategy_version)별 독립 성과 집계 (v1.0.0, legacy 분리)
    3. 팩터별 실시간 승률(태그 중복 제거) 및 기대값/Profit Factor 산출
    """
    history = load_history()
    if not history:
        return {
            'strategy_version': strategy_version,
            'selected_version': strategy_version,
            'available_versions': [CURRENT_STRATEGY_VERSION],
            'total_recs': 0,
            'completed_count': 0,
            'ongoing_count': 0,
            'wins': 0,
            'losses': 0,
            'win_rate': 0.0,
            'avg_return': 0.0,
            'avg_return_net': 0.0,
            'benchmark_ticker': 'QQQ',
            'avg_benchmark_return': None,
            'avg_excess_return': None,
            'alpha_win_rate': None,
            'alpha_win_rate_lb': None,
            'excess_tstat': None,
            'alpha_is_significant': False,
            'benchmark_samples': 0,
            'avg_win': 0.0,
            'avg_loss': 0.0,
            'expected_value': 0.0,
            'profit_factor': 0.0,
            'profit_factor_display': "0.00",
            'sample_tier': "실험 단계 (0~29건)",
            'sample_tier_desc': "표본 축적 단계로, 통계적 왜곡 방지를 위해 가중치 변경이 동결되어 있습니다.",
            'sample_error_margin': "±20%p 이상",
            'history_items': [],
            'best_factors': [],
            'adaptive_weights': get_adaptive_factor_weights(strategy_version=strategy_version)
        }

    # 보유 중인 종목들의 최신 시세 일괄 수집
    unique_tickers = list(set([item['ticker'] for item in history if not item.get('is_completed')]))
    today_str = datetime.now().strftime('%Y-%m-%d')
    today_date = datetime.now().date()

    stock_dfs = {}
    for t in unique_tickers:
        try:
            df = clean_ohlcv(yf.Ticker(t).history(period='3mo', interval='1d'))
            if df is not None and not df.empty:
                stock_dfs[t] = df
        except Exception:
            pass

    # 1. 진행 중인 종목들의 목표가/손절가 도달 판정 업데이트
    for item in history:
        if item.get('is_completed'):
            continue

        t = item['ticker']
        rec_p = float(item.get('rec_price', 0.0))
        t1 = float(item.get('target_price', 0.0)) if item.get('target_price') else 0.0
        sl = float(item.get('stop_loss', 0.0)) if item.get('stop_loss') else 0.0
        rec_date_str = item.get('date', today_str)
        try:
            rec_date = datetime.strptime(rec_date_str, '%Y-%m-%d').date()
        except Exception:
            rec_date = today_date

        if t in stock_dfs:
            df = stock_dfs[t]
            curr_p = float(df['Close'].iloc[-1])
            item['current_price'] = round(curr_p, 2)
            
            pnl_pct = ((curr_p / rec_p) - 1) * 100
            item['current_pnl_pct'] = round(pnl_pct, 2)
            
            df_after = df[df.index.date > rec_date].sort_index()
            
            if df_after.empty:
                item['status'] = '⏳ 실시간 추적 중 (오늘 등록)'
                item['hit_success'] = False
                item['is_completed'] = False
            else:
                after_high = float(df_after['High'].max())
                item['max_price'] = round(after_high, 2)
                
                resolved = False
                for bar_date, row in df_after.iterrows():
                    bar_open = float(row['Open'])
                    bar_high = float(row['High'])
                    bar_low = float(row['Low'])
                    fee_pct = float(item.get('fee_slippage_pct', 0.25))

                    # t1/sl이 0인(미설정) 경우 해당 배리어는 비활성 처리
                    t_active = t1 > 0
                    s_active = sl > 0

                    hit_t = t_active and bar_high >= t1
                    hit_s = s_active and bar_low <= sl

                    # ── 청산가 결정 (갭 체결 반영) ─────────────────────────────
                    # 시가가 이미 배리어를 넘겨 출발한 경우 지정가가 아니라 시가로 체결된다.
                    # 기존 코드는 무조건 sl/t1로 기록해 갭하락 손실을 체계적으로 과소계상했음.
                    exit_kind = None
                    exit_p = None
                    is_gap = False

                    if t_active and bar_open >= t1:
                        exit_kind, exit_p, is_gap = 'TARGET', bar_open, True
                    elif s_active and bar_open <= sl:
                        exit_kind, exit_p, is_gap = 'STOP', bar_open, True
                    elif hit_t and hit_s:
                        # 동일봉 동시 도달 -> 보수적 손절 우선
                        exit_kind, exit_p = 'STOP_SAMEBAR', sl
                    elif hit_s:
                        exit_kind, exit_p = 'STOP', sl
                    elif hit_t:
                        exit_kind, exit_p = 'TARGET', t1

                    if exit_kind is not None:
                        realized_pnl = round(((exit_p / rec_p) - 1) * 100, 2)
                        item['is_completed'] = True
                        item['exit_price'] = round(exit_p, 2)
                        item['exit_date'] = str(bar_date.date())
                        item['exit_kind'] = exit_kind
                        item['exit_gapped'] = is_gap
                        item['realized_pnl_pct'] = realized_pnl
                        item['realized_pnl_net_pct'] = round(realized_pnl - fee_pct, 2)

                        if exit_kind == 'TARGET':
                            item['hit_success'] = True
                            item['status'] = '🎯 목표가 갭 도달 (성공)' if is_gap else '🎯 목표가 도달 (성공)'
                            item['failure_reason'] = None
                        else:
                            item['hit_success'] = False
                            if exit_kind == 'STOP_SAMEBAR':
                                item['status'] = '⚠️ 변동성 손절 이탈 (동일봉 도달)'
                            elif is_gap:
                                item['status'] = '⚠️ 갭하락 손절 이탈 (시가 체결)'
                            else:
                                item['status'] = '⚠️ 손절선 이탈'
                            diagnosis = diagnose_failure_reason(item, df_after.loc[:bar_date], df)
                            extra = ''
                            if is_gap:
                                extra = f" · 손절선(${sl:.2f}) 미체결 갭하락으로 시가 ${exit_p:.2f} 체결"
                            item['failure_reason'] = diagnosis['full_diagnosis'] + extra

                        resolved = True
                        break


                if not resolved:
                    if len(df_after) >= 20:
                        exit_p = curr_p
                        realized_pnl = round(((exit_p / rec_p) - 1) * 100, 2)
                        fee_pct = float(item.get('fee_slippage_pct', 0.25))
                        item['is_completed'] = True
                        item['exit_price'] = round(exit_p, 2)
                        item['exit_date'] = str(df_after.index[-1].date())
                        item['realized_pnl_pct'] = realized_pnl
                        item['realized_pnl_net_pct'] = round(realized_pnl - fee_pct, 2)
                        item['hit_success'] = (exit_p >= rec_p)
                        item['status'] = '기간 만료 마감'
                        if not item['hit_success']:
                            diagnosis = diagnose_failure_reason(item, df_after, df)
                            item['failure_reason'] = diagnosis['full_diagnosis']
                        else:
                            item['failure_reason'] = None
                    else:
                        item['status'] = f'📈 보유 {len(df_after)}일차 추적 중'
                        item['hit_success'] = False
                        item['is_completed'] = False

    save_history(history)

    # 2. 존재하는 모든 전략 버전 식별
    available_versions = sorted(list({item.get('strategy_version', 'legacy') for item in history}))
    if CURRENT_STRATEGY_VERSION not in available_versions:
        available_versions.insert(0, CURRENT_STRATEGY_VERSION)

    # 3. 요청된 전략 버전 필터링 (버전별 독립 평가)
    if strategy_version and strategy_version != 'all':
        eval_items = [it for it in history if it.get('strategy_version', 'legacy') == strategy_version]
    else:
        eval_items = history

    completed_items = [it for it in eval_items if it.get('is_completed', False)]
    completed_samples = len(completed_items)
    ongoing_count = len([it for it in eval_items if not it.get('is_completed', False)])
    wins = len([it for it in completed_items if it.get('hit_success', False)])
    losses = completed_samples - wins

    total_pnls = [float(it['realized_pnl_pct']) for it in completed_items if it.get('realized_pnl_pct') is not None]
    total_net_pnls = []
    fee_list = []
    for it in completed_items:
        fee = float(it.get('fee_slippage_pct', 0.25))
        fee_list.append(fee)
        pnl = it.get('realized_pnl_pct')
        if pnl is not None:
            total_net_pnls.append(pnl - fee)

    avg_fee = float(np.mean(fee_list)) if fee_list else 0.25
    win_rate = (wins / completed_samples * 100) if completed_samples > 0 else 0.0
    avg_return = float(np.mean(total_pnls)) if total_pnls else 0.0
    avg_return_net = float(np.mean(total_net_pnls)) if total_net_pnls else 0.0

    # ── 벤치마크(QQQ) 대비 초과수익 집계 ────────────────────────────────
    # 상승장 유니버스에서는 절대 승률 대부분이 시장 베타다.
    # '같은 기간 그냥 지수를 샀을 때보다 나았는가'를 별도 지표로 산출한다.
    benchmark_series = None
    try:
        from .screener import get_benchmark_series, benchmark_return_between, wilson_lower_bound
        candidate = get_benchmark_series(period='2y')
        if candidate is not None and len(candidate) >= 50:
            benchmark_series = candidate
    except Exception:
        benchmark_series = None

    excess_list = []
    bench_list = []
    if benchmark_series is not None:
        for it in completed_items:
            pnl = it.get('realized_pnl_pct')
            entry_d = it.get('date')
            exit_d = it.get('exit_date')
            if pnl is None or not entry_d or not exit_d:
                continue
            b_ret = benchmark_return_between(benchmark_series, entry_d, exit_d)
            if b_ret is None:
                continue
            fee = float(it.get('fee_slippage_pct', 0.25))
            excess = (pnl - fee) - b_ret
            it['benchmark_return_pct'] = round(b_ret, 2)
            it['excess_return_pct'] = round(excess, 2)
            bench_list.append(b_ret)
            excess_list.append(excess)

    if excess_list:
        alpha_wins = sum(1 for e in excess_list if e > 0)
        alpha_win_rate = round((alpha_wins / len(excess_list)) * 100, 1)
        alpha_win_rate_lb = round(wilson_lower_bound(alpha_wins, len(excess_list)), 1)
        avg_excess_return = round(float(np.mean(excess_list)), 2)
        avg_benchmark_return = round(float(np.mean(bench_list)), 2)
        # 초과수익의 통계적 유의성 (|t| > 1.96 이면 95% 유의)
        if len(excess_list) >= 2:
            se = float(np.std(excess_list, ddof=1)) / np.sqrt(len(excess_list))
            excess_tstat = round(avg_excess_return / se, 2) if se > 0 else 0.0
        else:
            excess_tstat = 0.0
        alpha_is_significant = bool(abs(excess_tstat) > 1.96)
    else:
        alpha_win_rate = None
        alpha_win_rate_lb = None
        avg_excess_return = None
        avg_benchmark_return = None
        excess_tstat = None
        alpha_is_significant = False

    # 팩터별 승률 집계 (1추천당 중복 태그 set으로 원천 제거)
    factor_hits = {}
    for it in completed_items:
        normalized_tags = {
            normalize_factor_tag(tag)
            for tag in it.get('tags', [])
            if tag
        }
        for norm_tag in normalized_tags:
            if norm_tag not in factor_hits:
                factor_hits[norm_tag] = {'total': 0, 'wins': 0}
            factor_hits[norm_tag]['total'] += 1
            if it.get('hit_success', False):
                factor_hits[norm_tag]['wins'] += 1

    win_pnls = [p for p in total_pnls if p > 0]
    loss_pnls = [p for p in total_pnls if p <= 0]
    avg_win = float(np.mean(win_pnls)) if win_pnls else 0.0
    avg_loss = float(np.mean(loss_pnls)) if loss_pnls else 0.0

    sum_win = float(sum(win_pnls)) if win_pnls else 0.0
    sum_loss = float(abs(sum(loss_pnls))) if loss_pnls else 0.0

    if sum_loss > 0:
        profit_factor = round(sum_win / sum_loss, 2)
        profit_factor_display = f"{profit_factor:.2f}"
    elif sum_win > 0:
        profit_factor = 99.9
        profit_factor_display = "손실 없음 (∞)"
    else:
        profit_factor = 0.0
        profit_factor_display = "0.00"

    # 기대값 산출 (거래비용 avg_fee 동적 차감)
    if completed_samples > 0:
        p_win = wins / completed_samples
        p_loss = losses / completed_samples
        expected_value = round((p_win * avg_win) - (p_loss * abs(avg_loss)) - avg_fee, 2)
    else:
        expected_value = 0.0

    # 표본 수에 따른 통계적 신뢰도 단계
    if completed_samples < 30:
        sample_tier = "실험 단계 (0~29건)"
        sample_tier_desc = "표본 축적 단계로, 통계적 왜곡 방지를 위해 가중치 변경이 동결되어 있습니다."
        sample_error_margin = "±20%p 이상"
    elif completed_samples < 100:
        sample_tier = "참고 단계 (30~99건)"
        sample_tier_desc = "성과 추세 파악이 가능하며, 팩터 가중치는 최대 ±2점으로 극히 제한 보정됩니다."
        sample_error_margin = "대략 ±18%p"
    elif completed_samples < 300:
        sample_tier = "1차 평가 단계 (100~299건)"
        sample_tier_desc = "전략의 유효성 검증이 가능하며, 팩터 가중치는 최대 ±5점으로 제한 보정됩니다."
        sample_error_margin = "대략 ±10%p"
    else:
        sample_tier = "통계적 안정 단계 (300건 이상)"
        sample_tier_desc = "시장 환경별 세부 분석 및 가중치 능동 보정이 유효한 단계입니다."
        sample_error_margin = "±5%p 이내"

    best_factors = []
    for tag, counts in factor_hits.items():
        if counts['total'] >= 1:
            f_rate = (counts['wins'] / counts['total']) * 100
            best_factors.append({
                'factor': tag,
                'win_rate': round(f_rate, 1),
                'count': counts['total']
            })
    best_factors.sort(key=lambda x: x['win_rate'], reverse=True)

    adaptive_weights = get_adaptive_factor_weights(strategy_version=strategy_version)

    return {
        'strategy_version': CURRENT_STRATEGY_VERSION,
        'strategy_rules': CURRENT_STRATEGY_RULES,
        'selected_version': strategy_version,
        'available_versions': available_versions,
        'total_recs': len(eval_items),
        'completed_count': completed_samples,
        'ongoing_count': ongoing_count,
        'wins': wins,
        'losses': losses,
        'win_rate': round(win_rate, 1),
        'avg_return': round(avg_return, 1),
        'avg_return_net': round(avg_return_net, 1),
        'benchmark_ticker': 'QQQ',
        'avg_benchmark_return': avg_benchmark_return,
        'avg_excess_return': avg_excess_return,
        'alpha_win_rate': alpha_win_rate,
        'alpha_win_rate_lb': alpha_win_rate_lb,
        'excess_tstat': excess_tstat,
        'alpha_is_significant': alpha_is_significant,
        'benchmark_samples': len(excess_list),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'expected_value': expected_value,
        'profit_factor': profit_factor,
        'profit_factor_display': profit_factor_display,
        'sample_tier': sample_tier,
        'sample_tier_desc': sample_tier_desc,
        'sample_error_margin': sample_error_margin,
        'history_items': eval_items[::-1],
        'best_factors': best_factors[:4],
        'adaptive_weights': adaptive_weights
    }

