"""
quant_core/indicators.py
고변동성/레버리지 종목을 위한 기술적 지표 계산 모듈:
- EMA (9, 21, 50, 120, 200)
- Supertrend (10, 3)
- RSI (14) & MACD (12, 26, 9)
- Bollinger Bands (20, 2)
- ATR (14) - 변동성 및 손절선 기준
- Swing High/Low 기반 주요 지지선 & 저항선 산출
- 피봇 포인트 (P, R1, R2, S1, S2)
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Any


def calculate_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    OHLCV 데이터프레임에 모든 핵심 기술적 지표를 계산하여 추가합니다.
    """
    if df.empty or len(df) < 5:
        return df
    
    data = df.copy()
    close = data['Close']
    high = data['High']
    low = data['Low']
    
    # 1. EMA (지수이동평균)
    data['EMA_9'] = close.ewm(span=9, adjust=False).mean()
    data['EMA_21'] = close.ewm(span=21, adjust=False).mean()
    data['EMA_50'] = close.ewm(span=50, adjust=False).mean() if len(data) >= 50 else close.ewm(span=len(data), adjust=False).mean()
    data['EMA_120'] = close.ewm(span=120, adjust=False).mean() if len(data) >= 120 else np.nan
    data['EMA_200'] = close.ewm(span=200, adjust=False).mean() if len(data) >= 200 else np.nan
    
    # 2. RSI (Relative Strength Index, 14)
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14, min_periods=1).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14, min_periods=1).mean()
    rs = gain / loss.replace(0, np.nan)
    data['RSI_14'] = 100 - (100 / (1 + rs))
    data['RSI_14'] = data['RSI_14'].fillna(50)
    
    # 3. MACD (12, 26, 9)
    ema_12 = close.ewm(span=12, adjust=False).mean()
    ema_26 = close.ewm(span=26, adjust=False).mean()
    data['MACD'] = ema_12 - ema_26
    data['MACD_Signal'] = data['MACD'].ewm(span=9, adjust=False).mean()
    data['MACD_Hist'] = data['MACD'] - data['MACD_Signal']
    
    # 4. 볼린저 밴드 (20, 2)
    bb_window = min(20, len(data))
    data['BB_Middle'] = close.rolling(window=bb_window, min_periods=1).mean()
    bb_std = close.rolling(window=bb_window, min_periods=1).std().fillna(0)
    data['BB_Upper'] = data['BB_Middle'] + (bb_std * 2)
    data['BB_Lower'] = data['BB_Middle'] - (bb_std * 2)
    data['BB_Width'] = ((data['BB_Upper'] - data['BB_Lower']) / data['BB_Middle']).replace(0, np.nan)
    
    # 5. ATR (Average True Range, 14)
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    data['ATR_14'] = tr.rolling(window=14, min_periods=1).mean()
    
    # 6. Supertrend (10, 3)
    data = calculate_supertrend(data, period=10, multiplier=3.0)
    
    # 7. 거래량 이동평균
    data['Volume_SMA_20'] = data['Volume'].rolling(window=min(20, len(data)), min_periods=1).mean()
    
    return data


def calculate_supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """
    수퍼트렌드(Supertrend) 지표 계산 (표준 공식).
    고변동성/레버리지 종목의 추세 반전 및 손절 포인트를 잡는 핵심 지표.
    """
    high = df['High'].values.flatten()
    low = df['Low'].values.flatten()
    close = df['Close'].values.flatten()
    
    n = len(df)
    if n == 0:
        return df
        
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    atr = pd.Series(tr).rolling(window=period, min_periods=1).mean().values
    
    hl2 = (high + low) / 2.0
    basic_upper = hl2 + (multiplier * atr)
    basic_lower = hl2 - (multiplier * atr)
    
    final_upper = np.zeros(n)
    final_lower = np.zeros(n)
    supertrend = np.zeros(n)
    direction = np.ones(n)
    
    final_upper[0] = basic_upper[0]
    final_lower[0] = basic_lower[0]
    
    for i in range(1, n):
        if (basic_upper[i] < final_upper[i-1]) or (close[i-1] > final_upper[i-1]):
            final_upper[i] = basic_upper[i]
        else:
            final_upper[i] = final_upper[i-1]
            
        if (basic_lower[i] > final_lower[i-1]) or (close[i-1] < final_lower[i-1]):
            final_lower[i] = basic_lower[i]
        else:
            final_lower[i] = final_lower[i-1]
            
        if direction[i-1] == 1:
            if close[i] < final_lower[i]:
                direction[i] = -1
                supertrend[i] = final_upper[i]
            else:
                direction[i] = 1
                supertrend[i] = final_lower[i]
        else:
            if close[i] > final_upper[i]:
                direction[i] = 1
                supertrend[i] = final_lower[i]
            else:
                direction[i] = -1
                supertrend[i] = final_upper[i]
                
    df['Supertrend'] = supertrend
    df['Supertrend_Direction'] = direction  # 1 (Bull) or -1 (Bear)
    return df



def get_support_resistance_levels(df: pd.DataFrame, window: int = 20) -> Dict[str, List[float]]:
    """
    최근 가격 데이터에서 주요 지지선(Support)과 저항선(Resistance)을 추출합니다.
    """
    if len(df) < window:
        window = len(df)
        
    recent = df.tail(window)
    current_price = float(df['Close'].iloc[-1])
    
    # 국소 고점과 저점 탐색
    highs = recent['High'].values
    lows = recent['Low'].values
    
    resistance_levels = []
    support_levels = []
    
    # 최근 최고가 및 최저가
    resistance_levels.append(float(recent['High'].max()))
    support_levels.append(float(recent['Low'].min()))
    
    # 피봇 포인트 계산 (직전 일봉 기준)
    last = df.iloc[-1]
    h, l, c = float(last['High']), float(last['Low']), float(last['Close'])
    pivot = (h + l + c) / 3
    r1 = (2 * pivot) - l
    s1 = (2 * pivot) - h
    r2 = pivot + (h - l)
    s2 = pivot - (h - l)
    
    if r1 > current_price:
        resistance_levels.append(round(r1, 2))
    if r2 > current_price:
        resistance_levels.append(round(r2, 2))
    if s1 < current_price:
        support_levels.append(round(s1, 2))
    if s2 < current_price:
        support_levels.append(round(s2, 2))
        
    # 이동평균선(EMA 50, EMA 20)도 동적 지지/저항 역할
    if 'EMA_50' in df.columns and not np.isnan(df['EMA_50'].iloc[-1]):
        ema_50 = float(df['EMA_50'].iloc[-1])
        if ema_50 > current_price:
            resistance_levels.append(round(ema_50, 2))
        else:
            support_levels.append(round(ema_50, 2))
            
    # 정렬 및 중복 제거
    resistance_levels = sorted(list(set([r for r in resistance_levels if r > current_price])))
    support_levels = sorted(list(set([s for s in support_levels if s < current_price])), reverse=True)
    
    return {
        'current_price': current_price,
        'pivot': round(pivot, 2),
        'resistances': resistance_levels[:3],  # 가까운 상위 3개
        'supports': support_levels[:3]          # 가까운 하위 3개
    }


def calculate_fibonacci_levels(df: pd.DataFrame, window: int = 60) -> Dict[str, Any]:
    """
    최근 N봉의 스윙 고점과 저점을 기준으로 피보나치 되돌림 레벨을 자동 산출하고,
    현재가가 0.500~0.618 황금 지지존(Golden Pocket)에 위치하는지 분석합니다.
    """
    if len(df) < 15:
        return {}
        
    recent = df.tail(min(window, len(df)))
    swing_high = float(recent['High'].max())
    swing_low = float(recent['Low'].min())
    diff = swing_high - swing_low
    
    if diff <= 0:
        return {}
        
    fib_0 = swing_high
    fib_236 = round(swing_high - (diff * 0.236), 2)
    fib_382 = round(swing_high - (diff * 0.382), 2)
    fib_500 = round(swing_high - (diff * 0.500), 2)
    fib_618 = round(swing_high - (diff * 0.618), 2)
    fib_786 = round(swing_high - (diff * 0.786), 2)
    fib_100 = swing_low
    
    current_price = float(df['Close'].iloc[-1])
    
    # 골든 포켓(0.5 ~ 0.618) 또는 0.382 지지 여부 판정
    is_in_golden_pocket = (fib_618 * 0.98) <= current_price <= (fib_500 * 1.02)
    is_at_fib_382 = (fib_382 * 0.98) <= current_price <= (fib_382 * 1.03)
    
    status_desc = "관망 구간"
    if is_in_golden_pocket:
        status_desc = "피보나치 0.618~0.5 지지 구간 (되돌림 지지 타점)"
    elif is_at_fib_382:
        status_desc = "피보나치 0.382 지지 (단기 추세 지속형)"
    elif current_price > fib_236:
        status_desc = "신고가 영역 돌파 준비"
        
    return {
        'swing_high': round(swing_high, 2),
        'swing_low': round(swing_low, 2),
        'fib_236': fib_236,
        'fib_382': fib_382,
        'fib_500': fib_500,
        'fib_618': fib_618,
        'fib_786': fib_786,
        'is_in_golden_pocket': is_in_golden_pocket,
        'is_at_fib_382': is_at_fib_382,
        'status_desc': status_desc
    }


def detect_trendline_breakout(df: pd.DataFrame, window: int = 50) -> Dict[str, Any]:
    """
    최근 N봉의 국소 고점들을 연결한 우하향 빗각(하락 추세선)을 산출하고,
    최근 1~3봉 내에 상향 돌파(Breakout)가 발생했는지 감지합니다.
    """
    if len(df) < 25:
        return {'is_breakout': False, 'status': '데이터 부족'}
        
    recent = df.tail(min(window, len(df))).copy()
    highs = recent['High'].values
    n = len(recent)
    
    # 3일 단위 국소 고점 탐색
    peak_indices = []
    for i in range(2, n - 2):
        if highs[i] >= highs[i-1] and highs[i] >= highs[i-2] and highs[i] >= highs[i+1] and highs[i] >= highs[i+2]:
            peak_indices.append(i)
            
    if len(peak_indices) < 2:
        return {'is_breakout': False, 'status': '명확한 빗각 미형성'}
        
    # 가장 높은 고점과 그 이후의 두 번째 주요 고점을 잇는 하향 추세선 계산
    p1_idx = peak_indices[0]
    p1_high = highs[p1_idx]
    
    # 우하향 기울기를 만드는 최적의 두 번째 고점 탐색
    best_slope = None
    best_intercept = None
    p2_best_idx = None
    
    for idx in peak_indices[1:]:
        slope = (highs[idx] - p1_high) / (idx - p1_idx)
        if slope < -0.01:  # 음의 기울기(우하향 빗각)
            best_slope = slope
            best_intercept = p1_high - (slope * p1_idx)
            p2_best_idx = idx
            break
            
    if best_slope is None:
        return {'is_breakout': False, 'status': '하락 빗각 미형성'}
        
    # 현재 시점의 추세선 값
    curr_idx = n - 1
    trendline_curr = (best_slope * curr_idx) + best_intercept
    prev_idx = n - 2
    trendline_prev = (best_slope * prev_idx) + best_intercept
    
    curr_close = float(recent['Close'].iloc[-1])
    prev_close = float(recent['Close'].iloc[-2])
    
    # 돌파 판정: 오늘 종가가 빗각을 상회
    is_breakout = (curr_close >= trendline_curr) and (prev_close <= trendline_prev * 1.02)
    is_approaching = (curr_close >= trendline_curr * 0.97) and not is_breakout
    
    status = "돌파 대기 중"
    if is_breakout:
        status = "하락 빗각(대각 추세선) 상향 돌파 성공!"
    elif is_approaching:
        status = "하락 빗각 상단 턱밑 도달 (돌파 임박)"
        
    return {
        'is_breakout': is_breakout,
        'is_approaching': is_approaching,
        'trendline_value': round(trendline_curr, 2),
        'slope': round(best_slope, 4),
        'status': status
    }


def detect_bullish_divergence(df: pd.DataFrame, window: int = 30) -> Dict[str, Any]:
    """
    RSI 상승 다이버전스(주가 신저가 vs RSI 저점 상승)를 감지합니다.
    기관들의 저가 매집 및 바닥 탈출 시그널.
    """
    if len(df) < window or 'RSI_14' not in df.columns:
        return {'has_divergence': False}
        
    recent = df.tail(window)
    lows = recent['Low'].values
    rsis = recent['RSI_14'].values
    n = len(recent)
    
    # 국소 저점 탐색 (2개)
    trough_indices = []
    for i in range(2, n - 2):
        if lows[i] <= lows[i-1] and lows[i] <= lows[i-2] and lows[i] <= lows[i+1] and lows[i] <= lows[i+2]:
            trough_indices.append(i)
            
    if len(trough_indices) >= 2:
        i1 = trough_indices[-2]
        i2 = trough_indices[-1]
        
        # 주가는 저점을 낮췄는데, RSI는 저점을 높인 경우 (다이버전스)
        if lows[i2] < lows[i1] and rsis[i2] > (rsis[i1] + 1.5):
            return {
                'has_divergence': True,
                'status': "RSI 상승 다이버전스 발생 (강력 바닥 턴어라운드)"
            }
            
    return {'has_divergence': False, 'status': '다이버전스 없음'}


def detect_candlestick_reversal(df: pd.DataFrame) -> Dict[str, Any]:
    """
    지지선 부근 망치형(Pinbar) 또는 상승장악형 캔들 탐지.
    """
    if len(df) < 2:
        return {'has_reversal_candle': False}
        
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    o, h, l, c = float(last['Open']), float(last['High']), float(last['Low']), float(last['Close'])
    body = abs(c - o)
    lower_wick = min(o, c) - l
    
    # 망치형 캔들: 아래꼬리가 몸통의 1.8배 이상
    is_hammer = (lower_wick >= (body * 1.8)) and (lower_wick > 0)
    
    # 상승 장악형: 전일 음봉을 오늘 양봉이 완전히 감쌈
    prev_o, prev_c = float(prev['Open']), float(prev['Close'])
    is_engulfing = (prev_c < prev_o) and (c > o) and (c >= prev_o) and (o <= prev_c)
    
    if is_hammer:
        return {'has_reversal_candle': True, 'type': '아래꼬리 망치형 캔들 (저가 매수세 유입)'}
    elif is_engulfing:
        return {'has_reversal_candle': True, 'type': '상승 장악형 캔들 (매수세 역전)'}
        
    return {'has_reversal_candle': False, 'type': '일반 캔들'}

