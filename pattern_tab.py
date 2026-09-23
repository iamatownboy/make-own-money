"""
pattern_tab.py
메인 화면: 패턴 구간 추천.

원칙: 한 종목 = 카드 한 장, 숫자 세 개(매수 구간·손절·1차 목표). 나머지는 접어 둔다.
데이터는 매일 스캔이 만든 pattern_setups.json / pattern_history.json 을 읽기만 한다.
"""

import json
import os
from html import escape

import streamlit as st

ROOT = os.path.dirname(os.path.abspath(__file__))
SETUPS_FILE = os.path.join(ROOT, 'pattern_setups.json')
HISTORY_FILE = os.path.join(ROOT, 'pattern_history.json')

CSS = """
<style>
.pt-head {font-size:13px; color:#9094a6; margin:2px 0 14px 0;}
.pt-score {background:#17181f; border:1px solid #23252f; border-radius:12px; padding:12px 16px;
           margin-bottom:16px; font-size:13.5px; color:#cfcfd4;}
.pt-score b {color:#ffffff;}
.pt-card {background:#17181f; border:1px solid #23252f; border-radius:14px; padding:14px 16px; margin-bottom:10px;}
.pt-top {display:flex; justify-content:space-between; align-items:baseline; gap:8px;}
.pt-tk {font-size:17px; font-weight:700; color:#ffffff;}
.pt-nm {font-size:12.5px; color:#8b8fa3; margin-left:6px;}
.pt-px {font-size:15px; font-weight:600; color:#ffffff; white-space:nowrap;}
.pt-badges {margin-top:6px;}
.pt-b {display:inline-block; font-size:11.5px; padding:2px 8px; border-radius:6px; margin-right:4px;
       background:#21232d; color:#9ba0b4; border:1px solid #2a2d3a;}
.pt-b.in {color:#00e676; border-color:rgba(0,230,118,.3); background:rgba(0,230,118,.08);}
.pt-b.up {color:#ffd54f; border-color:rgba(255,213,79,.3); background:rgba(255,213,79,.08);}
.pt-b.dn {color:#ff8a65; border-color:rgba(255,138,101,.3); background:rgba(255,138,101,.08);}
.pt-grid {display:grid; grid-template-columns:repeat(3, 1fr); gap:8px; margin-top:12px;}
.pt-cell {background:#1b1d26; border-radius:10px; padding:8px 10px;}
.pt-lb {font-size:11.5px; color:#8b8fa3;}
.pt-v {font-size:14.5px; font-weight:700; color:#ffffff; margin-top:2px;}
.pt-v.red {color:#ff6b6b;} .pt-v.green {color:#4ade80;}
.pt-sub {font-size:11.5px; color:#8b8fa3; font-weight:500;}
.pt-sig {margin-top:10px; font-size:12px; color:#9ba0b4;}
.pt-foot {display:flex; justify-content:space-between; align-items:center; margin-top:10px;}
.pt-foot a {font-size:12.5px; color:#60a5fa; text-decoration:none;}
.pt-card details {font-size:12.5px; color:#9ba0b4;}
.pt-card summary {cursor:pointer; color:#8b8fa3;}
.pt-card details div {margin-top:6px; line-height:1.7;}
.pt-note {font-size:11.5px; color:#6b6f82; margin-top:18px; line-height:1.6;}
@media (max-width: 640px) { .pt-grid {grid-template-columns:repeat(3, 1fr); gap:6px;} .pt-v {font-size:13px;} }
</style>
"""


@st.cache_data(ttl=600)
def _load(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _money(x):
    if x is None:
        return '-'
    return f"${x:,.2f}" if x >= 1 else f"${x:,.4f}"


def _group_by_ticker(setups):
    """같은 종목이 주봉·일봉에 모두 있으면 주봉 카드 하나로 합친다."""
    cards = {}
    for s in setups:
        t = s['ticker']
        if t not in cards:
            cards[t] = dict(s, also=[])
        else:
            cards[t]['also'].append(s['tf_label'])
    return list(cards.values())


def _card_html(s):
    pos = s.get('position', '구간 안')
    pos_cls = {'구간 안': 'in', '구간 위': 'up', '구간 아래': 'dn'}.get(pos, '')
    badges = f'<span class="pt-b">{escape(s["tf_label"])}</span>'
    badges += f'<span class="pt-b {pos_cls}">{escape(pos)}</span>'
    for a in s.get('also', []):
        badges += f'<span class="pt-b">{escape(a)}도 구간</span>'
    if s.get('earnings_soon'):
        dd = s.get('earnings_days')
        label = '실적 발표 오늘' if dd == 0 else f'실적 발표 D-{dd}'
        badges += f'<span class="pt-b dn" title="발표 직후 갭으로 손절선을 건너뛸 수 있습니다">{label}</span>'

    sig = ''
    if s.get('signals'):
        sig = '<div class="pt-sig">참고 근거 · ' + ' · '.join(escape(x) for x in s['signals']) + '</div>'

    br = s.get('base_rate') or {}
    detail = (
        f'<details><summary>자세히</summary><div>'
        f'스윙 저점 {_money(s["L"])} ({escape(s["L_date"])}) → 고점 {_money(s["H"])} ({escape(s["H_date"])}), +{s["swing_pct"]:.0f}%<br>'
        f'구간 진입일 {escape(s["entry_date"])} · 현재 되돌림 {s["retrace"]:.2f}<br>'
        f'2차 {_money(s["t2"])} · 3차 {_money(s["t3"])} · 4차 {_money(s["t4"])}<br>'
        + (f'다음 실적 발표 {escape(s["earnings_date"])}<br>' if s.get('earnings_date') else '') +
        f'과거 같은 조건에서 1차 목표 먼저 도달 {br.get("hit", "-")}% (아무 때나 샀을 때 {br.get("random", "-")}%)'
        f'</div></details>'
    )
    chart = f'https://www.tradingview.com/chart/?symbol=NASDAQ%3A{escape(s["ticker"])}'
    return f"""
<div class="pt-card">
  <div class="pt-top">
    <div><span class="pt-tk">{escape(s['ticker'])}</span><span class="pt-nm">{escape(s['name'][:34])}</span></div>
    <div class="pt-px">{_money(s['price'])}</div>
  </div>
  <div class="pt-badges">{badges}</div>
  <div class="pt-grid">
    <div class="pt-cell"><div class="pt-lb">매수 구간</div>
      <div class="pt-v">{_money(s['buy_low'])}~{_money(s['buy_high'])}</div></div>
    <div class="pt-cell"><div class="pt-lb">손절</div>
      <div class="pt-v red">{_money(s['stop'])} <span class="pt-sub">{s['stop_pct']:+.1f}%</span></div></div>
    <div class="pt-cell"><div class="pt-lb">1차 목표</div>
      <div class="pt-v green">{_money(s['t1'])} <span class="pt-sub">{s['t1_pct']:+.1f}%</span></div></div>
  </div>
  {sig}
  <div class="pt-foot">{detail}<a href="{chart}" target="_blank">차트 보기 ↗</a></div>
</div>"""


def _score_line(summary):
    if not summary or not summary.get('total'):
        return '추천 기록을 쌓기 시작했습니다. 결과가 나오면 여기에 적중률이 표시됩니다.'
    decided = summary.get('decided', 0)
    if not decided:
        return (f'지금까지 추천 <b>{summary["total"]}건</b> · 아직 결과가 난 추천이 없습니다. '
                f'같은 날 무작위 종목 비교군도 함께 기록 중입니다.')
    line = (f'지금까지 추천 <b>{summary["total"]}건</b> · 결과 난 {decided}건 중 '
            f'<b>1차 목표 먼저 {summary["hit_rate"]}%</b> (적중 {summary["hit"]} · 손절 {summary["stop"]})')
    bm = summary.get('baseline_matched') or {}
    if bm.get('hit_rate') is not None:
        diff = summary['hit_rate'] - bm['hit_rate']
        line += (f'<br>같은 날 무작위 종목 비교군 <b>{bm["hit_rate"]}%</b> ({bm["decided"]}건) → '
                 f'차이 <b>{diff:+.1f}%p</b>')
    if summary.get('avg_excess') is not None:
        line += f' · 끝난 추천의 QQQ 대비 평균 {summary["avg_excess"]:+.1f}%p'
    return line


def render_pattern_tab():
    st.markdown(CSS, unsafe_allow_html=True)
    data = _load(SETUPS_FILE)
    if not data:
        st.info('아직 스캔 결과가 없습니다. GitHub Actions의 Daily Quant Scan을 한 번 실행하면 여기에 표시됩니다.')
        return

    cards = _group_by_ticker(data.get('setups', []))
    st.markdown(
        f'<div class="pt-head">{escape(data["date"])} 종가 기준 · 나스닥 {data.get("liquid", 0):,}종목 중 '
        f'<b style="color:#fff">{len(cards)}종목</b>이 매수 구간</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="pt-score">{_score_line(data.get("summary"))}</div>', unsafe_allow_html=True)

    if not cards:
        st.markdown('<div class="pt-score">오늘은 매수 구간에 들어온 종목이 없습니다.</div>', unsafe_allow_html=True)
    for s in cards:
        st.markdown(_card_html(s), unsafe_allow_html=True)

    watch = data.get('watch') or []
    if watch:
        with st.expander(f'곧 구간에 들어올 종목 ({len(watch)})'):
            for w in watch:
                st.markdown(f"**{w['ticker']}** · {w['tf_label']} · 현재 {_money(w['price'])} · "
                            f"구간 상단 {_money(w['buy_high'])}까지 {-w['distance_pct']:.1f}%")

    hist = _load(HISTORY_FILE) or []
    if hist:
        with st.expander(f'추천 기록 ({len(hist)}건)'):
            import pandas as pd
            rows = []
            for h in sorted(hist, key=lambda x: x['rec_date'], reverse=True):
                e = h.get('eval') or {}
                ret = e.get('ret')
                b = e.get('bench_ret')
                rows.append({
                    '추천일': h['rec_date'], '종목': h['ticker'],
                    '봉': '주봉' if h['tf'] == '1w' else '일봉',
                    '결과': e.get('result', '진행 중'),
                    '수익률': None if ret is None else round(ret * 100, 1),
                    'QQQ 대비': None if (ret is None or b is None) else round((ret - b) * 100, 1),
                })
            table = pd.DataFrame(rows)
            try:
                st.dataframe(table, hide_index=True, width='stretch')           # Streamlit 최신
            except TypeError:
                st.dataframe(table, hide_index=True, use_container_width=True)  # 구버전

    st.markdown(
        '<div class="pt-note">규칙: 저점→고점 스윙 후 0.786~0.886 분할 매수 · 원래 저점(1) 이탈 시 손절 · '
        '1차 목표는 스윙의 0.5. 과거 통계는 S&P 500 과거 구성종목(2013~2026) 기준이며, 중소형주는 이 기록으로 '
        '검증 중입니다. 매수 추천이 아니라 검토 후보이니 차트를 직접 확인하세요.</div>',
        unsafe_allow_html=True)
