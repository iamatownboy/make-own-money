"""
quant_core/tracker.py
퀀트 추천 종목 이력 기록, 실제 주가 성과 추적 및 규칙 기반 가중치 피드백(Feedback Loop) 모듈
"""

import os
import json
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, List, Any

HISTORY_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'recommendation_history.json')


def load_history() -> List[Dict[str, Any]]:
    """과거 추천 이력을 불러옵니다."""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_history(history: List[Dict[str, Any]]):
    """추천 이력을 저장합니다."""
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"추천 이력 저장 실패: {e}")


def record_daily_recommendations(recs: List[Dict[str, Any]]):
    """
    오늘 AI가 선별한 추천 종목들을 학습 데이터베이스에 기록합니다.
    (이미 오늘 기록된 종목은 중복 방지)
    """
    history = load_history()
    today_str = datetime.now().strftime('%Y-%m-%d')
    existing_keys = {f"{item['date']}_{item['ticker']}" for item in history}
    
    added_count = 0
    for r in recs:
        key = f"{today_str}_{r['ticker']}"
        if key not in existing_keys:
            history.append({
                'date': today_str,
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
                'status': 'HOLD',  # HOLD, WIN_TARGET1, WIN_TARGET2, STOP_LOSS
                'max_price': r['current_price'],
                'current_price': r['current_price'],
                'current_pnl_pct': 0.0,
                'hit_success': False
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

        # 팩터별 성패 집계
        if is_completed:
            for tag in item.get('tags', []):
                if tag not in factor_stats:
                    factor_stats[tag] = {'total': 0, 'wins': 0, 'losses': 0}
                factor_stats[tag]['total'] += 1
                if hit_success:
                    factor_stats[tag]['wins'] += 1
                else:
                    factor_stats[tag]['losses'] += 1

    # 팩터 가중치 조정값 산출 (최소 2회 이상 표본)
    factor_adjustments = {}
    boosted_factors = []
    penalized_factors = []

    for tag, stats in factor_stats.items():
        total = stats['total']
        if total >= 2:
            win_rate = (stats['wins'] / total) * 100
            if win_rate >= 70:
                adj = 5 if win_rate >= 80 else 3
                factor_adjustments[tag] = adj
                boosted_factors.append({'tag': tag, 'win_rate': round(win_rate, 1), 'adj': f"+{adj}점 (우수 팩터 가산)"})
            elif win_rate <= 40:
                adj = -10 if win_rate <= 25 else -6
                factor_adjustments[tag] = adj
                penalized_factors.append({'tag': tag, 'win_rate': round(win_rate, 1), 'adj': f"{adj}점 (실패율 과다 감점)"})
            else:
                factor_adjustments[tag] = 0

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
        'total_recs': len(history),
        'completed_count': completed_samples,
        'ongoing_count': ongoing_count,
        'wins': wins,
        'win_rate': round(win_rate, 1),
        'avg_return': round(avg_return, 1),
        'history_items': history[::-1],
        'best_factors': best_factors[:4],
        'adaptive_weights': adaptive_weights
    }

