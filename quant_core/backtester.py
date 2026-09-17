"""
quant_core/backtester.py
2배 레버리지 ETF 전용 백테스팅 시뮬레이터:
- 전략 1: Supertrend + EMA 추세 추종 & Trailing Stop 전략 (하락장 침식 방어형)
- 전략 2: RSI 과매도 반등 + ATR 손절 전략 (단기 스윙형)
- 성과 지표 산출 (CAGR, MDD, 승률, 손익비, 단순보유 비교)
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, Tuple


def backtest_trend_following_strategy(
    df: pd.DataFrame,
    initial_capital: float = 10000.0,
    atr_multiplier: float = 2.0,
    fee_pct: float = 0.001  # 거래 수수료 0.1%
) -> Dict[str, Any]:
    """
    Supertrend 및 EMA 기반 추세 추종 백테스트.
    - 진입: Supertrend 상승 전환 + 종가 > EMA_21
    - 청산: Supertrend 하락 전환 또는 최고점 대비 (ATR * multiplier) 하락(추적 손절)
    """
    if df.empty or len(df) < 20:
        return {'error': '백테스팅을 위한 데이터가 부족합니다.'}
        
    data = df.copy()
    cash = initial_capital
    position = 0.0  # 보유 주식 수
    in_trade = False
    highest_price_since_entry = 0.0
    
    trades = []
    equity_curve = []
    
    for i in range(len(data)):
        date = data.index[i]
        row = data.iloc[i]
        close = float(row['Close'])
        low = float(row['Low'])
        atr = float(row.get('ATR_14', close * 0.03))
        supertrend_dir = row.get('Supertrend_Direction', 0)
        ema21 = float(row.get('EMA_21', close))
        
        # 현재 포트폴리오 가치
        current_equity = cash + (position * close)
        equity_curve.append({
            'Date': date,
            'Portfolio_Value': current_equity,
            'Benchmark_Value': initial_capital * (close / data.iloc[0]['Close'])
        })
        
        # 1. 매도(청산) 조건 체크
        if in_trade:
            highest_price_since_entry = max(highest_price_since_entry, close)
            trailing_stop_price = highest_price_since_entry - (atr * atr_multiplier)
            
            is_stop_loss = low <= trailing_stop_price
            is_trend_reversed = supertrend_dir == -1
            
            if is_stop_loss or is_trend_reversed:
                # 매도 실행
                sell_price = trailing_stop_price if is_stop_loss else close
                sell_price = max(sell_price, low)  # 갭하락 방영
                proceeds = position * sell_price * (1 - fee_pct)
                cash += proceeds
                
                entry_trade = trades[-1]
                pnl = proceeds - entry_trade['cost']
                pnl_pct = (sell_price / entry_trade['price'] - 1) * 100
                
                entry_trade.update({
                    'exit_date': date,
                    'exit_price': round(sell_price, 2),
                    'reason': '트레일링 손절' if is_stop_loss else '추세 하락 전환',
                    'pnl': round(pnl, 2),
                    'pnl_pct': round(pnl_pct, 2)
                })
                
                position = 0.0
                in_trade = False
                highest_price_since_entry = 0.0
                continue
                
        # 2. 매수(진입) 조건 체크
        if not in_trade:
            is_bullish_trend = supertrend_dir == 1 and close > ema21
            if is_bullish_trend:
                # 전액 매수 실행
                entry_price = close
                invest_amount = cash * (1 - fee_pct)
                position = invest_amount / entry_price
                cash = 0.0
                in_trade = True
                highest_price_since_entry = entry_price
                
                trades.append({
                    'entry_date': date,
                    'price': round(entry_price, 2),
                    'shares': round(position, 4),
                    'cost': round(invest_amount, 2)
                })
                
    # 아직 포지션을 쥐고 있는 경우 최종 청산 가정
    if in_trade and trades:
        last_close = float(data.iloc[-1]['Close'])
        proceeds = position * last_close * (1 - fee_pct)
        cash += proceeds
        entry_trade = trades[-1]
        pnl = proceeds - entry_trade['cost']
        pnl_pct = (last_close / entry_trade['price'] - 1) * 100
        entry_trade.update({
            'exit_date': data.index[-1],
            'exit_price': round(last_close, 2),
            'reason': '백테스트 종료 시점 보유',
            'pnl': round(pnl, 2),
            'pnl_pct': round(pnl_pct, 2)
        })
        
    equity_df = pd.DataFrame(equity_curve).set_index('Date')
    
    # 성과 지표 계산
    final_equity = equity_df['Portfolio_Value'].iloc[-1]
    total_return = ((final_equity / initial_capital) - 1) * 100
    
    # 벤치마크(단순보유) 수익률
    bm_final = equity_df['Benchmark_Value'].iloc[-1]
    bm_return = ((bm_final / initial_capital) - 1) * 100
    
    # MDD (최대 낙폭)
    roll_max = equity_df['Portfolio_Value'].cummax()
    drawdown = (equity_df['Portfolio_Value'] - roll_max) / roll_max * 100
    mdd = abs(float(drawdown.min()))
    
    bm_roll_max = equity_df['Benchmark_Value'].cummax()
    bm_drawdown = (equity_df['Benchmark_Value'] - bm_roll_max) / bm_roll_max * 100
    bm_mdd = abs(float(bm_drawdown.min()))
    
    # 거래 통계
    closed_trades = [t for t in trades if 'pnl' in t]
    total_trades = len(closed_trades)
    winning_trades = [t for t in closed_trades if t['pnl'] > 0]
    win_rate = (len(winning_trades) / total_trades * 100) if total_trades > 0 else 0.0
    
    gross_profits = sum(t['pnl'] for t in winning_trades)
    gross_losses = abs(sum(t['pnl'] for t in closed_trades if t['pnl'] < 0))
    profit_factor = (gross_profits / gross_losses) if gross_losses > 0 else (999.0 if gross_profits > 0 else 0.0)
    
    return {
        'strategy_name': 'Supertrend 추세 추종 & Trailing Stop',
        'initial_capital': initial_capital,
        'final_equity': round(final_equity, 2),
        'total_return_pct': round(total_return, 2),
        'benchmark_return_pct': round(bm_return, 2),
        'mdd_pct': round(mdd, 2),
        'benchmark_mdd_pct': round(bm_mdd, 2),
        'total_trades': total_trades,
        'win_rate_pct': round(win_rate, 2),
        'profit_factor': round(profit_factor, 2),
        'trades': closed_trades,
        'equity_df': equity_df
    }
