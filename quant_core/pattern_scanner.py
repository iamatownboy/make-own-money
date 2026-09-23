"""
quant_core/pattern_scanner.py
나스닥 상장 보통주 전체에서 '피보나치 스윙 되돌림 매수 구간'에 들어온 종목을 찾는다.

흐름
----
1. 나스닥 공식 상장 목록(nasdaqtrader.com)에서 보통주만 추림 (ETF·테스트·워런트·우선주·유닛 제외)
2. 최근 3개월 시세로 유동성 필터: 주가 $5 이상, 최근 60일 하루 거래대금 중앙값 $10M 이상
3. 남은 종목의 4년 일봉으로 일봉(스윙 +30%)·주봉(스윙 +50%) 매수 구간 탐지
4. 오늘 결과 -> pattern_setups.json
   새 추천 -> pattern_history.json 에 추가 (추천 당시 값은 이후 바꾸지 않는다)
5. 과거 추천을 추천일 이후 시세로 채점해 pattern_history.json 의 'eval' 에 기록
"""

import io
import os
import json
import time
import pickle
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
BENCHMARK = 'QQQ'

MIN_PRICE = 5.0
MIN_DOLLAR_VOLUME = 10_000_000
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


def liquid_tickers(short: Dict[str, pd.DataFrame]) -> List[str]:
    keep = []
    for t, d in short.items():
        if d is None or len(d) < 40:
            continue
        tail = d.tail(60)
        dollar = (tail['Close'] * tail['Volume']).median()
        if float(d['Close'].iloc[-1]) >= MIN_PRICE and dollar >= MIN_DOLLAR_VOLUME:
            keep.append(t)
    return keep


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


def append_history(setups: List[Dict[str, Any]], rec_date: str) -> int:
    """처음 나타난 추천만 기록한다. 추천 당시 값은 이후 바꾸지 않는다."""
    hist = _load_json(HISTORY_FILE, [])
    seen = {h['id'] for h in hist}
    added = 0
    for s in setups:
        if s['id'] in seen:
            continue
        rec = {k: s[k] for k in ('id', 'ticker', 'name', 'tf', 'price', 'L', 'H', 'L_date', 'H_date',
                                 'entry_date', 'buy_high', 'buy_low', 'stop', 't1', 'signals')}
        rec['rec_date'] = rec_date
        hist.append(rec)
        added += 1
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


def history_summary(hist: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """추천 성적 요약. 적중률 = 결과가 난 추천 중 1차 목표에 손절보다 먼저 닿은 비율."""
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
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 전체 실행
# ─────────────────────────────────────────────────────────────────────────────
def run_pattern_scan(cache_dir: Optional[str] = None, verbose: bool = True) -> Dict[str, Any]:
    """
    매일 스캔 진입점. cache_dir 를 주면 다운로드를 캐시해 이어받는다(개발·로컬 테스트용).
    """
    t0 = time.time()
    uni = load_nasdaq_universe()
    names = dict(zip(uni.ticker, uni.name))
    if verbose:
        print(f"  [1/4] 나스닥 보통주 {len(uni):,}개")

    c_short = os.path.join(cache_dir, 'nasdaq_3mo.pkl') if cache_dir else None
    c_full = os.path.join(cache_dir, 'nasdaq_4y.pkl') if cache_dir else None
    short = download_daily(list(uni.ticker), '3mo', cache_path=c_short, verbose=verbose)
    liquid = liquid_tickers(short)
    if verbose:
        print(f"  [2/4] 유동성 필터(주가 ${MIN_PRICE:.0f}+, 거래대금 ${MIN_DOLLAR_VOLUME / 1e6:.0f}M+) 통과 {len(liquid):,}개")

    full = download_daily(liquid + [BENCHMARK], '4y', cache_path=c_full, verbose=verbose)
    bench_df = full.pop(BENCHMARK, None)
    bench = bench_df['Close'] if bench_df is not None and not bench_df.empty else None

    res = scan_universe(full, names)
    last_dates = [d.index[-1] for d in full.values() if d is not None and len(d)]
    rec_date = str(max(last_dates).date()) if last_dates else datetime.now().strftime('%Y-%m-%d')
    if verbose:
        print(f"  [3/4] 매수 구간 {len(res['setups'])}개, 구간 근접 {len(res['watch'])}개 (기준일 {rec_date})")

    added = append_history(res['setups'], rec_date)
    hist = evaluate_history(full, bench)
    summary = history_summary(hist)
    if verbose:
        print(f"  [4/4] 새 추천 기록 {added}개, 누적 {summary['total']}개 채점 완료")

    payload = {
        'date': rec_date,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'universe': int(len(uni)), 'liquid': int(len(liquid)),
        'setups': res['setups'], 'watch': res['watch'][:30],
        'summary': summary,
        'elapsed_sec': round(time.time() - t0, 1),
    }
    _save_json(SETUPS_FILE, payload)
    return payload
