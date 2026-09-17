"""
scripts/daily_scan.py
매일 미국 장 마감 후 자동 실행되는 일일 퀀트 스캔 및 성과 학습 자동화 스크립트
"""

import os
import sys
from datetime import datetime

# 프로젝트 루트 경로 추가
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from quant_core.screener import run_full_market_scan
from quant_core.tracker import evaluate_and_learn_from_history, record_daily_recommendations


def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🚀 일일 나스닥 82개 종목 자동 퀀트 스캔 & 학습 시작")

    # 1. 기존 추천 종목 성과 추적 및 학습 모델 업데이트
    print("\n📊 1단계: 과거 추천 종목 실전 성과 추적 및 팩터 학습 중...")
    eval_result = evaluate_and_learn_from_history()
    print(f"  • 누적 표본수: {eval_result['total_recs']}개 (완료: {eval_result['completed_count']}개, 추적중: {eval_result['ongoing_count']}개)")
    print(f"  • 현재 공식 승률: {eval_result['win_rate']}% · 평균 수익률: {eval_result['avg_return']:+}%")

    adaptive = eval_result.get('adaptive_weights', {})
    if adaptive.get('penalized_factors'):
        print("\n  ⚠️ [실패 피드백 감점 팩터]:")
        for pf in adaptive['penalized_factors']:
            print(f"    - '{pf['tag']}': 승률 {pf['win_rate']}% ➡️ {pf['adj']}")
    if adaptive.get('boosted_factors'):
        print("  🎯 [고승률 가산 팩터]:")
        for bf in adaptive['boosted_factors']:
            print(f"    - '{bf['tag']}': 승률 {bf['win_rate']}% ➡️ {bf['adj']}")
    if adaptive.get('cooldown_tickers'):
        print(f"  🧊 [최근 손절 쿨다운 종목]: {', '.join(adaptive['cooldown_tickers'].keys())}")


    # 2. 82개 종목 전체 병렬 스캔 실행
    print("\n🔍 2단계: 82개 유니버스 병렬 스캔 및 오늘의 Top 7 선별 중...")
    recs = run_full_market_scan(force_refresh=True)

    if recs:
        print(f"  ✅ 스캔 완료! 선별된 유망 종목: {len(recs)}개")
        for i, r in enumerate(recs, 1):
            curr_p = r['current_price']
            t1 = r.get('bull_target_1', curr_p * 1.08)
            t1_pct = ((t1 / curr_p) - 1) * 100 if curr_p > 0 else 0.0
            print(f"    {i}. [{r['ticker']}] {r['name']} | 현재가: ${curr_p:.2f} | 1차목표가: ${t1:.2f} (+{t1_pct:.1f}%) | 점수: {r['total_score']}점")

        
        # 3. 새로운 추천 종목 데이터베이스에 등록
        print("\n💾 3단계: 오늘자 신규 추천 종목 영구 데이터베이스에 기록...")
        record_daily_recommendations(recs)
        print("  ✅ 데이터베이스 기록 및 JSON 동기화 완료!")
    else:
        print("  ⚠️ 추천 조건에 부합하는 종목이 없습니다.")

    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ✨ 모든 일일 작업이 성공적으로 완료되었습니다.\n")


if __name__ == '__main__':
    main()
