"""
quant_core/snapshot.py
매일의 예측 스냅샷을 추적하고 비교하며, 예측의 정확도를 리포팅하는 모듈
"""

import os
import json
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import pytz

from .db import get_supabase_client, is_supabase_enabled
from .screener import get_ny_market_date_str

SNAPSHOT_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'prediction_snapshots.json')

def _np_encoder(obj):
    """NumPy 자료형을 JSON 직렬화하기 위한 인코더"""
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    if isinstance(obj, (np.floating, float)):
        return float(obj)
    if isinstance(obj, (np.integer, int)):
        return int(obj)
    return str(obj)


def db_save_snapshots(date_str: str, snapshot_items: List[Dict]) -> bool:
    """
    Supabase 'daily_snapshots' 테이블에 스냅샷을 저장합니다.
    (date, ticker) 고유 제약조건을 기반으로 upsert를 수행합니다.
    """
    client = get_supabase_client()
    if not client or not is_supabase_enabled():
        return False
        
    try:
        records = []
        for item in snapshot_items:
            div_val = item.get("divergence_status")
            if isinstance(div_val, bool):
                div_str = "bullish" if div_val else "none"
            elif isinstance(div_val, str):
                div_str = div_val
            else:
                div_str = "none"

            rec = {
                "date": date_str,
                "ticker": str(item.get("ticker", "")),
                "total_score": int(item.get("total_score", 0)) if item.get("total_score") is not None else None,
                "tech_score": int(item.get("tech_score", 0)) if item.get("tech_score") is not None else None,
                "fund_score": int(item.get("fund_score", 0)) if item.get("fund_score") is not None else None,
                "mom_score": int(item.get("mom_score", 0)) if item.get("mom_score") is not None else None,
                "pattern_status": str(item.get("pattern_status", "")) if item.get("pattern_status") is not None else None,
                "current_price": float(item.get("current_price", 0.0)) if item.get("current_price") is not None else None,
                "target_1": float(item.get("target_1", 0.0)) if item.get("target_1") is not None else None,
                "target_2": float(item.get("target_2", 0.0)) if item.get("target_2") is not None else None,
                "stop_loss": float(item.get("stop_loss", 0.0)) if item.get("stop_loss") is not None else None,
                "risk_reward": float(item.get("risk_reward_ratio", 0.0)) if item.get("risk_reward_ratio") is not None else None,
                "fib_status": str(item.get("fib_status", "none")),
                "trendline_status": str(item.get("trendline_status", "none")),
                "divergence_status": div_str,
                "market_regime": str(item.get("market_regime", "unknown")),
                "is_qualified": bool(item.get("is_qualified", False)),
                "updated_at": datetime.now(pytz.utc).isoformat()
            }
            records.append(rec)
            
        client.table("daily_snapshots").upsert(
            records,
            on_conflict="date,ticker"
        ).execute()
        return True
    except Exception as e:
        print(f"[DB] Supabase 스냅샷 저장 실패: {e}")
        return False


def db_load_snapshots(days_back: int = 90) -> Optional[Dict[str, List[Dict]]]:
    """
    Supabase 'daily_snapshots' 테이블에서 과거 스냅샷을 불러옵니다.
    날짜를 기준으로 그룹화하여 반환합니다.
    """
    client = get_supabase_client()
    if not client or not is_supabase_enabled():
        return None
        
    try:
        eastern = pytz.timezone('US/Eastern')
        cutoff_date = (datetime.now(eastern) - timedelta(days=days_back)).strftime('%Y-%m-%d')
        
        response = client.table("daily_snapshots") \
            .select("*") \
            .gte("date", cutoff_date) \
            .execute()
            
        if response and hasattr(response, "data") and response.data is not None:
            grouped = {}
            for row in response.data:
                d = row.get("date")
                if d not in grouped:
                    grouped[d] = []
                grouped[d].append(dict(row))
            return grouped
    except Exception as e:
        print(f"[DB] Supabase 스냅샷 로드 실패: {e}")
        return None
        
    return None


def load_snapshots(days_back: int = 90) -> Dict[str, List[Dict]]:
    """
    로컬 JSON 파일(또는 Supabase)에서 과거 스냅샷을 로드합니다.
    최우선적으로 Supabase 연동을 시도하고, 실패 시 로컬 JSON으로 폴백합니다.
    """
    snapshots = {}
    
    # 1. Supabase 시도
    cloud_data = db_load_snapshots(days_back)
    if cloud_data is not None:
        return cloud_data
        
    # 2. 로컬 JSON 폴백
    if os.path.exists(SNAPSHOT_FILE):
        try:
            with open(SNAPSHOT_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                snapshots = data.get("snapshots", {})
                
            # days_back 필터링 (US/Eastern 기준)
            eastern = pytz.timezone('US/Eastern')
            cutoff_date = (datetime.now(eastern) - timedelta(days=days_back)).strftime('%Y-%m-%d')
            
            filtered = {k: v for k, v in snapshots.items() if k >= cutoff_date}
            return filtered
        except Exception as e:
            print(f"로컬 스냅샷 로드 실패: {e}")
            
    return snapshots


def save_daily_snapshot(scan_results: List[Dict], all_scores: List[Dict] = None) -> bool:
    """
    전체 스캔 결과를 바탕으로 당일자 스냅샷을 저장합니다.
    로컬 JSON과 Supabase 양쪽에 저장하며, 지난 90일 스냅샷만 유지하도록 프루닝합니다.
    """
    try:
        date_str = get_ny_market_date_str()
        
        items = []
        source_data = all_scores if all_scores else scan_results
        
        for p in source_data:
            # 보조지표 상태 추출
            fib = p.get('fibonacci', {})
            fib_stat = 'golden_pocket' if fib.get('is_in_golden_pocket') else ('fib_382' if fib.get('is_at_fib_382') else 'none')
            
            tl = p.get('trendline', {})
            tl_stat = 'breakout' if tl.get('is_breakout') else ('approaching' if tl.get('is_approaching') else 'none')
            
            div = p.get('divergence', {})
            div_stat = 'bullish' if div.get('has_divergence') else 'none'
            
            mkt = p.get('market_context', {})
            regime = mkt.get('regime', 'unknown')
            
            item = {
                'ticker': p.get('ticker'),
                'total_score': p.get('total_score', 0),
                'tech_score': p.get('tech_score', 0),
                'fund_score': p.get('fund_score', 0),
                'mom_score': p.get('mom_score', 0),
                'pattern_status': p.get('pattern_status', ''),
                'current_price': p.get('current_price', 0.0),
                'target_1': p.get('bull_target_1', 0.0),
                'target_2': p.get('bull_target_2', 0.0),
                'stop_loss': p.get('stop_loss', 0.0),
                'risk_reward_ratio': p.get('risk_reward_ratio', 0.0),
                'fib_status': fib_stat,
                'trendline_status': tl_stat,
                'divergence_status': div_stat,
                'market_regime': regime,
                'is_qualified': p.get('is_qualified', False)
            }
            items.append(item)
            
        # 1. Supabase 클라우드 DB 저장 시도
        db_save_snapshots(date_str, items)
        
        # 2. 로컬 JSON 저장 및 프루닝 (최근 90일치만 유지)
        existing_data = {}
        if os.path.exists(SNAPSHOT_FILE):
            try:
                with open(SNAPSHOT_FILE, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f).get("snapshots", {})
            except Exception:
                pass
                
        existing_data[date_str] = items
        
        eastern = pytz.timezone('US/Eastern')
        cutoff_date = (datetime.now(eastern) - timedelta(days=90)).strftime('%Y-%m-%d')
        filtered_data = {k: v for k, v in existing_data.items() if k >= cutoff_date}
        
        with open(SNAPSHOT_FILE, 'w', encoding='utf-8') as f:
            json.dump({"snapshots": filtered_data}, f, ensure_ascii=False, indent=2, default=_np_encoder)
            
        return True
    except Exception as e:
        print(f"일간 스냅샷 저장 실패: {e}")
        return False


def compare_with_previous(today_date: str = None) -> Dict:
    """
    오늘과 어제의 스냅샷을 로드하고 비교하여 다음과 같은 변화를 추출합니다:
    - new_entries: 오늘 새롭게 스크리닝 요건(is_qualified)을 충족한 종목
    - dropped_entries: 어제는 요건을 충족했으나 오늘은 탈락한 종목
    - score_changes: 점수에 변화가 생긴 종목
    - target_changes: 목표가가 변경된 종목
    - price_changes: 전일 대비 실제 가격 변화
    """
    if not today_date:
        today_date = get_ny_market_date_str()
        
    snapshots = load_snapshots(days_back=10)
    
    if not snapshots or today_date not in snapshots:
        return {"error": "오늘의 스냅샷 데이터가 없습니다."}
        
    today_data = snapshots[today_date]
    
    past_dates = sorted([d for d in snapshots.keys() if d < today_date], reverse=True)
    if not past_dates:
        return {"error": "비교할 이전 스냅샷 데이터가 없습니다."}
        
    yesterday_date = past_dates[0]
    yesterday_data = snapshots[yesterday_date]
    
    today_map = {item['ticker']: item for item in today_data}
    yesterday_map = {item['ticker']: item for item in yesterday_data}
    
    today_qualified = {k: v for k, v in today_map.items() if v.get('is_qualified')}
    yesterday_qualified = {k: v for k, v in yesterday_map.items() if v.get('is_qualified')}
    
    new_entries = [v for k, v in today_qualified.items() if k not in yesterday_qualified]
    dropped_entries = [v for k, v in yesterday_qualified.items() if k not in today_qualified]
    
    score_changes = []
    target_changes = []
    price_changes = []
    
    for ticker, t_item in today_map.items():
        if ticker in yesterday_map:
            y_item = yesterday_map[ticker]
            
            # 가격 변동 추적
            t_price = t_item.get('current_price', 0)
            y_price = y_item.get('current_price', 0)
            if y_price and y_price > 0:
                p_change = ((t_price / y_price) - 1) * 100
                price_changes.append({"ticker": ticker, "change_pct": round(p_change, 2), "old": y_price, "new": t_price})
            
            # 점수 변동 추적
            t_score = t_item.get('total_score', 0)
            y_score = y_item.get('total_score', 0)
            if t_score != y_score:
                score_changes.append({
                    "ticker": ticker, 
                    "delta": t_score - y_score,
                    "direction": "up" if t_score > y_score else "down",
                    "old_score": y_score,
                    "new_score": t_score
                })
                
            # 1차 목표가 변동 추적
            t_target1 = t_item.get('target_1', 0)
            y_target1 = y_item.get('target_1', 0)
            if t_target1 != y_target1 and y_target1 and y_target1 > 0:
                t_delta = ((t_target1 / y_target1) - 1) * 100
                if abs(t_delta) >= 1.0: # 1% 이상 변동된 경우만 수집
                    target_changes.append({
                        "ticker": ticker,
                        "target_1_delta_pct": round(t_delta, 2),
                        "old_target_1": y_target1,
                        "new_target_1": t_target1
                    })
                    
    score_changes.sort(key=lambda x: abs(x['delta']), reverse=True)
    target_changes.sort(key=lambda x: abs(x['target_1_delta_pct']), reverse=True)
    price_changes.sort(key=lambda x: abs(x['change_pct']), reverse=True)
    
    return {
        "today_date": today_date,
        "yesterday_date": yesterday_date,
        "new_entries": new_entries,
        "dropped_entries": dropped_entries,
        "score_changes": score_changes,
        "target_changes": target_changes,
        "price_changes": price_changes
    }


def get_prediction_accuracy_report(days_back: int = 30) -> Dict:
    """
    과거 스냅샷을 기반으로 실제 미래 가격 변동을 조회하여, 모델의 예측 정확도 리포트를 산출합니다.
    yfinance를 활용하여 과거 추천 시점 대비 실제 달성률과 오차를 분석합니다.
    """
    snapshots = load_snapshots(days_back=days_back)
    
    eastern = pytz.timezone('US/Eastern')
    today_dt = datetime.now(eastern)
    
    results_by_period = {
        '5d': {'hits': 0, 'total': 0, 'dir_hits': 0, 'error_sum': 0, 'pending': 0},
        '10d': {'hits': 0, 'total': 0, 'dir_hits': 0, 'error_sum': 0, 'pending': 0},
        '20d': {'hits': 0, 'total': 0, 'dir_hits': 0, 'error_sum': 0, 'pending': 0}
    }
    
    per_stock_acc = {}
    
    # 종목별 과거 데이터 조회를 위한 티커 목록 취합
    tickers_to_check = set()
    for date_str, items in snapshots.items():
        try:
            snap_dt = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=eastern)
            days_passed = (today_dt - snap_dt).days
            
            if days_passed >= 5: # 최소 5일 경과한 데이터만 평가 대상
                for item in items:
                    if item.get('is_qualified'):
                        tickers_to_check.add(item['ticker'])
        except Exception:
            continue
                    
    hist_data = {}
    if tickers_to_check:
        try:
            df = yf.download(list(tickers_to_check), period='3mo', progress=False)['Close']
            if len(tickers_to_check) == 1:
                ticker = list(tickers_to_check)[0]
                hist_data[ticker] = df
            else:
                for ticker in tickers_to_check:
                    if ticker in df.columns:
                        hist_data[ticker] = df[ticker]
        except Exception as e:
            print(f"정확도 평가용 사후 데이터 수집 실패: {e}")
            
    for date_str, items in snapshots.items():
        try:
            snap_dt = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=eastern)
            snap_dt_naive = snap_dt.replace(tzinfo=None)
            
            for item in items:
                if not item.get('is_qualified'):
                    continue
                    
                ticker = item['ticker']
                if ticker not in hist_data:
                    continue
                    
                s_hist = hist_data[ticker]
                if s_hist.index.tz is not None:
                    s_hist.index = s_hist.index.tz_localize(None)
                
                # 스냅샷 기준일 이후의 가격 흐름
                future_data = s_hist[s_hist.index > snap_dt_naive]
                
                base_price = item.get('current_price', 0)
                target_1 = item.get('target_1', 0)
                
                if base_price <= 0 or target_1 <= 0 or len(future_data) == 0:
                    continue
                    
                pred_pct = (target_1 / base_price) - 1
                pred_dir = 1 if pred_pct > 0 else -1
                
                if ticker not in per_stock_acc:
                    per_stock_acc[ticker] = {'predictions': 0, 'hits': 0}
                    
                # 각 기간별(5일, 10일, 20일) 예측 적중 여부 분석
                for period, target_days in [('5d', 5), ('10d', 10), ('20d', 20)]:
                    # 엄격한 성숙도 기준: 해당 영업일이 완전히 경과한 표본만 집계
                    if len(future_data) >= target_days:
                        period_data = future_data.head(target_days)
                        if isinstance(period_data, pd.DataFrame):
                            period_series = period_data.iloc[:, 0]
                        else:
                            period_series = period_data
                        max_price = float(np.max(period_series))
                        min_price = float(np.min(period_series))
                        end_price = float(period_series.iloc[-1])
                        
                        actual_max_pct = (max_price / base_price) - 1
                        actual_end_pct = (end_price / base_price) - 1
                        actual_dir = 1 if actual_end_pct > 0 else -1
                        
                        hit = (max_price >= target_1) if pred_dir > 0 else (min_price <= target_1)
                        error = abs(pred_pct - actual_max_pct) * 100
                        
                        results_by_period[period]['total'] += 1
                        if hit:
                            results_by_period[period]['hits'] += 1
                        if pred_dir == actual_dir:
                            results_by_period[period]['dir_hits'] += 1
                        results_by_period[period]['error_sum'] += error
                        
                        # 종목 단위 신뢰도는 최장기(20일) 성숙 표본을 기준으로 가산
                        if period == '20d':
                            per_stock_acc[ticker]['predictions'] += 1
                            if hit:
                                per_stock_acc[ticker]['hits'] += 1
                    else:
                        # 아직 해당 기간이 채워지지 않은 미성숙 표본은 통계 왜곡 방지를 위해 pending으로 집계
                        results_by_period[period]['pending'] += 1
                                
        except Exception as e:
            continue
            
    # 최종 리포트 생성
    report = {}
    for p in ['5d', '10d', '20d']:
        st = results_by_period[p]
        total = st['total']
        pending = st.get('pending', 0)
        report[f'period_{p}'] = {
            'target_hit_rate': round((st['hits'] / total) * 100, 1) if total > 0 else 0.0,
            'directional_accuracy': round((st['dir_hits'] / total) * 100, 1) if total > 0 else 0.0,
            'avg_error': round(st['error_sum'] / total, 2) if total > 0 else 0.0,
            'sample_count': total,
            'pending_count': pending
        }
        
    stock_list = []
    for t, d in per_stock_acc.items():
        if d['predictions'] > 0:
            acc = round((d['hits'] / d['predictions']) * 100, 1)
            stock_list.append({
                'ticker': t,
                'predictions': d['predictions'],
                'hits': d['hits'],
                'accuracy': acc
            })
            
    stock_list.sort(key=lambda x: (x['accuracy'], x['predictions']), reverse=True)
    report['per_stock_accuracy'] = stock_list
    report['sample_count'] = max([report[f'period_{p}']['sample_count'] for p in ['5d', '10d', '20d']]) if any(results_by_period[p]['total'] > 0 for p in ['5d', '10d', '20d']) else 0
    
    return report
