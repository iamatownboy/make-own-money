"""
run_analysis.py
사용자 실제 보유 포트폴리오 맞춤형 정밀 차트 분석 & 전략 리포트 출력 스크립트
"""

import sys
import json
import os
import pandas as pd
import numpy as np
from quant_core.data_loader import fetch_stock_data, get_latest_quote_info, PORTFOLIO_CONFIG
from quant_core.indicators import calculate_all_indicators, get_support_resistance_levels
from quant_core.prediction import evaluate_technical_health, predict_price_scenarios
from quant_core.backtester import backtest_trend_following_strategy


def load_user_portfolio():
    path = os.path.join(os.path.dirname(__file__), 'user_portfolio.json')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def print_banner():
    print("=" * 80)
    print(" 🚀 사용자 실제 계좌 기반 나스닥 레버리지 포트폴리오 정밀 진단 & 차트 분석")
    print("=" * 80)


def run_portfolio_analysis(period: str = '6mo'):
    print_banner()
    user_data = load_user_portfolio()
    holdings = user_data.get('holdings', {})
    
    summary_rows = []
    
    for ticker, cfg in PORTFOLIO_CONFIG.items():
        user_h = holdings.get(ticker, None)
        h_info = f" [보유: {user_h['shares']}주 | 평단가 ${user_h['avg_price']:.2f}]" if user_h else ""
        print(f"\n▶ [{ticker}] {cfg['name']}{h_info}")
        
        # 1. 데이터 수집
        df = fetch_stock_data(ticker, period=period)
        if df.empty or len(df) < 5:
            print(f"  ❌ {ticker} 시세 데이터를 불러오지 못했습니다.")
            continue
            
        # 2. 지표 계산
        df = calculate_all_indicators(df)
        
        # 3. 진단 및 예측
        health = evaluate_technical_health(df)
        pred = predict_price_scenarios(df, days_ahead=5)
        
        # 4. 백테스트 시뮬레이션
        bt = backtest_trend_following_strategy(df)
        
        # 콘솔 세부 출력
        curr_p = pred['current_price']
        atr = pred['atr']
        atr_pct = pred['atr_pct']
        signal = health['signal']
        score = health['score']
        stop_loss = pred['stop_loss']
        sl_pct = pred['stop_loss_pct']
        bull_1 = pred['bull_target_1']
        bull_2 = pred['bull_target_2']
        bear_1 = pred['bear_support_1']
        
        # 사용자 계좌 상태
        if user_h:
            shares = user_h['shares']
            avg_p = user_h['avg_price']
            eval_amt = curr_p * shares
            cost_amt = user_h['cost_basis']
            pnl = eval_amt - cost_amt
            pnl_pct = (pnl / cost_amt) * 100
            print(f"  ├─ 내 계좌 현황: 평가금 ${eval_amt:.2f} | 손익: ${pnl:+.2f} ({pnl_pct:+.2f}%)")
            
        print(f"  ├─ 현재 종가: ${curr_p:.2f} | 일일 변동폭(ATR): ${atr:.2f} (±{atr_pct}%)")
        print(f"  ├─ 기술적 진단: [{signal}] (기술 점수: {score:+d}점 / 100점)")
        print(f"  ├─ 주요 지표 상태:")
        for name, pts, desc in health['details'][:3]:
            print(f"  │   • {name} ({pts}): {desc}")
            
        print(f"  ├─ 단기(5일) 가격 시나리오:")
        print(f"  │   • 1차 목표가 (Bull 1): ${bull_1:.2f} ({(bull_1/curr_p - 1)*100:+.1f}%)")
        print(f"  │   • 2차 목표가 (Bull 2): ${bull_2:.2f} ({(bull_2/curr_p - 1)*100:+.1f}%)")
        print(f"  │   • 핵심 지지선 (Bear 1): ${bear_1:.2f} ({(bear_1/curr_p - 1)*100:+.1f}%)")
        print(f"  │   • 권장 손절선 (Stop Loss): ${stop_loss:.2f} ({sl_pct:+.1f}%)")
        
        if 'total_return_pct' in bt:
            strat_ret = bt['total_return_pct']
            bm_ret = bt['benchmark_return_pct']
            strat_mdd = bt['mdd_pct']
            bm_mdd = bt['benchmark_mdd_pct']
            print(f"  └─ 6개월 백테스트 (추세추종&손절 vs 단순보유):")
            print(f"      • 전략 수익률: {strat_ret:+.1f}% (MDD: -{strat_mdd:.1f}%) | 거래: {bt['total_trades']}회 (승률 {bt['win_rate_pct']}%)")
            print(f"      • 단순 보유시: {bm_ret:+.1f}% (MDD: -{bm_mdd:.1f}%)")
            
        summary_rows.append({
            '티커': ticker,
            '보유수량': f"{user_h['shares']}주" if user_h else "-",
            '내평단가': f"${user_h['avg_price']:.2f}" if user_h else "-",
            '현재가': f"${curr_p:.2f}",
            '내수익률': f"{((curr_p/user_h['avg_price'] - 1)*100):+.1f}%" if user_h else "-",
            '종합진단': signal,
            '손절가(SL)': f"${stop_loss:.2f}",
            '1차목표가': f"${bull_1:.2f}"
        })
        
    print("\n" + "=" * 95)
    print(" 📊 내 포트폴리오 종목별 현황 및 핵심 가격 요약표")
    print("=" * 95)
    summary_df = pd.DataFrame(summary_rows)
    print(summary_df.to_string(index=False))
    print("=" * 95)
    print(" 💡 웹 브라우저 차트 대시보드 실행: 'streamlit run app.py'\n")


if __name__ == '__main__':
    period = sys.argv[1] if len(sys.argv) > 1 else '6mo'
    run_portfolio_analysis(period)
