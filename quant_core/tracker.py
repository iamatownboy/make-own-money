"""
quant_core/tracker.py
AI 추천 종목 이력 기록, 실제 주가 성과 추적 및 팩터 가중치 자가 학습(Feedback Loop) 모듈
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


def evaluate_and_learn_from_history() -> Dict[str, Any]:
    """
    과거에 추천했던 모든 종목들의 실제 주가를 야후 파이낸스로 재조회하여:
    1. 실제 목표가에 도달했는지(적중 여부)
    2. 현재까지의 누적 승률 및 평균 수익률
    3. 어떤 기술적 패턴(피보나치, 빗각 등)이 가장 잘 맞았는지 팩터별 적중률을 학습 분석합니다.
    (주의: 추천 당일(오늘) 신규 추천 종목은 아직 장 마감 및 거래가 진행되지 않았으므로 '실시간 추적 중'으로 분류하고 승률 통계에서 분리합니다.)
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
            'best_factors': []
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
    factor_hits = {}  # 팩터별 성공 횟수 기록 (자가 학습용)
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
            
        if t in stock_dfs:
            df = stock_dfs[t]
            curr_p = float(df['Close'].iloc[-1])
            item['current_price'] = round(curr_p, 2)
            
            pnl_pct = ((curr_p / rec_p) - 1) * 100
            item['current_pnl_pct'] = round(pnl_pct, 2)
            
            # 추천일 이후(date > rec_date)의 실제 거래일 봉만 필터링!
            df_after = df[df.index.date > rec_date]
            
            if df_after.empty:
                # 추천 당일이거나 아직 다음 거래일 봉이 완성되지 않음 (본장 미개장)
                item['status'] = '⏳ 실시간 추적 중 (오늘 등록)'
                item['hit_success'] = False
                item['is_completed'] = False
                ongoing_count += 1
            else:
                after_high = float(df_after['High'].max())
                after_low = float(df_after['Low'].min())
                item['max_price'] = round(after_high, 2)
                
                if after_high >= t1:
                    item['hit_success'] = True
                    item['status'] = '🎯 목표가 도달 (성공)'
                    item['is_completed'] = True
                    completed_samples += 1
                    wins += 1
                    total_pnls.append(pnl_pct)
                elif after_low <= sl:
                    item['hit_success'] = False
                    item['status'] = '⚠️ 손절선 이탈'
                    item['is_completed'] = True
                    completed_samples += 1
                    total_pnls.append(pnl_pct)
                elif len(df_after) >= 20:
                    item['hit_success'] = (curr_p >= rec_p)
                    item['status'] = '기간 만료 마감'
                    item['is_completed'] = True
                    completed_samples += 1
                    if item['hit_success']:
                        wins += 1
                    total_pnls.append(pnl_pct)
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
    
    # 최고 승률 팩터 랭킹 (학습 피드백)
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
    
    return {
        'total_recs': len(history),
        'completed_count': completed_samples,
        'ongoing_count': ongoing_count,
        'wins': wins,
        'win_rate': round(win_rate, 1),
        'avg_return': round(avg_return, 1),
        'history_items': history[::-1],
        'best_factors': best_factors[:4]
    }
