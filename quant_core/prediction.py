"""
quant_core/prediction.py
기술적 지표 종합 진단 및 확률론적 가격 밴드 예측 모듈:
- 다중 기술적 지표 결합 종합 점수 (-100 ~ +100) 및 시그널 판정
- 2배 레버리지 종목의 고유 변동성(ATR 및 일일 표준편차)을 반영한 단기(5일)/중기(20일) 가격 시나리오
- 권장 손절선(Stop Loss) 및 익절 목표가(Take Profit) 자동 산출
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List
from .indicators import get_support_resistance_levels


def evaluate_technical_health(df: pd.DataFrame) -> Dict[str, Any]:
    """
    최신 데이터프레임을 분석하여 종합 기술적 건전도 및 매매 시그널을 산출합니다.
    """
    if df.empty or len(df) < 5:
        return {'score': 0, 'signal': '데이터 부족', 'details': []}
        
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last
    
    score = 0
    details = []
    
    close = float(last['Close'])
    
    # 1. 추세 분석 (최대 40점)
    # 1-1. Supertrend (20점)
    if 'Supertrend_Direction' in last:
        if last['Supertrend_Direction'] == 1:
            score += 20
            details.append(("Supertrend", "+20", "상승 추세 유지 중 (Bullish)"))
        else:
            score -= 20
            details.append(("Supertrend", "-20", "하락 추세 지속 중 (Bearish)"))
            
    # 1-2. EMA 정배열 / 역배열 (20점)
    if 'EMA_9' in last and 'EMA_21' in last:
        ema9 = float(last['EMA_9'])
        ema21 = float(last['EMA_21'])
        if close > ema9 > ema21:
            score += 20
            details.append(("EMA 배열", "+20", "단기 정배열 (주가 > EMA9 > EMA21)"))
        elif close < ema9 < ema21:
            score -= 20
            details.append(("EMA 배열", "-20", "단기 역배열 (주가 < EMA9 < EMA21)"))
        elif close > ema21:
            score += 10
            details.append(("EMA 배열", "+10", "EMA21선 상회 중 (단기 지지)"))
        else:
            score -= 10
            details.append(("EMA 배열", "-10", "EMA21선 하회 중 (단기 저항)"))
            
    # 2. 모멘텀 분석 (최대 30점)
    # 2-1. RSI (15점)
    if 'RSI_14' in last:
        rsi = float(last['RSI_14'])
        if rsi < 30:
            score += 10  # 과매도 반등 가능성
            details.append(("RSI(14)", "+10", f"단기 과매도 구간 ({rsi:.1f}) - 기술적 반등 기대"))
        elif rsi > 70:
            score -= 10  # 과열 조정 위험
            details.append(("RSI(14)", "-10", f"단기 과매수/과열 구간 ({rsi:.1f}) - 단기 차익실현 주의"))
        elif 50 <= rsi <= 70:
            score += 15
            details.append(("RSI(14)", "+15", f"강세 모멘텀 구간 ({rsi:.1f})"))
        else:
            score -= 10
            details.append(("RSI(14)", "-10", f"약세 모멘텀 구간 ({rsi:.1f})"))
            
    # 2-2. MACD (15점)
    if 'MACD' in last and 'MACD_Signal' in last:
        macd = float(last['MACD'])
        macd_sig = float(last['MACD_Signal'])
        macd_hist = float(last['MACD_Hist'])
        prev_hist = float(prev['MACD_Hist'])
        
        if macd > macd_sig:
            if macd_hist > prev_hist:
                score += 15
                details.append(("MACD", "+15", "MACD 골든크로스 및 상승 가속화"))
            else:
                score += 5
                details.append(("MACD", "+5", "MACD 시그널선 상회 (모멘텀 둔화)"))
        else:
            if macd_hist < prev_hist:
                score -= 15
                details.append(("MACD", "-15", "MACD 데드크로스 및 하락 가속화"))
            else:
                score -= 5
                details.append(("MACD", "-5", "MACD 시그널선 하회 (하락세 완화)"))
                
    # 3. 변동성 & 거래량 (최대 30점)
    # 3-1. 볼린저 밴드 위치 (15점)
    if 'BB_Lower' in last and 'BB_Upper' in last and 'BB_Middle' in last:
        bb_u = float(last['BB_Upper'])
        bb_l = float(last['BB_Lower'])
        bb_m = float(last['BB_Middle'])
        
        if close > bb_u:
            score += 5
            details.append(("볼린저 밴드", "+5", "상단 밴드 상향 돌파 (강한 추세 / 단기 되돌림 주의)"))
        elif close < bb_l:
            score += 5
            details.append(("볼린저 밴드", "+5", "하단 밴드 이탈 (단기 과매도 낙폭과대)"))
        elif close > bb_m:
            score += 10
            details.append(("볼린저 밴드", "+10", "볼린저 밴드 중심선(20일선) 위에서 지지"))
        else:
            score -= 10
            details.append(("볼린저 밴드", "-10", "볼린저 밴드 중심선(20일선) 아래에 위치"))
            
    # 3-2. 거래량 확인 (15점)
    if 'Volume' in last and 'Volume_SMA_20' in last:
        vol = float(last['Volume'])
        vol_sma = float(last['Volume_SMA_20'])
        if vol_sma > 0:
            vol_ratio = (vol / vol_sma) * 100
            if close >= prev['Close'] and vol_ratio > 130:
                score += 15
                details.append(("거래량", "+15", f"거래량 급증 동반 상승 (평균 대비 {vol_ratio:.0f}%)"))
            elif close < prev['Close'] and vol_ratio > 130:
                score -= 15
                details.append(("거래량", "-15", f"거래량 급증 동반 하락 (투매 주의, 평균 대비 {vol_ratio:.0f}%)"))
            else:
                details.append(("거래량", "0", f"평상시 거래량 수준 ({vol_ratio:.0f}%)"))

    # 총점 정규화 (-100 ~ +100)
    score = max(-100, min(100, score))
    
    # 시그널 판정
    if score >= 50:
        signal = "강력 매수 (Strong Buy)"
        color = "green"
    elif score >= 15:
        signal = "매수 우위 (Buy / Accumulate)"
        color = "lightgreen"
    elif score >= -15:
        signal = "중립 / 관망 (Neutral / Hold)"
        color = "orange"
    elif score >= -50:
        signal = "매도 우위 (Sell / Reduce)"
        color = "salmon"
    else:
        signal = "적극 리스크 관리 (Strong Sell / Cut Loss)"
        color = "red"
        
    return {
        'score': score,
        'signal': signal,
        'color': color,
        'details': details
    }


def find_volume_profile_nodes(df: pd.DataFrame, bins: int = 15) -> Dict[str, List[float]]:
    """최근 60거래일 거래량 집중 매물대(Volume Profile Nodes)를 추출합니다."""
    if len(df) < 10:
        return {'resistance_nodes': [], 'support_nodes': []}
    
    recent = df.tail(min(60, len(df))).copy()
    current_price = float(df['Close'].iloc[-1])
    
    price_min = float(recent['Low'].min())
    price_max = float(recent['High'].max())
    if price_max <= price_min:
        return {'resistance_nodes': [], 'support_nodes': []}
        
    price_bins = np.linspace(price_min, price_max, bins + 1)
    typical_prices = (recent['High'] + recent['Low'] + recent['Close']) / 3.0
    vol_hist, _ = np.histogram(typical_prices, bins=price_bins, weights=recent['Volume'])
    
    mean_vol = np.mean(vol_hist)
    high_vol_indices = np.where(vol_hist > mean_vol * 1.2)[0]
    
    r_nodes = []
    s_nodes = []
    for idx in high_vol_indices:
        node_price = (price_bins[idx] + price_bins[idx + 1]) / 2.0
        if node_price > current_price * 1.015:
            r_nodes.append(round(node_price, 2))
        elif node_price < current_price * 0.985:
            s_nodes.append(round(node_price, 2))
            
    r_nodes.sort()
    s_nodes.sort(reverse=True)
    return {'resistance_nodes': r_nodes, 'support_nodes': s_nodes}


def predict_price_scenarios(df: pd.DataFrame, days_ahead: int = 5) -> Dict[str, Any]:
    """
    실제 구조적 저항선, 거래량 매물대, 스윙 고점/저점, ATR 변동성을 결합하여
    종목 고유의 현실적인 목표가, 손절가 및 동적 손익비(Risk-Reward Ratio)를 산출합니다.
    """
    if df.empty or len(df) < 5:
        return {}
        
    last = df.iloc[-1]
    current_price = float(last['Close'])
    atr = float(last['ATR_14']) if 'ATR_14' in last and not np.isnan(last['ATR_14']) else current_price * 0.04
    direction = last.get('Supertrend_Direction', 1)
    
    # 1. 구조적 상방 저항 후보군 집계 (피봇 저항선, 매물대, 20일 스윙 고점)
    sr = get_support_resistance_levels(df)
    vp = find_volume_profile_nodes(df)
    swing_high_20 = float(df['High'].tail(min(20, len(df))).max())
    
    resistance_pool = set()
    for r in sr.get('resistances', []):
        if r > current_price * 1.01:
            resistance_pool.add(round(float(r), 2))
    for r in vp.get('resistance_nodes', []):
        if r > current_price * 1.015:
            resistance_pool.add(round(float(r), 2))
    if swing_high_20 > current_price * 1.01:
        resistance_pool.add(round(swing_high_20, 2))
        
    sorted_resistances = sorted(list(resistance_pool))
    
    # 1차 목표가 (bull_target_1): 상방에서 실제로 먼저 부딪힐 구조적 저항선
    atr_cap = round(current_price + (atr * 2.5), 2)
    atr_floor = round(current_price + max(atr * 0.8, current_price * 0.035), 2)
    
    valid_near_r = [r for r in sorted_resistances if r >= atr_floor and r <= atr_cap]
    if valid_near_r:
        bull_target_1 = valid_near_r[0]
    else:
        bull_target_1 = round(current_price + (atr * 1.4), 2)
        
    # 2차 목표가 (bull_target_2): 1차 목표가 상방의 다음 유효 저항선
    valid_far_r = [r for r in sorted_resistances if r > bull_target_1 * 1.025]
    if valid_far_r:
        bull_target_2 = min(valid_far_r[0], round(bull_target_1 + (atr * 1.8), 2))
    else:
        bull_target_2 = round(bull_target_1 + (atr * 1.2), 2)
        
    # 2. 구조적 하방 지지 후보군 집계 (피봇 지지선, 매물대, 20일 스윙 저점, Supertrend)
    support_pool = set()
    for s in sr.get('supports', []):
        if s < current_price * 0.99:
            support_pool.add(round(float(s), 2))
    for s in vp.get('support_nodes', []):
        if s < current_price * 0.985:
            support_pool.add(round(float(s), 2))
    swing_low_20 = float(df['Low'].tail(min(20, len(df))).min())
    if swing_low_20 < current_price * 0.99:
        support_pool.add(round(swing_low_20, 2))
    if 'Supertrend' in last and not np.isnan(last['Supertrend']) and direction == 1:
        st_val = float(last['Supertrend'])
        if st_val < current_price * 0.99:
            support_pool.add(round(st_val, 2))
            
    sorted_supports = sorted(list(support_pool), reverse=True)
    
    # 손절가 (stop_loss): 바로 아래 구조적 지지 붕괴선과 ATR 기반 안전선 조화
    atr_sl = round(current_price - (atr * 1.5), 2)
    min_safe_sl = round(current_price * 0.92, 2)  # 최대 손절 마지노선 (-8%)
    
    valid_near_s = [s for s in sorted_supports if s <= current_price * 0.985 and s >= min_safe_sl]
    if valid_near_s:
        structural_sl = round(valid_near_s[0] * 0.995, 2)
        # 구조적 지지선과 ATR 기준선 중 '더 여유 있는(먼)' 쪽을 채택한다.
        # 기존 max()는 둘 중 타이트한 쪽을 골라, 바로 아래 지지선이 가까우면
        # 손절선이 종목의 정상 변동폭(ATR) 안쪽에 놓여 노이즈에 확정 손절되었다.
        # (실측: 설정 손절폭 중앙값 -2.8% vs 실제 MAE 중앙값 -8.9%, 손절 터치율 80.8%)
        # ATR 기준선은 변동성 하한선 역할을 해야 하므로 min()이 옳다.
        stop_loss = min(structural_sl, atr_sl)
    else:
        stop_loss = atr_sl

    # 최대 손실 마지노선(-8%)보다 더 내려가지 않도록 clamp
    stop_loss = max(stop_loss, min_safe_sl)
    
    # 3. 실제 동적 손익비 (Risk-Reward Ratio) 산출
    potential_reward = max(0.01, bull_target_1 - current_price)
    potential_risk = max(0.01, current_price - stop_loss)
    rr_ratio = round(potential_reward / potential_risk, 2) if potential_risk > 0 else 0.0
    
    time_factor = np.sqrt(days_ahead)
    expected_move = atr * time_factor
    base_target = round(current_price + (expected_move * 0.3 * direction), 2)
    bear_support_1 = min(round(s, 2) for s in sorted_supports) if sorted_supports else round(current_price - expected_move * 0.8, 2)
    bear_support_2 = round(current_price - expected_move * 1.4, 2)
    
    return {
        'days_ahead': days_ahead,
        'current_price': current_price,
        'atr': round(atr, 2),
        'atr_pct': round((atr / current_price) * 100, 2),
        'bull_target_1': bull_target_1,
        'bull_target_2': bull_target_2,
        'base_target': base_target,
        'bear_support_1': bear_support_1,
        'bear_support_2': bear_support_2,
        'stop_loss': stop_loss,
        'stop_loss_pct': round(((stop_loss - current_price) / current_price) * 100, 2),
        'risk_reward_ratio': rr_ratio,
        'resistances': sorted_resistances,
        'supports': sorted_supports
    }
