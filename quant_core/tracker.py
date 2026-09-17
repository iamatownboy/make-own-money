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
    1. Supabase 클라우드 DB 연결 가능 시 우선 조회
    2. 미연결 또는 빈 데이터 시 로컬 JSON 파일 폴백
    3. 로컬 데이터가 존재하고 DB가 비어있을 시 자동 클라우드 마이그레이션
    """
    if is_supabase_enabled():
        db_items = db_load_history()
        if db_items:
            return db_items

    # 로컬 JSON 폴백
    local_items = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                local_items = json.load(f)
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
    (이미 오늘 기록된 종목은 중복 방지)
    """
    history = load_history()
    today_str = get_ny_market_date_str()
    existing_keys = {f"{item['date']}_{item['ticker']}" for item in history}
    
    added_count = 0
    for r in recs:
        rec_date_val = r.get('date') or today_str
        key = f"{rec_date_val}_{r['ticker']}"
        if key not in existing_keys:
            history.append({
                'strategy_version': r.get('strategy_version', CURRENT_STRATEGY_VERSION),
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
                'market_context': r.get('market_context', {'market_regime': '정상장세'}),
                'fee_slippage_pct': r.get('fee_slippage_pct', 0.25),
                'status': 'HOLD',  # HOLD, WIN_TARGET1, WIN_TARGET2, STOP_LOSS
                'max_price': r['current_price'],
                'current_price': r['current_price'],
                'current_pnl_pct': 0.0,
                'realized_pnl_pct': None,
                'realized_pnl_net_pct': None,
                'hit_success': False,
                'is_completed': False
            })
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


def get_adaptive_factor_weights() -> Dict[str, Any]:
    """
    과거 추천 이력의 성공/실패 데이터를 바탕으로:
    1. 각 팩터별 승률 및 가중치 보너스(+)/페널티(-) 계산
    2. 최근 14일 내 손절 이탈 종목(쿨다운 대상) 식별
    3. 최근 손절/실패 원인 기술 감사 로그 생성
    """
    history = load_history()
    if not history:
        return {
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

        # 최근 14일(약 10거래일) 이내 손절 이탈 종목 쿨다운 등록
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

        # 팩터별 성패 집계 (정규화 버킷 적용)
        if is_completed:
            for tag in item.get('tags', []):
                norm_tag = normalize_factor_tag(tag)
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
        'factor_adjustments': factor_adjustments,
        'cooldown_tickers': cooldown_tickers,
        'failure_notes': failure_notes[-5:],
        'boosted_factors': boosted_factors,
        'penalized_factors': penalized_factors
    }


def evaluate_and_learn_from_history() -> Dict[str, Any]:
    """
    과거에 추천했던 모든 종목들의 실제 주가를 야후 파이낸스로 재조회하여:
    1. 실제 목표가에 도달했는지(적중 여부)
    2. 손절/실패 시 실패 원인 기술 분석 및 감사 로그 작성
    3. 팩터별 실시간 승률 및 피드백 반영
    """
    history = load_history()
    if not history:
        return {
            'total_recs': 0,
            'completed_count': 0,
            'ongoing_count': 0,
            'win_rate': 0.0,
            'avg_return': 0.0,
            'history_items': [],
            'best_factors': [],
            'adaptive_weights': get_adaptive_factor_weights()
        }
        
    unique_tickers = list(set([item['ticker'] for item in history]))
    today_str = datetime.now().strftime('%Y-%m-%d')
    today_date = datetime.now().date()
    
    # 최신 시세 일괄 수집
    stock_dfs = {}
    for t in unique_tickers:
        try:
            df = yf.Ticker(t).history(period='3mo', interval='1d')
            if not df.empty:
                if df.index.tz is not None:
                    df.index = df.index.tz_localize(None)
                stock_dfs[t] = df
        except Exception:
            pass
            
    completed_samples = 0
    wins = 0
    total_pnls = []
    factor_hits = {}
    ongoing_count = 0
    
    for item in history:
        t = item['ticker']
        rec_p = item['rec_price']
        t1 = item['target_price']
        sl = item['stop_loss']
        rec_date_str = item.get('date', today_str)
        try:
            rec_date = datetime.strptime(rec_date_str, '%Y-%m-%d').date()
        except Exception:
            rec_date = today_date
            
        if item.get('is_completed'):
            completed_samples += 1
            if item.get('hit_success'):
                wins += 1
            realized = item.get('realized_pnl_pct')
            if realized is not None:
                total_pnls.append(realized)
            for tag in item.get('tags', []):
                if tag not in factor_hits:
                    factor_hits[tag] = {'total': 0, 'wins': 0}
                factor_hits[tag]['total'] += 1
                if item.get('hit_success'):
                    factor_hits[tag]['wins'] += 1
            continue

        if t in stock_dfs:
            df = stock_dfs[t]
            curr_p = float(df['Close'].iloc[-1])
            item['current_price'] = round(curr_p, 2)
            
            pnl_pct = ((curr_p / rec_p) - 1) * 100
            item['current_pnl_pct'] = round(pnl_pct, 2)
            
            # 추천일 이후(date > rec_date)의 실제 거래일 봉만 필터링!
            df_after = df[df.index.date > rec_date].sort_index()
            
            if df_after.empty:
                item['status'] = '⏳ 실시간 추적 중 (오늘 등록)'
                item['hit_success'] = False
                item['is_completed'] = False
                ongoing_count += 1
            else:
                after_high = float(df_after['High'].max())
                after_low = float(df_after['Low'].min())
                item['max_price'] = round(after_high, 2)
                
                # 일자별(시간순) 순차 판정: 목표가 vs 손절가 선후 관계 판별
                resolved = False
                for bar_date, row in df_after.iterrows():
                    bar_high = float(row['High'])
                    bar_low = float(row['Low'])
                    
                    hit_t = bar_high >= t1
                    hit_s = bar_low <= sl
                    
                    if hit_t and hit_s:
                        # 동일 일봉 내 동시 도달: 보수적 리스크 원칙에 따라 손절 우선 처리
                        item['hit_success'] = False
                        item['status'] = '⚠️ 변동성 손절 이탈 (동일봉 도달)'
                        item['is_completed'] = True
                        item['exit_price'] = sl
                        item['exit_date'] = str(bar_date.date())
                        realized_pnl = round(((sl / rec_p) - 1) * 100, 2)
                        item['realized_pnl_pct'] = realized_pnl
                        item['realized_pnl_net_pct'] = round(realized_pnl - item.get('fee_slippage_pct', 0.25), 2)
                        diagnosis = diagnose_failure_reason(item, df_after.loc[:bar_date], df)
                        item['failure_reason'] = diagnosis['full_diagnosis']
                        completed_samples += 1
                        total_pnls.append(realized_pnl)
                        resolved = True
                        break
                    elif hit_s:
                        item['hit_success'] = False
                        item['status'] = '⚠️ 손절선 이탈'
                        item['is_completed'] = True
                        item['exit_price'] = sl
                        item['exit_date'] = str(bar_date.date())
                        realized_pnl = round(((sl / rec_p) - 1) * 100, 2)
                        item['realized_pnl_pct'] = realized_pnl
                        item['realized_pnl_net_pct'] = round(realized_pnl - item.get('fee_slippage_pct', 0.25), 2)
                        diagnosis = diagnose_failure_reason(item, df_after.loc[:bar_date], df)
                        item['failure_reason'] = diagnosis['full_diagnosis']
                        completed_samples += 1
                        total_pnls.append(realized_pnl)
                        resolved = True
                        break
                    elif hit_t:
                        item['hit_success'] = True
                        item['status'] = '🎯 목표가 도달 (성공)'
                        item['is_completed'] = True
                        item['exit_price'] = t1
                        item['exit_date'] = str(bar_date.date())
                        realized_pnl = round(((t1 / rec_p) - 1) * 100, 2)
                        item['realized_pnl_pct'] = realized_pnl
                        item['realized_pnl_net_pct'] = round(realized_pnl - item.get('fee_slippage_pct', 0.25), 2)
                        item['failure_reason'] = None
                        completed_samples += 1
                        wins += 1
                        total_pnls.append(realized_pnl)
                        resolved = True
                        break
                        
                if not resolved:
                    # 20거래일(약 1개월) 경과 시 기간 만료 청산
                    if len(df_after) >= 20:
                        exit_p = curr_p
                        realized_pnl = round(((exit_p / rec_p) - 1) * 100, 2)
                        item['is_completed'] = True
                        item['exit_price'] = round(exit_p, 2)
                        item['exit_date'] = str(df_after.index[-1].date())
                        item['realized_pnl_pct'] = realized_pnl
                        item['realized_pnl_net_pct'] = round(realized_pnl - item.get('fee_slippage_pct', 0.25), 2)
                        item['hit_success'] = (exit_p >= rec_p)
                        item['status'] = '기간 만료 마감'
                        if not item['hit_success']:
                            diagnosis = diagnose_failure_reason(item, df_after, df)
                            item['failure_reason'] = diagnosis['full_diagnosis']
                        else:
                            item['failure_reason'] = None
                        completed_samples += 1
                        if item['hit_success']:
                            wins += 1
                        total_pnls.append(realized_pnl)
                    else:
                        item['status'] = f'📈 보유 {len(df_after)}일차 추적 중'
                        item['hit_success'] = False
                        item['is_completed'] = False
                        ongoing_count += 1
                    
                if item.get('is_completed'):
                    for tag in item.get('tags', []):
                        if tag not in factor_hits:
                            factor_hits[tag] = {'total': 0, 'wins': 0}
                        factor_hits[tag]['total'] += 1
                        if item['hit_success']:
                            factor_hits[tag]['wins'] += 1
                            
    save_history(history)
    
    win_rate = (wins / completed_samples * 100) if completed_samples > 0 else 0.0
    avg_return = np.mean(total_pnls) if total_pnls else 0.0
    
    # 정밀 퀀트 통계: 기대값(Expected Value), Profit Factor, 수수료 차감 순손익
    losses = completed_samples - wins
    win_pnls = [p for p in total_pnls if p > 0]
    loss_pnls = [p for p in total_pnls if p <= 0]
    
    avg_win = float(np.mean(win_pnls)) if win_pnls else 0.0
    avg_loss = float(np.mean(loss_pnls)) if loss_pnls else 0.0
    
    sum_win = float(sum(win_pnls)) if win_pnls else 0.0
    sum_loss = float(abs(sum(loss_pnls))) if loss_pnls else 0.0
    profit_factor = round(sum_win / sum_loss, 2) if sum_loss > 0 else (99.9 if sum_win > 0 else 0.0)
    
    # 1회 추천당 통계적 기대값: (승률 * 평균수익) - (패배율 * 평균손실) - 0.25%(수수료/슬리피지)
    if completed_samples > 0:
        p_win = wins / completed_samples
        p_loss = losses / completed_samples
        expected_value = round((p_win * avg_win) - (p_loss * abs(avg_loss)) - 0.25, 2)
    else:
        expected_value = 0.0
        
    total_net_pnls = [p - 0.25 for p in total_pnls]
    avg_return_net = round(float(np.mean(total_net_pnls)), 2) if total_net_pnls else 0.0
    
    # 표본 수에 따른 통계적 신뢰도 단계 판정 (과최적화 방지 안내)
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
    
    adaptive_weights = get_adaptive_factor_weights()
    
    return {
        'strategy_version': CURRENT_STRATEGY_VERSION,
        'strategy_rules': CURRENT_STRATEGY_RULES,
        'total_recs': len(history),
        'completed_count': completed_samples,
        'ongoing_count': ongoing_count,
        'wins': wins,
        'losses': losses,
        'win_rate': round(win_rate, 1),
        'avg_return': round(avg_return, 1),
        'avg_return_net': avg_return_net,
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'expected_value': expected_value,
        'profit_factor': profit_factor,
        'sample_tier': sample_tier,
        'sample_tier_desc': sample_tier_desc,
        'sample_error_margin': sample_error_margin,
        'history_items': history[::-1],
        'best_factors': best_factors[:4],
        'adaptive_weights': adaptive_weights
    }

