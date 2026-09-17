"""
quant_core/prediction.py
기술적 지표 종합 진단 및 확률론적 가격 밴드 예측 모듈:
- 다중 기술적 지표 결합 종합 점수 (-100 ~ +100) 및 시그널 판정
- 2배 레버리지 종목의 고유 변동성(ATR 및 일일 표준편차)을 반영한 단기(5일)/중기(20일) 가격 시나리오
- 권장 손절선(Stop Loss) 및 익절 목표가(Take Profit) 자동 산출
"""

import pandas as pd
import numpy as np
from typing import Dict, Any
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


def predict_price_scenarios(df: pd.DataFrame, days_ahead: int = 5) -> Dict[str, Any]:
    """
    고변동성 2X 종목의 ATR 및 일일 변동성을 기반으로 단기 시나리오별 가격 밴드를 예측합니다.
    """
    if df.empty or len(df) < 5:
        return {}
        
    last = df.iloc[-1]
    current_price = float(last['Close'])
    atr = float(last['ATR_14']) if 'ATR_14' in last and not np.isnan(last['ATR_14']) else current_price * 0.04
    
    sr = get_support_resistance_levels(df)
    
    # 단기 변동폭 계산 (기간에 따른 제곱근 변동성 반영)
    time_factor = np.sqrt(days_ahead)
    expected_move = atr * time_factor
    
    # 1. Bull Target (상승 추세 시 1차/2차 목표가)
    r1 = sr['resistances'][0] if sr['resistances'] else (current_price + expected_move)
    r2 = sr['resistances'][1] if len(sr['resistances']) > 1 else (current_price + (expected_move * 1.5))
    
    bull_target_1 = max(round(r1, 2), round(current_price + expected_move * 0.8, 2))
    bull_target_2 = max(round(r2, 2), round(current_price + expected_move * 1.4, 2))
    
    # 2. Base Scenario (현재 추세 지속 시 예상치)
    direction = last.get('Supertrend_Direction', 1)
    base_target = round(current_price + (expected_move * 0.3 * direction), 2)
    
    # 3. Bear Support (하락 시 1차/2차 지지선)
    s1 = sr['supports'][0] if sr['supports'] else (current_price - expected_move)
    s2 = sr['supports'][1] if len(sr['supports']) > 1 else (current_price - (expected_move * 1.5))
    
    bear_support_1 = min(round(s1, 2), round(current_price - expected_move * 0.8, 2))
    bear_support_2 = min(round(s2, 2), round(current_price - expected_move * 1.4, 2))
    
    # 4. 권장 손절가 (Stop Loss)
    # Supertrend 기준선 또는 ATR 1.5배 이탈 지점
    if 'Supertrend' in last and not np.isnan(last['Supertrend']) and direction == 1:
        stop_loss = round(min(last['Supertrend'], current_price - (atr * 1.5)), 2)
    else:
        stop_loss = round(current_price - (atr * 1.5), 2)
        
    # 손익비 (Risk-Reward Ratio)
    potential_reward = bull_target_1 - current_price
    potential_risk = current_price - stop_loss
    rr_ratio = round(potential_reward / potential_risk, 2) if potential_risk > 0 else 0.0
    
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
        'resistances': sr['resistances'],
        'supports': sr['supports']
    }
