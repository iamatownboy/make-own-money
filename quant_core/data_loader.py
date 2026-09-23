"""
quant_core/data_loader.py
데이터 수집 및 전처리 모듈:
- 야후 파이낸스를 통한 실시간 및 일봉/시간봉 시세 데이터 수집
- 2배 레버리지 ETF의 짧은 상장 기간을 보완하기 위한 원자산(본주) 기반 2배 합성 데이터 생성 기능
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional

OHLC_COLUMNS = ['Open', 'High', 'Low', 'Close']


def clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """
    yfinance 시세를 분석 가능한 형태로 정제합니다. 모든 history() 결과는 이 함수를 거쳐야 합니다.

    [배경] yfinance는 당일 봉이 확정되기 전, 해당 날짜 행을 OHLC 전부 NaN(거래량만 존재)으로
    붙여 반환하는 경우가 있다. 이를 거르지 않으면
      - 현재가 = df['Close'].iloc[-1] 이 NaN이 되어 손절가·목표가·손익비가 전부 NaN
      - is_qualified 판정에서 NaN >= 1.2 가 False가 되어 해당 시점 스캔이 조용히 0건
      - 추적 엔진의 기간 만료 청산가가 NaN
      - 백테스트 평균 수익률(np.mean)이 NaN으로 오염
    되는 문제가 발생한다.

    처리: 타임존 제거, 인덱스 정렬, 중복 날짜 제거(마지막 값 유지), OHLC 중 하나라도 NaN인 행 제거.
    """
    if df is None or df.empty:
        return df
    out = df.copy()
    if getattr(out.index, 'tz', None) is not None:
        out.index = out.index.tz_localize(None)
    out = out[~out.index.duplicated(keep='last')].sort_index()
    present = [c for c in OHLC_COLUMNS if c in out.columns]
    if present:
        out = out.dropna(subset=present)
    return out

# 포트폴리오 종목 기본 메타데이터
PORTFOLIO_CONFIG = {
    'RKLX': {
        'name': '로켓랩 2X (Defiance 2X Long Rocket Lab)',
        'underlying': 'RKLB',
        'category': '우주/항공',
        'leverage': 2.0
    },
    'RAM': {
        'name': 'DRAM 반도체 2X (T-Rex 2X Long DRAM)',
        'underlying': 'DRAM',
        'category': '반도체',
        'leverage': 2.0
    },
    'UGL': {
        'name': '금 2X (ProShares Ultra Gold)',
        'underlying': 'GLD',
        'category': '원자재/안전자산',
        'leverage': 2.0
    },
    'SNXX': {
        'name': '샌디스크 2X (Tradr 2X Long SNDK)',
        'underlying': 'SNDK',
        'category': '반도체/플래시',
        'leverage': 2.0
    },
    'SPCH': {
        'name': '스페이스X 2X (Leverage Shares 2X Long SpaceX)',
        'underlying': 'SPCX',
        'category': '우주/탐사',
        'leverage': 2.0
    },
    'SOXL': {
        'name': '반도체 3X (Direxion Daily Semi Bull 3X)',
        'underlying': 'SOXX',
        'category': '반도체',
        'leverage': 3.0
    },
    'RKLB': {
        'name': '로켓랩 본주 (Rocket Lab USA)',
        'underlying': 'RKLB',
        'category': '우주/항공',
        'leverage': 1.0
    }
}



def fetch_stock_data(
    ticker: str,
    period: str = '1y',
    interval: str = '1d',
    use_synthetic: bool = False
) -> pd.DataFrame:
    """
    주가 데이터를 다운로드하고 검증합니다.
    
    Args:
        ticker: 종목 티커 (예: 'RKLX', 'UGL')
        period: 조회 기간 ('1mo', '3mo', '6mo', '1y', '2y', '5y', 'max')
        interval: 봉 주기 ('1d', '1h' 등)
        use_synthetic: True인 경우 원자산 데이터를 이용해 과거 2배 합성 시계열을 병합
    """
    try:
        t = yf.Ticker(ticker)
        df = clean_ohlcv(t.history(period=period, interval=interval))
        
        if df is None or df.empty:
            raise ValueError(f"'{ticker}' 데이터를 가져올 수 없습니다.")
        
        # 타임존 정보 정제
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
            
        # 열 정리
        required_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
        for col in required_cols:
            if col not in df.columns:
                raise ValueError(f"필수 컬럼 {col}이 데이터에 없습니다.")
        
        df = df[required_cols].copy()
        
        # 합성 데이터 옵션 (신규 ETF의 과거 백테스팅용)
        if use_synthetic and ticker in PORTFOLIO_CONFIG:
            cfg = PORTFOLIO_CONFIG[ticker]
            underlying = cfg['underlying']
            leverage = cfg['leverage']
            
            # 본주 데이터 가져오기
            u_df = clean_ohlcv(yf.Ticker(underlying).history(period=period, interval=interval))
            if u_df is not None and not u_df.empty:
                if u_df.index.tz is not None:
                    u_df.index = u_df.index.tz_localize(None)
                
                # 본주 데이터가 실제 ETF보다 훨씬 이전부터 존재할 때 합성
                first_etf_date = df.index[0]
                u_past = u_df[u_df.index < first_etf_date].copy()
                
                if not u_past.empty:
                    # 일일 수익률의 N배로 역산 시뮬레이션
                    u_past['Return'] = u_past['Close'].pct_change() * leverage
                    u_past = u_past.dropna()
                    
                    # ETF 첫 시작가 기준으로 과거 가격 역산
                    base_price = df['Close'].iloc[0]
                    reversed_returns = u_past['Return'].iloc[::-1].values
                    past_prices = [base_price]
                    for r in reversed_returns:
                        prev_p = past_prices[-1] / (1 + r)
                        past_prices.append(prev_p)
                    
                    past_prices = past_prices[1:][::-1]
                    
                    synthetic_df = pd.DataFrame(index=u_past.index)
                    synthetic_df['Close'] = past_prices
                    synthetic_df['Open'] = synthetic_df['Close'] * (u_past['Open'] / u_past['Close'])
                    synthetic_df['High'] = synthetic_df['Close'] * (u_past['High'] / u_past['Close'])
                    synthetic_df['Low'] = synthetic_df['Close'] * (u_past['Low'] / u_past['Close'])
                    synthetic_df['Volume'] = u_past['Volume']
                    
                    # 합성 데이터와 실제 데이터 병합
                    df = pd.concat([synthetic_df, df]).sort_index()
        
        return df
    except Exception as e:
        print(f"[{ticker}] 데이터 수집 오류: {e}")
        return pd.DataFrame()


def get_latest_quote_info(ticker: str) -> Dict:
    """
    종목의 최신 호가 및 기본 정보를 딕셔너리로 반환합니다.
    """
    cfg = PORTFOLIO_CONFIG.get(ticker, {
        'name': ticker,
        'underlying': ticker,
        'category': '일반',
        'leverage': 1.0
    })
    
    t = yf.Ticker(ticker)
    data = clean_ohlcv(t.history(period='5d', interval='1d'))
    
    if data is None or data.empty:
        return {'ticker': ticker, 'name': cfg['name'], 'error': '시세 데이터 없음'}
    
    last_row = data.iloc[-1]
    prev_row = data.iloc[-2] if len(data) > 1 else last_row
    
    current_price = float(last_row['Close'])
    prev_price = float(prev_row['Close'])
    change = current_price - prev_price
    change_pct = (change / prev_price) * 100 if prev_price > 0 else 0.0
    
    return {
        'ticker': ticker,
        'name': cfg['name'],
        'category': cfg['category'],
        'leverage': cfg['leverage'],
        'underlying': cfg['underlying'],
        'current_price': current_price,
        'change': change,
        'change_pct': change_pct,
        'volume': int(last_row['Volume']),
        'high': float(last_row['High']),
        'low': float(last_row['Low']),
        'date': data.index[-1].strftime('%Y-%m-%d')
    }
