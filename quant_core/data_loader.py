"""
quant_core/data_loader.py
데이터 수집 및 전처리 모듈:
- 야후 파이낸스를 통한 실시간 및 일봉/시간봉 시세 데이터 수집
- 2배 레버리지 ETF의 짧은 상장 기간을 보완하기 위한 원자산(본주) 기반 2배 합성 데이터 생성 기능
"""

import time
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from typing import Callable, Dict, Iterable, Tuple, Optional

OHLC_COLUMNS = ['Open', 'High', 'Low', 'Close']

# 일봉이 마지막 거래일까지 올라왔는지 가늠하는 표본 (대형주 + 벤치마크)
SESSION_PROBE = ('QQQ', 'AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL')


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


def last_session_date(ticker: str = 'QQQ') -> Optional[pd.Timestamp]:
    """야후가 알려주는 마지막 정규장 거래일(뉴욕 기준 날짜). 조회에 실패하면 None."""
    try:
        raw = yf.Ticker(ticker).history_metadata.get('regularMarketTime')
        if raw is None:
            return None
        ts = pd.Timestamp(raw, unit='s', tz='UTC') if isinstance(raw, (int, float)) else pd.Timestamp(raw)
        if ts.tzinfo is None:
            ts = ts.tz_localize('UTC')
        return ts.tz_convert('America/New_York').tz_localize(None).normalize()
    except Exception:
        return None


def latest_bar_dates(tickers: Iterable[str]) -> Dict[str, pd.Timestamp]:
    """종목별로 정제된 일봉의 마지막 날짜. 받지 못한 종목은 빠진다."""
    out: Dict[str, pd.Timestamp] = {}
    for t in tickers:
        try:
            d = clean_ohlcv(yf.Ticker(t).history(period='5d', interval='1d'))
        except Exception:
            continue
        if d is not None and not d.empty:
            out[t] = d.index[-1].normalize()
    return out


def wait_for_latest_daily_bars(
    probe: Iterable[str] = SESSION_PROBE,
    max_wait_sec: float = 5400,
    poll_sec: float = 300,
    session_fn: Optional[Callable[[], Optional[pd.Timestamp]]] = None,
    dates_fn: Optional[Callable[[Iterable[str]], Dict[str, pd.Timestamp]]] = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    log: Callable[[str], None] = print,
) -> bool:
    """
    야후 일봉에 마지막 거래일 봉이 올라올 때까지 기다립니다. 표본 종목의 절반 넘게 올라오면 True.

    [배경] 미국 장 마감 후 일봉이 확정되기까지 시간이 걸려, UTC 자정(뉴욕 20시) 이후에 돈 스캔은
    마지막 거래일 봉이 빠진 채(clean_ohlcv 가 NaN 행을 지움) 하루 전 종가로 계산되곤 했다.
    max_wait_sec 안에 올라오지 않으면 False 를 돌려주고, 호출한 쪽은 경고만 남기고 계속 진행한다.
    마지막 거래일 자체를 알 수 없을 때는 확인할 방법이 없으므로 기다리지 않고 True.
    """
    session_fn = session_fn or last_session_date
    dates_fn = dates_fn or latest_bar_dates
    probe = list(probe)
    deadline = clock() + max_wait_sec
    while True:
        session = session_fn()
        if session is None:
            log("  ⚠️ 마지막 거래일을 확인하지 못해 최신 봉 확인을 건너뜁니다.")
            return True
        dates = dates_fn(probe)
        fresh = sum(1 for d in dates.values() if d >= session)
        if fresh * 2 > len(probe):
            return True
        if clock() + poll_sec > deadline:
            log(f"  ⚠️ {session.date()} 일봉이 아직 확정되지 않았습니다 "
                f"({fresh}/{len(probe)}종목 반영). 대기를 마치고 그대로 진행합니다.")
            return False
        log(f"  ⏳ {session.date()} 일봉 대기 중 ({fresh}/{len(probe)}종목 반영) - {int(poll_sec // 60)}분 뒤 다시 확인")
        sleep(poll_sec)


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
