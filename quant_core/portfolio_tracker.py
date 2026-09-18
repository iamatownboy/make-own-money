"""
포트폴리오 성과 추적 및 팩터 분석 모듈
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta
import math

from .tracker import normalize_factor_tag, load_history
from .data_loader import fetch_stock_data


def build_daily_equity_curve(history: Optional[List[Dict]] = None, initial_capital: float = 10_000_000.0,
                             price_data: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    """
    일별 에퀴티 커브를 생성하고, 전체 성과 지표(MDD, 샤프 지수 등)를 계산합니다.
    - 실제 일별 종가(Mark-to-Market) 기반 자산 평가
    - 동시 보유 종목 수 최대 7개 제한 (MAX_POSITIONS = 7)
    - 현금 잔고 및 슬롯 배분 제약 엄격 준수
    
    Args:
        history (Optional[List[Dict]]): 추천 히스토리 리스트. None일 경우 load_history()로 불러옵니다.
        initial_capital (float): 초기 자본금. 기본값은 10,000,000원입니다.
        price_data (Optional[pd.DataFrame]): 모의 테스트 또는 외부 주입용 일별 종가 데이터프레임.
        
    Returns:
        Dict[str, Any]: 에퀴티 커브와 포트폴리오 성과 지표들을 포함하는 딕셔너리.
    """
    if history is None:
        history = load_history()
        
    if not history:
        return {
            'equity_curve': [],
            'total_return_pct': 0.0,
            'mdd_pct': 0.0,
            'max_underwater_days': 0,
            'sharpe_ratio': 0.0,
            'calmar_ratio': 0.0
        }
        
    # 데이터프레임으로 변환
    df = pd.DataFrame(history)
    df['date'] = pd.to_datetime(df['date'])
    
    # 종료일이 없는 경우 20일 만기(20-day expiry)로 가정하거나 현재일로 처리
    if 'exit_date' in df.columns:
        df['exit_date'] = pd.to_datetime(df['exit_date'])
        df['exit_date'] = df['exit_date'].fillna(df['date'] + pd.Timedelta(days=20))
    else:
        df['exit_date'] = df['date'] + pd.Timedelta(days=20)
        
    start_date = df['date'].min()
    end_date = df['exit_date'].max()
    
    if pd.isna(start_date) or pd.isna(end_date):
        return {
            'equity_curve': [],
            'total_return_pct': 0.0,
            'mdd_pct': 0.0,
            'max_underwater_days': 0,
            'sharpe_ratio': 0.0,
            'calmar_ratio': 0.0
        }

    date_range = pd.date_range(start=start_date, end=end_date, freq='B')
    if len(date_range) == 0:
        return {
            'equity_curve': [],
            'total_return_pct': 0.0,
            'mdd_pct': 0.0,
            'max_underwater_days': 0,
            'sharpe_ratio': 0.0,
            'calmar_ratio': 0.0
        }
    
    # 거래 정보 전처리
    trades = []
    for idx, row in df.iterrows():
        t_ticker = str(row.get('ticker', ''))
        if not t_ticker:
            continue
        e_date = row['date']
        x_date = row['exit_date']
        e_price = float(row.get('current_price', 100.0) or 100.0)
        if e_price <= 0:
            e_price = 100.0
            
        x_price = row.get('exit_price')
        if x_price is None or pd.isna(x_price) or float(x_price) <= 0:
            pnl_net = row.get('realized_pnl_net_pct')
            pnl_gross = row.get('realized_pnl_pct', 0.0)
            target_pnl = pnl_net if (pnl_net is not None and not pd.isna(pnl_net)) else pnl_gross
            x_price = e_price * (1.0 + float(target_pnl or 0.0) / 100.0)
        else:
            x_price = float(x_price)
            
        fee_pct = float(row.get('fee_slippage_pct', 0.25) or 0.25) / 100.0
        score = int(row.get('total_score', 0) or 0)
        
        trades.append({
            'id': idx,
            'ticker': t_ticker,
            'entry_date': e_date,
            'exit_date': x_date,
            'entry_price': e_price,
            'exit_price': x_price,
            'fee_pct': fee_pct,
            'score': score
        })
        
    tickers = list({t['ticker'] for t in trades})
    
    # 일별 가격 데이터(price_data) 준비
    if price_data is None:
        try:
            import yfinance as yf
            dl_end = end_date + pd.Timedelta(days=2)
            dl_data = yf.download(tickers, start=start_date, end=dl_end, progress=False)
            if 'Close' in dl_data:
                close_df = dl_data['Close']
            else:
                close_df = dl_data
            if isinstance(close_df, pd.Series):
                close_df = close_df.to_frame(name=tickers[0])
            price_data = close_df
        except Exception:
            price_data = None
            
    # 특정 날짜의 종목 가격 조회 헬퍼
    def _lookup_price(ticker: str, dt_ts: pd.Timestamp, trade_info: Dict) -> float:
        d_str = dt_ts.strftime('%Y-%m-%d')
        if price_data is not None and ticker in price_data.columns:
            matches = price_data.loc[price_data.index.strftime('%Y-%m-%d') == d_str, ticker]
            if not matches.empty and pd.notna(matches.values[0]) and float(matches.values[0]) > 0:
                return float(matches.values[0])
        # 폴백: 진입가~청산가 구간 일자별 보간
        ep = trade_info['entry_price']
        xp = trade_info['exit_price']
        tot_days = max(1, (trade_info['exit_date'] - trade_info['entry_date']).days)
        elapsed = max(0, min(tot_days, (dt_ts - trade_info['entry_date']).days))
        return float(ep + (xp - ep) * (elapsed / tot_days))

    # 포트폴리오 일별 Mark-to-Market 시뮬레이션
    MAX_POSITIONS = 7
    cash = float(initial_capital)
    active_positions = {}  # trade_id -> position dict
    equity_records = []
    
    for cur_dt in date_range:
        cur_str = cur_dt.strftime('%Y-%m-%d')
        
        # 1. 청산 처리: 오늘(또는 이전) exit_date에 도달한 보유 종목 청산
        to_close = [tid for tid, pos in active_positions.items() if pos['exit_date'].strftime('%Y-%m-%d') <= cur_str]
        for tid in to_close:
            pos = active_positions.pop(tid)
            proceeds = pos['shares'] * pos['exit_price']
            net_proceeds = proceeds * (1.0 - pos['fee_pct'])
            cash += net_proceeds
            
        # 2. 신규 진입 처리: 오늘 entry_date인 추천 종목들
        today_candidates = [t for t in trades if t['entry_date'].strftime('%Y-%m-%d') == cur_str]
        today_candidates.sort(key=lambda x: x['score'], reverse=True)
        
        for cand in today_candidates:
            if len(active_positions) >= MAX_POSITIONS:
                break  # 동시 보유 최대 7종목 제약
            if cash < 100.0:
                break  # 가용 현금 부족
                
            # 슬롯당 가용 자본 (당일 추정 총자산 / 7)
            current_est_pos_val = sum(
                p['shares'] * _lookup_price(p['ticker'], cur_dt, p['trade'])
                for p in active_positions.values()
            )
            est_total_equity = cash + current_est_pos_val
            target_slot_capital = est_total_equity / MAX_POSITIONS
            allocated = min(cash, target_slot_capital)
            
            if allocated <= 0 or cand['entry_price'] <= 0:
                continue
                
            fee = allocated * cand['fee_pct']
            net_invest = allocated - fee
            shares = net_invest / cand['entry_price']
            cash -= allocated
            
            active_positions[cand['id']] = {
                'ticker': cand['ticker'],
                'shares': shares,
                'entry_price': cand['entry_price'],
                'exit_date': cand['exit_date'],
                'exit_price': cand['exit_price'],
                'fee_pct': cand['fee_pct'],
                'trade': cand
            }
            
        # 3. 당일 Mark-to-Market 포트폴리오 총가치 산출
        positions_market_val = 0.0
        for pos in active_positions.values():
            curr_p = _lookup_price(pos['ticker'], cur_dt, pos['trade'])
            positions_market_val += pos['shares'] * curr_p
            
        day_equity = cash + positions_market_val
        equity_records.append({
            'date': cur_str,
            'equity_value': round(float(day_equity), 2),
            'cash': round(float(cash), 2),
            'positions_value': round(float(positions_market_val), 2),
            'num_positions': len(active_positions)
        })
        
    capital_series = pd.Series([r['equity_value'] for r in equity_records], index=[r['date'] for r in equity_records])
    daily_returns = capital_series.pct_change().fillna(0)
    for i, ret in enumerate(daily_returns):
        equity_records[i]['daily_return_pct'] = round(float(ret * 100.0), 2)
        
    # Drawdown 및 지표 계산
    cumulative_max = capital_series.cummax()
    drawdown = (capital_series - cumulative_max) / cumulative_max
    
    mdd_pct = round(float(drawdown.min() * 100.0), 2) if not drawdown.empty else 0.0
    total_return_pct = round(float((capital_series.iloc[-1] / initial_capital - 1) * 100.0), 2) if not capital_series.empty else 0.0
    
    # 최대 침체 기간 (max underwater days)
    underwater = drawdown < 0
    underwater_groups = (underwater != underwater.shift()).cumsum()
    underwater_days = underwater.groupby(underwater_groups).sum()
    max_underwater_days = int(underwater_days.max()) if not underwater_days.empty else 0
    
    # 샤프 지수 계산 (연간 252영업일, 무위험 수익률 4% 가정)
    risk_free_rate = 0.04
    daily_rf = risk_free_rate / 252.0
    excess_returns = daily_returns - daily_rf
    if excess_returns.std() > 0:
        sharpe_ratio = round(float(np.sqrt(252) * (excess_returns.mean() / excess_returns.std())), 2)
    else:
        sharpe_ratio = 0.0
        
    # 칼마 지수 계산
    annual_return = ((1 + total_return_pct / 100.0) ** (252 / max(1, len(capital_series)))) - 1 if len(capital_series) > 0 else 0
    calmar_ratio = round(float(annual_return / abs(mdd_pct / 100.0)), 2) if mdd_pct < 0 else 0.0

    return {
        'equity_curve': equity_records,
        'total_return_pct': total_return_pct,
        'mdd_pct': mdd_pct,
        'max_underwater_days': max_underwater_days,
        'sharpe_ratio': sharpe_ratio,
        'calmar_ratio': calmar_ratio
    }


def compare_with_benchmark(equity_curve: List[Dict], benchmark_ticker: str = 'QQQ') -> Dict[str, Any]:
    """
    에퀴티 커브를 벤치마크(QQQ 등)와 비교합니다.
    
    Args:
        equity_curve (List[Dict]): build_daily_equity_curve에서 반환된 에퀴티 커브 리스트.
        benchmark_ticker (str): 벤치마크 종목 티커. 기본값은 'QQQ'.
        
    Returns:
        Dict[str, Any]: 벤치마크 대비 성과 지표를 포함하는 딕셔너리.
    """
    if not equity_curve:
        return {}
        
    try:
        start_date = equity_curve[0]['date']
        end_date = equity_curve[-1]['date']
        
        # 벤치마크 데이터 다운로드
        bm_data = fetch_stock_data(benchmark_ticker, period='max', interval='1d')
        bm_df = pd.DataFrame(bm_data)
        bm_df['date'] = pd.to_datetime(bm_df['date'])
        
        # 날짜 필터링
        mask = (bm_df['date'] >= pd.to_datetime(start_date)) & (bm_df['date'] <= pd.to_datetime(end_date))
        bm_df = bm_df.loc[mask].copy()
        
        if bm_df.empty:
            return {}
            
        bm_df.set_index('date', inplace=True)
        bm_df['daily_return'] = bm_df['close'].pct_change().fillna(0)
        
        # 에퀴티 커브 데이터프레임화
        eq_df = pd.DataFrame(equity_curve)
        eq_df['date'] = pd.to_datetime(eq_df['date'])
        eq_df.set_index('date', inplace=True)
        eq_df['daily_return'] = eq_df['daily_return_pct'] / 100.0
        
        # 날짜 정렬을 위한 조인
        merged = eq_df[['daily_return']].join(bm_df[['daily_return']], rsuffix='_bm', how='inner').fillna(0)
        
        # 벤치마크 수익률 계산
        bm_total_return_pct = float(( (1 + merged['daily_return_bm']).prod() - 1 ) * 100.0)
        
        # 벤치마크 MDD 계산
        bm_cum_returns = (1 + merged['daily_return_bm']).cumprod()
        bm_cum_max = bm_cum_returns.cummax()
        bm_drawdown = (bm_cum_returns - bm_cum_max) / bm_cum_max
        bm_mdd_pct = float(bm_drawdown.min() * 100.0)
        
        # 전략 수익률
        strat_total_return_pct = float(( (1 + merged['daily_return']).prod() - 1 ) * 100.0)
        
        # 알파 (초과 수익률)
        alpha = strat_total_return_pct - bm_total_return_pct
        
        # 상관계수
        correlation = float(merged['daily_return'].corr(merged['daily_return_bm']))
        
        # Information Ratio (초과 수익률 / 추적 오차)
        excess_returns = merged['daily_return'] - merged['daily_return_bm']
        tracking_error = excess_returns.std() * np.sqrt(252)
        annualized_excess_return = ( (1 + excess_returns.mean()) ** 252 ) - 1
        
        information_ratio = float(annualized_excess_return / tracking_error) if tracking_error > 0 else 0.0
        
        return {
            'benchmark_return_pct': bm_total_return_pct,
            'benchmark_mdd_pct': bm_mdd_pct,
            'alpha': alpha,
            'correlation': correlation,
            'information_ratio': information_ratio
        }
        
    except Exception as e:
        print(f"벤치마크 비교 중 오류 발생: {e}")
        return {}


def compute_factor_importance(history: Optional[List[Dict]] = None, min_samples: int = 30) -> Dict[str, Any]:
    """
    태그/팩터가 성공률(승률)에 미치는 영향을 분석합니다.
    
    Args:
        history (Optional[List[Dict]]): 추천 히스토리 리스트.
        min_samples (int): 분석에 필요한 최소 샘플 수.
        
    Returns:
        Dict[str, Any]: 팩터별 분석 결과, 상관관계 매트릭스, 최적 가중치 제안.
    """
    if history is None:
        history = load_history()
        
    # 완료된 트레이드만 필터링
    completed = [h for h in history if h.get('is_completed', False)]
    
    if not completed:
        return {'factors': [], 'correlation_matrix': {}, 'optimal_weight_suggestion': {}}
        
    df = pd.DataFrame(completed)
    
    # 전체 베이스라인 승률
    baseline_win_rate = df['hit_success'].mean() if 'hit_success' in df.columns else 0.0
    
    # 모든 태그 정규화 및 수집
    all_tags = []
    for tags in df.get('tags', []):
        if isinstance(tags, list):
            norm_tags = [normalize_factor_tag(t) for t in tags]
            all_tags.extend(norm_tags)
            
    unique_tags = list(set(all_tags))
    
    factor_results = []
    
    # 팩터 출현 행렬 생성 (상관관계 분석용)
    tag_matrix = pd.DataFrame(0, index=df.index, columns=unique_tags)
    
    for idx, row in df.iterrows():
        tags = row.get('tags', [])
        if isinstance(tags, list):
            for t in tags:
                tag_matrix.at[idx, normalize_factor_tag(t)] = 1
                
    # 상관관계 매트릭스 계산
    corr_matrix_df = tag_matrix.corr().fillna(0)
    correlation_matrix = corr_matrix_df.to_dict()
    
    # 각 팩터별 성과 분석
    for tag in unique_tags:
        # 이 태그가 포함된 트레이드
        tag_mask = tag_matrix[tag] == 1
        samples = tag_mask.sum()
        
        if samples < min_samples:
            continue
            
        combined_win_rate = df[tag_mask]['hit_success'].mean()
        
        # 이 태그만 유일하게 존재하는 경우 (단독 팩터)
        solo_mask = tag_mask & (tag_matrix.sum(axis=1) == 1)
        solo_samples = solo_mask.sum()
        
        if solo_samples > 0:
            solo_win_rate = df[solo_mask]['hit_success'].mean()
        else:
            solo_win_rate = None
            
        lift = combined_win_rate - baseline_win_rate
        
        factor_results.append({
            'tag': tag,
            'solo_win_rate': solo_win_rate,
            'combined_win_rate': combined_win_rate,
            'lift': lift,
            'samples': int(samples)
        })
        
    # 최적 가중치 제안 (단순 휴리스틱: 승률 상승분(lift)에 비례)
    optimal_weights = {}
    valid_factors = [f for f in factor_results if f['lift'] > 0]
    
    if valid_factors:
        total_lift = sum(f['lift'] for f in valid_factors)
        for f in valid_factors:
            optimal_weights[f['tag']] = round(f['lift'] / total_lift, 3)
            
    return {
        'factors': sorted(factor_results, key=lambda x: x['lift'], reverse=True),
        'correlation_matrix': correlation_matrix,
        'optimal_weight_suggestion': optimal_weights
    }


def detect_regime_shift(history: Optional[List[Dict]] = None, window: int = 20) -> Dict[str, Any]:
    """
    최근 성과를 바탕으로 시장 체제(regime)의 변화를 감지합니다.
    
    Args:
        history (Optional[List[Dict]]): 추천 히스토리 리스트.
        window (int): 최근 성과를 평가할 윈도우 크기(완료된 트레이드 수 기준).
        
    Returns:
        Dict[str, Any]: 체제 변화 감지 결과 및 관련 지표.
    """
    if history is None:
        history = load_history()
        
    completed = [h for h in history if h.get('is_completed', False)]
    
    if len(completed) < window * 2:
        return {
            'regime_shifted': False,
            'z_score': 0.0,
            'recent_win_rate': 0.0,
            'overall_win_rate': 0.0,
            'recent_ev': 0.0,
            'overall_ev': 0.0,
            'recommendation': '데이터 부족'
        }
        
    df = pd.DataFrame(completed)
    df = df.sort_values(by='exit_date', ascending=True).reset_index(drop=True)
    
    # 전체 지표
    overall_win_rate = df['hit_success'].mean()
    overall_pnl = df['realized_pnl_net_pct'].fillna(0)
    overall_ev = overall_pnl.mean()
    
    # 윈도우 롤링 기반으로 과거 평균/표준편차 계산하여 Z-score 도출
    # 단순화를 위해 전체 평균과 최근 윈도우를 비교
    recent_df = df.iloc[-window:]
    historical_df = df.iloc[:-window]
    
    recent_win_rate = recent_df['hit_success'].mean()
    historical_win_rate_mean = historical_df['hit_success'].mean()
    
    # 승률을 이항 분포로 간주하여 표준 오차 계산
    p = historical_win_rate_mean
    n = window
    standard_error = np.sqrt(p * (1 - p) / n) if p > 0 and p < 1 else 1e-6
    
    z_score = (recent_win_rate - p) / standard_error if standard_error > 0 else 0.0
    
    recent_ev = recent_df['realized_pnl_net_pct'].fillna(0).mean()
    
    regime_shifted = abs(z_score) > 1.5
    
    if regime_shifted:
        if z_score < 0:
            rec = "현재 전략의 성과가 유의미하게 하락했습니다. 리스크 관리를 강화하고 포지션 크기를 줄이세요."
        else:
            rec = "성과가 비정상적으로 높습니다. 시장의 단기 과열 가능성을 주의하며 트레일링 스탑을 적극 활용하세요."
    else:
        rec = "정상적인 성과 범위 내에 있습니다. 기존 전략을 유지하세요."
        
    return {
        'regime_shifted': bool(regime_shifted),
        'z_score': float(z_score),
        'recent_win_rate': float(recent_win_rate),
        'overall_win_rate': float(overall_win_rate),
        'recent_ev': float(recent_ev),
        'overall_ev': float(overall_ev),
        'recommendation': rec
    }


def get_regime_performance_split(history: Optional[List[Dict]] = None) -> Dict[str, Any]:
    """
    시장 환경(Market Context) 체제별로 전략의 성과를 분리하여 분석합니다.
    
    Args:
        history (Optional[List[Dict]]): 추천 히스토리 리스트.
        
    Returns:
        Dict[str, Any]: 체제별(bullish, bearish, sideways 등) 성과 요약.
    """
    if history is None:
        history = load_history()
        
    completed = [h for h in history if h.get('is_completed', False)]
    
    if not completed:
        return {}
        
    df = pd.DataFrame(completed)
    
    # 기본 체제 키워드 정의
    regimes = ['bullish', 'bearish', 'sideways', 'unknown']
    results = {r: {} for r in regimes}
    
    # market_context가 없는 경우 'unknown'으로 처리
    if 'market_context' not in df.columns:
        df['market_context'] = 'unknown'
    else:
        df['market_context'] = df['market_context'].fillna('unknown').str.lower()
        
    # 체제 할당 로직
    def assign_regime(ctx):
        if 'bull' in ctx or '강세' in ctx or '상승' in ctx:
            return 'bullish'
        elif 'bear' in ctx or '약세' in ctx or '하락' in ctx:
            return 'bearish'
        elif 'side' in ctx or '횡보' in ctx or '박스' in ctx:
            return 'sideways'
        else:
            return 'unknown'
            
    df['regime'] = df['market_context'].apply(assign_regime)
    
    for regime in regimes:
        regime_df = df[df['regime'] == regime]
        
        sample_count = len(regime_df)
        
        if sample_count == 0:
            results[regime] = {
                'win_rate': 0.0,
                'avg_return': 0.0,
                'profit_factor': 0.0,
                'ev': 0.0,
                'sample_count': 0
            }
            continue
            
        win_rate = regime_df['hit_success'].mean()
        
        pnl = regime_df['realized_pnl_net_pct'].fillna(0)
        avg_return = pnl.mean()
        ev = avg_return
        
        # Profit Factor: 총 수익 / 총 손실 (절대값)
        gross_profit = pnl[pnl > 0].sum()
        gross_loss = abs(pnl[pnl < 0].sum())
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf') if gross_profit > 0 else 0.0
        
        results[regime] = {
            'win_rate': float(win_rate),
            'avg_return': float(avg_return),
            'profit_factor': float(profit_factor),
            'ev': float(ev),
            'sample_count': int(sample_count)
        }
        
    return results
