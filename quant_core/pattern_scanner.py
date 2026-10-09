"""
quant_core/pattern_scanner.py
나스닥100 구성종목에서 '피보나치 스윙 되돌림 매수 구간'에 들어온 종목을 찾는다.

흐름
----
1. 대상 종목 정하기 (환경변수 PATTERN_UNIVERSE)
   - 'ndx100'(기본): 나스닥 공식 API의 나스닥100 구성종목. 지수 개편·중간 교체를 따라가도록 매번 받아오고,
     실패하면 마지막으로 저장한 목록(ndx100_current.csv)을 쓴다. 전부 대형·고유동성이라 별도 필터는 없다.
   - 'nasdaq': 나스닥 상장 보통주 전체(ETF·테스트·워런트·우선주·유닛 제외)에서
     주가 $5 이상, 최근 60일 하루 거래대금 중앙값 $30M 이상(1차 거름망) →
     시가총액 MIN_MARKET_CAP(기본 $10B) 이상인 대형주만 남김
3. 대상 종목의 4년 일봉으로 일봉(스윙 +30%)·주봉(스윙 +50%) 매수 구간 탐지
4. 오늘 결과 -> pattern_setups.json
   새 추천 -> pattern_history.json 에 추가 (추천 당시 값은 이후 바꾸지 않는다)
5. 과거 추천을 추천일 이후 시세로 채점해 pattern_history.json 의 'eval' 에 기록
"""

import io
import os
import json
import time
import pickle
import hashlib
from collections import Counter
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .data_loader import clean_ohlcv
from .indicators import calculate_all_indicators
from .pattern_setup import (TIMEFRAMES, find_live_setup, supporting_signals, to_weekly,
                            evaluate_recommendation)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNIVERSE_URL = 'https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt'
UNIVERSE_CACHE = os.path.join(ROOT, 'data', 'universes', 'nasdaq_listed.csv')
SETUPS_FILE = os.path.join(ROOT, 'pattern_setups.json')
HISTORY_FILE = os.path.join(ROOT, 'pattern_history.json')
PLACEBO_FILE = os.path.join(ROOT, 'pattern_placebo.json')
PLACEBO_PER_REC = 3        # 진짜 추천 1건당 무작위 비교군 수
PLACEBO_VOL_BAND = (0.67, 1.5)
EARNINGS_WARN_DAYS = 14          # 실적 발표가 이 기간 안이면 카드에 경고   # 비교군은 변동성이 진짜 추천의 0.67~1.5배인 종목 중에서만 뽑는다
BENCHMARK = 'QQQ'

# 추천 대상: 'ndx100'(나스닥100 구성종목만, 기본) | 'nasdaq'(나스닥 상장 보통주 전체 + 아래 규모 필터)
PATTERN_UNIVERSE = os.environ.get('PATTERN_UNIVERSE', 'ndx100').strip().lower()
NDX_URL = 'https://api.nasdaq.com/api/quote/list-type/nasdaq100'
NDX_CACHE = os.path.join(ROOT, 'data', 'universes', 'ndx100_current.csv')
NDX_MIN_SYMBOLS = 90                  # 이보다 적게 오면 응답이 깨진 것으로 보고 저장해 둔 목록을 쓴다

# 지수·섹터 ETF 현황판 (전부 레버리지 없는 1배). 화면에 현황만 보여주고 추천 기록·적중률·비교군에는 넣지 않는다.
# 주식 규칙(스윙 +30% / 주봉 +50%)이 1배 지수 ETF에서는 거의 뜨지 않고 기준을 낮추면 무작위 진입보다 성적이
# 낮아서(docs/PATTERN_SCANNER.md, scripts/etf_swing_check.py) '추천'이 아니라 '현황'으로 둔다.
ETF_WATCHLIST = [('QQQ', '나스닥100'), ('SMH', '반도체'), ('SPY', 'S&P 500'), ('GLD', '금')]

MIN_PRICE = 5.0
MIN_DOLLAR_VOLUME = 30_000_000        # 시가총액을 조회할 후보를 줄이는 1차 거름망 ('nasdaq' 모드)
# 'nasdaq' 모드에서 대형주만 추천. 환경변수 MIN_MARKET_CAP 으로 조절, 0 이하면 시가총액 필터를 끈다.
MIN_MARKET_CAP = float(os.environ.get('MIN_MARKET_CAP', 10_000_000_000))
MARKET_CAP_CACHE = os.path.join(ROOT, 'data', 'universes', 'market_cap_cache.json')
MARKET_CAP_TTL_DAYS = 7               # 시가총액은 며칠 단위로만 바뀌어도 충분 -> 매일 조회하지 않음
FALLBACK_DOLLAR_VOLUME = 100_000_000  # 시가총액 조회가 대부분 실패했을 때 대신 쓰는 거래대금 기준
EXCLUDE_NAME = (r'\bWarrants?\b|\bRights?\b|\bUnits?\b|Preferred|\bNotes\b|Debentures?|'
                r'Subordinated|Beneficial Interest|Contingent Value')


# ─────────────────────────────────────────────────────────────────────────────
# 유니버스
# ─────────────────────────────────────────────────────────────────────────────
def _clean_name(name: str) -> str:
    return str(name).split(' - ')[0].strip()


def parse_nasdaq_listed(raw: str) -> pd.DataFrame:
    """nasdaqlisted.txt 원문에서 보통주만 추린다 (ETF·테스트 종목·비정상 상태·워런트·우선주·유닛 등 제외)."""
    d = pd.read_csv(io.StringIO(raw), sep='|')
    d = d[~d['Symbol'].astype(str).str.startswith('File Creation')]
    d = d[(d['Test Issue'] == 'N') & (d['ETF'] == 'N') & (d['Financial Status'] == 'N')]
    d = d[~d['Security Name'].str.contains(EXCLUDE_NAME, case=False, na=False, regex=True)]
    d = d[d['Symbol'].astype(str).str.fullmatch(r'[A-Z]{1,5}')]
    return pd.DataFrame({'ticker': d['Symbol'].astype(str),
                         'name': d['Security Name'].map(_clean_name)}).reset_index(drop=True)


def load_nasdaq_universe() -> pd.DataFrame:
    """나스닥 상장 보통주 목록. 다운로드 실패 시 마지막으로 저장한 목록을 쓴다."""
    try:
        raw = urllib.request.urlopen(UNIVERSE_URL, timeout=30).read().decode('utf-8', 'ignore')
        out = parse_nasdaq_listed(raw)
        os.makedirs(os.path.dirname(UNIVERSE_CACHE), exist_ok=True)
        out.to_csv(UNIVERSE_CACHE, index=False)
        return out
    except Exception as exc:
        if os.path.exists(UNIVERSE_CACHE):
            print(f"  [유니버스] 다운로드 실패({exc}) -> 저장된 목록 사용")
            return pd.read_csv(UNIVERSE_CACHE)
        raise


def parse_ndx100(raw: str) -> pd.DataFrame:
    """나스닥 공식 나스닥100 구성종목 응답(JSON)에서 종목 목록을 뽑는다."""
    rows = json.loads(raw)['data']['data']['rows']
    d = pd.DataFrame({'ticker': [str(r['symbol']).strip() for r in rows],
                      'name': [_clean_name(r.get('companyName', '')) for r in rows]})
    d = d[d['ticker'].str.fullmatch(r'[A-Z]{1,5}')].drop_duplicates('ticker')
    return d.reset_index(drop=True)


def load_ndx100() -> pd.DataFrame:
    """나스닥100 구성종목. 조회 실패·응답 이상 시 마지막으로 저장한 목록을 쓴다."""
    try:
        req = urllib.request.Request(NDX_URL, headers={'User-Agent': 'Mozilla/5.0',
                                                       'Accept': 'application/json'})
        out = parse_ndx100(urllib.request.urlopen(req, timeout=30).read().decode('utf-8', 'ignore'))
        if len(out) < NDX_MIN_SYMBOLS:
            raise ValueError(f"구성종목이 {len(out)}개뿐")
        os.makedirs(os.path.dirname(NDX_CACHE), exist_ok=True)
        out.to_csv(NDX_CACHE, index=False)
        return out
    except Exception as exc:
        if os.path.exists(NDX_CACHE):
            print(f"  [나스닥100] 목록 조회 실패({exc}) -> 저장된 목록 사용")
            return pd.read_csv(NDX_CACHE)
        raise


# ─────────────────────────────────────────────────────────────────────────────
# 시세
# ─────────────────────────────────────────────────────────────────────────────
def download_daily(tickers: List[str], period: str, batch: int = 150,
                   cache_path: Optional[str] = None, verbose: bool = True,
                   deadline: Optional[float] = None) -> Dict[str, pd.DataFrame]:
    """
    yfinance 일봉을 묶음으로 받는다. cache_path 를 주면 중간 결과를 저장해 이어받을 수 있다(개발용).
    deadline(time.time 기준)을 넘기면 받은 데까지만 돌려준다.
    """
    import yfinance as yf

    out: Dict[str, pd.DataFrame] = {}
    if cache_path and os.path.exists(cache_path):
        out = pickle.load(open(cache_path, 'rb'))
    todo = [t for t in tickers if t not in out]
    for k in range(0, len(todo), batch):
        if deadline and time.time() > deadline:
            break
        chunk = todo[k:k + batch]
        df = None
        for attempt in range(3):
            try:
                df = yf.download(chunk, period=period, auto_adjust=True, progress=False,
                                 threads=True, group_by='ticker')
                break
            except Exception:
                time.sleep(5 * (attempt + 1))
        for t in chunk:
            try:
                d = df[t] if len(chunk) > 1 else df
                d = clean_ohlcv(d[['Open', 'High', 'Low', 'Close', 'Volume']])
            except Exception:
                d = pd.DataFrame()
            out[t] = d
        if cache_path:
            pickle.dump(out, open(cache_path, 'wb'))
        if verbose:
            print(f"    {min(k + batch, len(todo))}/{len(todo)}", flush=True)
        time.sleep(1)
    return out


def _median_dollar_volume(d: pd.DataFrame) -> float:
    tail = d.tail(60)
    return float((tail['Close'] * tail['Volume']).median())


def liquid_tickers(short: Dict[str, pd.DataFrame], min_dollar: float = MIN_DOLLAR_VOLUME) -> List[str]:
    keep = []
    for t, d in short.items():
        if d is None or len(d) < 40:
            continue
        if float(d['Close'].iloc[-1]) >= MIN_PRICE and _median_dollar_volume(d) >= min_dollar:
            keep.append(t)
    return keep


def fetch_market_caps(tickers: List[str], workers: int = 8) -> Dict[str, Optional[float]]:
    """yfinance 에서 시가총액(USD)을 가져온다. 실패하면 None."""
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor

    def one(t):
        try:
            v = getattr(yf.Ticker(t).fast_info, 'market_cap', None)
            return t, (float(v) if v else None)
        except Exception:
            return t, None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return dict(ex.map(one, sorted(set(tickers))))


def load_market_caps(tickers: List[str], fetch=fetch_market_caps,
                     today: Optional[pd.Timestamp] = None) -> Dict[str, float]:
    """
    시가총액을 캐시(MARKET_CAP_CACHE)와 함께 돌려준다. 캐시가 MARKET_CAP_TTL_DAYS 일 안이면 다시 조회하지 않고,
    조회에 실패한 종목은 오래된 값이라도 쓴다. 값을 끝내 못 구한 종목은 결과에 없다.
    """
    today = pd.Timestamp(today if today is not None else datetime.now().date())
    cache = _load_json(MARKET_CAP_CACHE, {})
    caps: Dict[str, float] = {}
    todo: List[str] = []
    for t in tickers:
        c = cache.get(t)
        if c and (today - pd.Timestamp(c['date'])).days <= MARKET_CAP_TTL_DAYS:
            caps[t] = float(c['cap'])
        else:
            todo.append(t)
    got = fetch(todo) if todo else {}
    for t in todo:
        v = got.get(t)
        if v:
            caps[t] = float(v)
            cache[t] = {'cap': float(v), 'date': str(today.date())}
        elif t in cache:
            caps[t] = float(cache[t]['cap'])      # 조회 실패 -> 마지막으로 안 값
    if todo:
        keep = set(tickers)
        _save_json(MARKET_CAP_CACHE, {t: c for t, c in cache.items() if t in keep})
    return caps


def large_cap_tickers(liquid: List[str], short: Dict[str, pd.DataFrame],
                      fetch=fetch_market_caps) -> Dict[str, Any]:
    """
    유동성 필터를 통과한 종목 중 시가총액 MIN_MARKET_CAP 이상만 남긴다.
    시가총액 조회가 절반 넘게 실패하면(네트워크 문제 등) 거래대금 FALLBACK_DOLLAR_VOLUME 이상으로 대신 거른다.
    돌려주는 값: {'tickers': [...], 'mode': 'market_cap' | 'off' | 'fallback'}
    """
    if MIN_MARKET_CAP <= 0:
        return {'tickers': list(liquid), 'mode': 'off'}
    caps = load_market_caps(liquid, fetch=fetch)
    if liquid and len(caps) < len(liquid) * 0.5:
        keep = [t for t in liquid if _median_dollar_volume(short[t]) >= FALLBACK_DOLLAR_VOLUME]
        return {'tickers': keep, 'mode': 'fallback'}
    return {'tickers': [t for t in liquid if caps.get(t, 0) >= MIN_MARKET_CAP], 'mode': 'market_cap'}


# ─────────────────────────────────────────────────────────────────────────────
# 스캔
# ─────────────────────────────────────────────────────────────────────────────
def scan_universe(full: Dict[str, pd.DataFrame], names: Dict[str, str]) -> Dict[str, List[Dict[str, Any]]]:
    setups, watch = [], []
    for t, df in full.items():
        if df is None or len(df) < 260:
            continue
        ind = None
        for tf in ('1w', '1d'):
            d = df if tf == '1d' else to_weekly(df)
            try:
                res = find_live_setup(d, tf)
            except Exception:
                continue
            if res.get('status') == 'zone':
                if ind is None:
                    try:
                        ind = calculate_all_indicators(df.tail(400))
                    except Exception:
                        ind = df
                lv = res['levels']
                setups.append({
                    'id': f"{t}|{tf}|{res['L_date']}|{res['H_date']}",
                    'ticker': t, 'name': names.get(t, t), 'tf': tf,
                    'tf_label': TIMEFRAMES[tf]['label'],
                    'price': round(res['price'], 4),
                    'L': res['L'], 'H': res['H'],
                    'L_date': res['L_date'], 'H_date': res['H_date'],
                    'entry_date': res['entry_date'],
                    'retrace': round(res['retrace'], 3),
                    'swing_pct': round(res['swing_pct'] * 100, 1),
                    'buy_high': round(lv['entry1'], 4), 'buy_low': round(lv['entry2'], 4),
                    'stop': round(lv['stop'], 4), 't1': round(lv['t1'], 4),
                    't2': round(lv['t2'], 4), 't3': round(lv['t3'], 4), 't4': round(lv['t4'], 4),
                    'stop_pct': round((lv['stop'] / res['price'] - 1) * 100, 1),
                    'position': ('구간 위' if res['price'] > lv['entry1'] else
                                 ('구간 안' if res['price'] >= lv['entry2'] else '구간 아래')),
                    't1_pct': round((lv['t1'] / res['price'] - 1) * 100, 1),
                    'signals': supporting_signals(ind),
                    'base_rate': TIMEFRAMES[tf]['base_rate'],
                })
            elif res.get('status') == 'watch':
                watch.append({
                    'ticker': t, 'name': names.get(t, t), 'tf': tf,
                    'tf_label': TIMEFRAMES[tf]['label'],
                    'price': round(res['price'], 4),
                    'buy_high': round(res['levels']['entry1'], 4),
                    'distance_pct': round(res['distance_to_zone'] * 100, 1),
                })
    # 주봉 우선, 같은 시간봉 안에서는 최근에 구간에 들어온 순
    setups.sort(key=lambda s: (s['tf'] != '1w', -pd.Timestamp(s['entry_date']).value))
    watch.sort(key=lambda w: (w['tf'] != '1w', w['distance_pct']))
    return {'setups': setups, 'watch': watch}


def etf_board(etf_full: Dict[str, pd.DataFrame]) -> List[Dict[str, Any]]:
    """
    지수·ETF 현황: 현재가, 전일 대비, 52주 고점 대비. 주식과 같은 규칙에 해당하면(구간 안 / 구간 근접)
    매수 구간·손절·1차 목표도 붙인다. 시세가 모자란 ETF는 건너뛴다.
    """
    out = []
    for t, label in ETF_WATCHLIST:
        d = etf_full.get(t)
        if d is None or len(d) < 60:
            continue
        price = float(d['Close'].iloc[-1])
        row = {'ticker': t, 'name': label, 'price': round(price, 2),
               'chg_pct': round((price / float(d['Close'].iloc[-2]) - 1) * 100, 2),
               'from_high_pct': round((price / float(d['High'].tail(252).max()) - 1) * 100, 1),
               'status': None}
        found = {}
        for tf in ('1w', '1d'):
            try:
                res = find_live_setup(d if tf == '1d' else to_weekly(d), tf)
            except Exception:
                continue
            if res.get('status') in ('zone', 'watch'):
                found.setdefault(res['status'], (tf, res))
        pick = found.get('zone') or found.get('watch')
        if pick:
            tf, res = pick
            lv = res['levels']
            row.update(status=res['status'], tf_label=TIMEFRAMES[tf]['label'], retrace=round(res['retrace'], 3),
                       buy_high=round(lv['entry1'], 2), buy_low=round(lv['entry2'], 2),
                       stop=round(lv['stop'], 2), t1=round(lv['t1'], 2))
        out.append(row)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 실적 발표일
# ─────────────────────────────────────────────────────────────────────────────
def fetch_earnings_dates(tickers: List[str], workers: int = 8) -> Dict[str, Optional[str]]:
    """yfinance 캘린더에서 다음 실적 발표 예정일을 가져온다. 실패하면 None."""
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor

    today = pd.Timestamp(datetime.now().date())

    def one(t):
        try:
            cal = yf.Ticker(t).calendar
            dates = cal.get('Earnings Date') if isinstance(cal, dict) else None
            if not dates:
                return t, None
            ds = sorted(pd.Timestamp(d) for d in dates)
            upcoming = [d for d in ds if d >= today - pd.Timedelta(days=3)]
            # 예정일이 없으면 지난 발표일이 돌아오는 경우가 있다 -> 비워 둔다 (과거 날짜를 '다음'으로 표시하지 않음)
            return t, (str(upcoming[0].date()) if upcoming else None)
        except Exception:
            return t, None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return dict(ex.map(one, sorted(set(tickers))))


def attach_earnings(setups: List[Dict[str, Any]], rec_date: str, fetch=fetch_earnings_dates) -> None:
    """각 추천에 다음 실적 발표일과 추천일로부터 남은 날(달력일)을 붙인다."""
    if not setups:
        return
    dates = fetch([s['ticker'] for s in setups])
    base = pd.Timestamp(rec_date)
    for s in setups:
        d = dates.get(s['ticker'])
        s['earnings_date'] = d
        s['earnings_days'] = int((pd.Timestamp(d) - base).days) if d else None
        s['earnings_soon'] = bool(d is not None and 0 <= s['earnings_days'] <= EARNINGS_WARN_DAYS)


# ─────────────────────────────────────────────────────────────────────────────
# 기록·채점
# ─────────────────────────────────────────────────────────────────────────────
def _load_json(path: str, default):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, obj) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=float)


def append_history(setups: List[Dict[str, Any]], rec_date: str) -> List[str]:
    """처음 나타난 추천만 기록한다. 추천 당시 값은 이후 바꾸지 않는다. 새로 기록한 ID 목록을 돌려준다."""
    hist = _load_json(HISTORY_FILE, [])
    seen = {h['id'] for h in hist}
    added: List[str] = []
    for s in setups:
        if s['id'] in seen:
            continue
        rec = {k: s[k] for k in ('id', 'ticker', 'name', 'tf', 'price', 'L', 'H', 'L_date', 'H_date',
                                 'entry_date', 'buy_high', 'buy_low', 'stop', 't1', 'signals')}
        rec['earnings_date'] = s.get('earnings_date')   # 나중에 '실적 직전 추천'의 성적을 따로 보기 위해
        rec['rec_date'] = rec_date
        hist.append(rec)
        seen.add(s['id'])
        added.append(s['id'])
    _save_json(HISTORY_FILE, hist)
    return added


def evaluate_history(full: Dict[str, pd.DataFrame], bench: Optional[pd.Series],
                     fetch_missing: bool = True) -> List[Dict[str, Any]]:
    hist = _load_json(HISTORY_FILE, [])
    if not hist:
        return hist
    need = [h['ticker'] for h in hist
            if not h.get('eval', {}).get('finished') and h['ticker'] not in full]
    extra = download_daily(sorted(set(need)), '4y', verbose=False) if (need and fetch_missing) else {}
    for h in hist:
        if h.get('eval', {}).get('finished'):
            continue
        df = full.get(h['ticker'])
        if df is None or df.empty:
            df = extra.get(h['ticker'])
        if df is None or df.empty:
            continue
        try:
            h['eval'] = evaluate_recommendation(h, df, bench)
        except Exception as exc:
            h['eval'] = {'result': f'채점 오류: {exc}'}
    _save_json(HISTORY_FILE, hist)
    return hist


def _stable_seed(text: str) -> int:
    return int(hashlib.md5(text.encode('utf-8')).hexdigest()[:8], 16)


def _recent_vol(df: pd.DataFrame, asof: pd.Timestamp, days: int = 60) -> Optional[float]:
    """추천일까지 최근 N거래일 일간 수익률 표준편차 (그 시점 정보만 사용)."""
    c = df['Close'].loc[:asof].tail(days + 1)
    if len(c) < days // 2:
        return None
    v = float(c.pct_change().dropna().std())
    return v if v > 0 else None


def ensure_placebos(hist: List[Dict[str, Any]], full: Dict[str, pd.DataFrame],
                    pool: List[str]) -> List[Dict[str, Any]]:
    """
    무작위 비교군(위약): 진짜 추천 1건마다 같은 날 무작위 종목 PLACEBO_PER_REC 개에
    '같은 모양의 가격표'를 붙인다. 가격표는 진짜 추천의 스윙 저점·고점을 그 종목의 추천일 종가에 맞춰
    비율 그대로 옮긴 것(매수 구간·손절·목표까지의 거리 % 가 동일).

    - 추천일 종가를 기준으로 만들기 때문에 나중에 만들어도 추천 당시 정보만 쓴다.
    - 무작위 선택은 추천 ID 로 시드를 고정 -> 다시 돌려도 같은 종목. 결과를 보고 고를 수 없다.
    - 차이 = '패턴 구간에 들어온 종목을 골랐다'는 것의 효과. 같은 날·같은 시장 조건끼리 비교된다.
    - 변동성이 비슷한 종목(PLACEBO_VOL_BAND)에서만 뽑는다. 변동성이 낮은 종목에 같은 % 손절·목표를 붙이면
      어느 쪽에도 잘 닿지 않아 비교가 기울기 때문이다.
    """
    pl = _load_json(PLACEBO_FILE, [])
    have = Counter(p['rec_id'] for p in pl)
    pool = sorted(set(pool))
    for h in hist:
        need = PLACEBO_PER_REC - have.get(h['id'], 0)
        if need <= 0:
            continue
        rng = np.random.default_rng(_stable_seed(h['id']))
        rec_date = pd.Timestamp(h['rec_date'])
        own = full.get(h['ticker'])
        own_vol = _recent_vol(own, rec_date) if own is not None and not own.empty else None
        cands = [t for t in pool if t != h['ticker']]
        order = rng.permutation(len(cands))
        k = have.get(h['id'], 0)
        for j in order:
            if need <= 0:
                break
            t = cands[j]
            df = full.get(t)
            if df is None or df.empty:
                continue
            base = df.loc[:rec_date]
            if base.empty or (rec_date - base.index[-1]).days > 5:
                continue
            if own_vol is not None:
                v = _recent_vol(df, rec_date)
                if v is None or not (PLACEBO_VOL_BAND[0] * own_vol <= v <= PLACEBO_VOL_BAND[1] * own_vol):
                    continue
            p = float(base['Close'].iloc[-1])
            f = p / float(h['price'])
            pl.append({'id': f"{h['id']}#p{k}", 'rec_id': h['id'], 'ticker': t, 'tf': h['tf'],
                       'rec_date': h['rec_date'], 'price': p,
                       'L': float(h['L']) * f, 'H': float(h['H']) * f})
            k += 1
            need -= 1
    _save_json(PLACEBO_FILE, pl)
    return pl


def evaluate_placebos(full: Dict[str, pd.DataFrame], bench: Optional[pd.Series],
                      fetch_missing: bool = True) -> List[Dict[str, Any]]:
    pl = _load_json(PLACEBO_FILE, [])
    need = [p['ticker'] for p in pl if not p.get('eval', {}).get('finished') and p['ticker'] not in full]
    extra = download_daily(sorted(set(need)), '4y', verbose=False) if (need and fetch_missing) else {}
    for p in pl:
        if p.get('eval', {}).get('finished'):
            continue
        df = full.get(p['ticker'])
        if df is None or df.empty:
            df = extra.get(p['ticker'])
        if df is None or df.empty:
            continue
        try:
            p['eval'] = evaluate_recommendation(p, df, bench)
        except Exception as exc:
            p['eval'] = {'result': f'채점 오류: {exc}'}
    _save_json(PLACEBO_FILE, pl)
    return pl


def _hit_stats(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    res = [(i.get('eval') or {}).get('result', '진행 중') for i in items]
    hit, stop = res.count('적중'), res.count('손절')
    dec = hit + stop
    return {'decided': dec, 'hit': hit, 'stop': stop,
            'hit_rate': round(hit / dec * 100, 1) if dec else None}


def history_summary(hist: Optional[List[Dict[str, Any]]] = None,
                    placebo: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    추천 성적 요약. 적중률 = 결과가 난 추천 중 1차 목표에 손절보다 먼저 닿은 비율.
    hist 를 직접 넘기면 비교군도 넘긴 것만 쓴다(파일을 읽지 않음).
    """
    from_file = hist is None
    hist = hist if hist is not None else _load_json(HISTORY_FILE, [])
    rows = []
    for h in hist:
        e = h.get('eval') or {}
        rows.append({'tf': h['tf'], 'result': e.get('result', '진행 중'), 'ret': e.get('ret'),
                     'bench': e.get('bench_ret'), 'finished': e.get('finished', False)})
    df = pd.DataFrame(rows)
    out = {'total': len(df)}
    if df.empty:
        return {**out, 'hit': 0, 'stop': 0, 'open': 0, 'hit_rate': None}
    decided = df[df.result.isin(['적중', '손절'])]
    out.update({
        'hit': int((df.result == '적중').sum()),
        'stop': int((df.result == '손절').sum()),
        'open': int((~df.result.isin(['적중', '손절'])).sum()),
        'hit_rate': round(float((decided.result == '적중').mean() * 100), 1) if len(decided) else None,
        'decided': int(len(decided)),
    })
    fin = df[df.finished & df.ret.notna()]
    if len(fin):
        out['avg_ret'] = round(float(fin.ret.mean() * 100), 2)
        fb = fin[fin.bench.notna()]
        if len(fb):
            out['avg_excess'] = round(float((fb.ret - fb.bench).mean() * 100), 2)
        out['finished'] = int(len(fin))
    by_tf = {}
    for tf, g in df.groupby('tf'):
        dg = g[g.result.isin(['적중', '손절'])]
        by_tf[tf] = {'total': int(len(g)), 'decided': int(len(dg)),
                     'hit_rate': round(float((dg.result == '적중').mean() * 100), 1) if len(dg) else None}
    out['by_tf'] = by_tf

    # 무작위 비교군 (같은 날짜들의 위약)
    if placebo is None:
        placebo = _load_json(PLACEBO_FILE, []) if from_file else []
    if placebo:
        b = _hit_stats(placebo)
        out['baseline'] = {'total': len(placebo), **b}
        # 공정 비교: 진짜 추천이 결과 난 날짜들에 한정한 비교군 적중률
        decided_ids = {h.get('id') for h in hist if (h.get('eval') or {}).get('result') in ('적중', '손절')}
        matched = [p for p in placebo if p['rec_id'] in decided_ids]
        out['baseline_matched'] = _hit_stats(matched)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 전체 실행
# ─────────────────────────────────────────────────────────────────────────────
def _select_wide_nasdaq(uni: pd.DataFrame, cache_dir: Optional[str], verbose: bool) -> Dict[str, Any]:
    """'nasdaq' 모드: 상장 보통주 전체에서 유동성 → 시가총액 순으로 거른다."""
    c_short = os.path.join(cache_dir, 'nasdaq_3mo.pkl') if cache_dir else None
    short = download_daily(list(uni.ticker), '3mo', cache_path=c_short, verbose=verbose)
    liquid = liquid_tickers(short)
    big = large_cap_tickers(liquid, short)
    tickers, mode = big['tickers'], big['mode']
    if verbose:
        if mode == 'market_cap':
            print(f"  [2/4] 유동성 필터 통과 {len(liquid):,}개 -> 시가총액 ${MIN_MARKET_CAP / 1e9:.0f}B+ 대형주 {len(tickers):,}개")
        elif mode == 'fallback':
            print(f"  [2/4] ⚠️ 시가총액 조회 실패 -> 거래대금 ${FALLBACK_DOLLAR_VOLUME / 1e6:.0f}M+ 로 대체: {len(tickers):,}개")
        else:
            print(f"  [2/4] 유동성 필터(주가 ${MIN_PRICE:.0f}+, 거래대금 ${MIN_DOLLAR_VOLUME / 1e6:.0f}M+) 통과 {len(tickers):,}개")
    return {'tickers': tickers, 'mode': mode}


def run_pattern_scan(cache_dir: Optional[str] = None, verbose: bool = True) -> Dict[str, Any]:
    """
    매일 스캔 진입점. cache_dir 를 주면 다운로드를 캐시해 이어받는다(개발·로컬 테스트용).
    """
    t0 = time.time()
    uni = load_nasdaq_universe()          # 종목 이름 표시용 + 'nasdaq' 모드의 후보
    names = dict(zip(uni.ticker, uni.name))
    ndx_mode = PATTERN_UNIVERSE != 'nasdaq'
    if ndx_mode:
        ndx = load_ndx100()
        names = {**dict(zip(ndx.ticker, ndx.name)), **names}
        liquid, cap_mode, n_universe = list(ndx.ticker), 'ndx100', len(ndx)
        if verbose:
            print(f"  [1/4] 나스닥100 구성종목 {len(liquid)}개 (유동성·시총 필터 없음)")
    else:
        if verbose:
            print(f"  [1/4] 나스닥 보통주 {len(uni):,}개")
        picked = _select_wide_nasdaq(uni, cache_dir, verbose)
        liquid, cap_mode, n_universe = picked['tickers'], picked['mode'], len(uni)

    c_full = os.path.join(cache_dir, 'nasdaq_4y.pkl') if cache_dir else None
    etf_tickers = [t for t, _ in ETF_WATCHLIST]
    extra = [t for t in etf_tickers + [BENCHMARK] if t not in liquid]       # QQQ 는 벤치마크이자 ETF
    full = download_daily(liquid + list(dict.fromkeys(extra)), '4y', cache_path=c_full, verbose=verbose)
    bench_df = full.get(BENCHMARK)
    bench = bench_df['Close'] if bench_df is not None and not bench_df.empty else None
    etf_full = {t: full[t] for t in etf_tickers if t in full}
    for t in etf_tickers + [BENCHMARK]:
        full.pop(t, None)           # 주식 스캔·채점·비교군 풀에는 ETF 가 섞이지 않게 한다
    board = etf_board(etf_full)
    if verbose:
        print(f"  [ETF] 현황판 {len(board)}종 (구간 {sum(e['status'] == 'zone' for e in board)}, "
              f"근접 {sum(e['status'] == 'watch' for e in board)})")

    res = scan_universe(full, names)
    last_dates = [d.index[-1] for d in full.values() if d is not None and len(d)]
    rec_date = str(max(last_dates).date()) if last_dates else datetime.now().strftime('%Y-%m-%d')
    try:
        attach_earnings(res['setups'], rec_date)
    except Exception as exc:
        if verbose:
            print(f"  [실적일] 조회 실패: {exc}")
    if verbose:
        print(f"  [3/4] 매수 구간 {len(res['setups'])}개, 구간 근접 {len(res['watch'])}개 (기준일 {rec_date})")

    new_ids = append_history(res['setups'], rec_date)
    added = len(new_ids)
    hist = evaluate_history(full, bench)
    ensure_placebos(hist, full, list(full.keys()))
    placebo = evaluate_placebos(full, bench)
    summary = history_summary(hist, placebo)
    if verbose:
        print(f"  [4/4] 새 추천 기록 {added}개, 누적 {summary['total']}개 채점 완료")

    # 새 추천이 있을 때만 알림 (설정이 없으면 조용히 건너뜀)
    sent = []
    try:
        from .notifier import notify_new_setups
        new_set = set(new_ids)
        sent = notify_new_setups([s for s in res['setups'] if s['id'] in new_set], rec_date, len(liquid),
                                 universe='나스닥100' if ndx_mode else '나스닥')
    except Exception as exc:
        if verbose:
            print(f"  [알림] 실패: {type(exc).__name__}")
    if verbose and new_ids:
        print(f"  [알림] 새 추천 {added}건 -> {', '.join(sent) if sent else '설정된 채널 없음'}")

    payload = {
        'date': rec_date,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'universe': int(n_universe), 'universe_name': 'ndx100' if ndx_mode else 'nasdaq',
        'liquid': int(len(liquid)), 'etf': board,
        'min_market_cap': MIN_MARKET_CAP if cap_mode == 'market_cap' else None,
        'setups': res['setups'], 'watch': res['watch'][:30],
        'summary': summary,
        'elapsed_sec': round(time.time() - t0, 1),
    }
    _save_json(SETUPS_FILE, payload)
    return payload
