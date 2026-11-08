"""
app.py
토스증권(Toss Securities) WTS 스타일 퀀트 트레이딩 대시보드
- 메인: 📐 패턴 구간 (나스닥100 피보나치 스윙 되돌림 매수 구간 + 추천 기록 채점)
- 서브: 📊 점수제 추천 (기존 82종목 점수제 스크리너)
- 💼 내 보유 포트폴리오 (토스 WTS 뷰 & 실시간 계좌)
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
import os
import hmac
from html import escape
from datetime import datetime
import yfinance as yf

from quant_core.data_loader import fetch_stock_data, PORTFOLIO_CONFIG
from quant_core.indicators import calculate_all_indicators
from quant_core.prediction import evaluate_technical_health, predict_price_scenarios
from quant_core.screener import run_full_market_scan
from quant_core.db import is_supabase_enabled, get_supabase_diagnostics
from pattern_tab import render_pattern_tab
from ui_styles import render_styles

# 1. 페이지 설정
st.set_page_config(
    page_title="토스증권 스타일 퀀트 WTS",
    page_icon="💸",
    layout="wide",
    initial_sidebar_state="collapsed"
)

render_styles("tokens.css", "base.css", "dashboard.css")

# -----------------------------------------------------------------------------
# 🔒 보안 인증: 등록된 관리자 전용 열람 2중 잠금 (Default Deny + 암호/매직키 지원)
# -----------------------------------------------------------------------------
AUTHORIZED_EMAILS = {"tomsslee043@gmail.com"}

ADMIN_PASSWORDS = set()

# 환경 변수 및 Streamlit Secrets에서만 안전하게 비밀번호 주입 (소스코드 평문 하드코딩 원천 금지)
env_pw = os.environ.get("ADMIN_PASSWORD", "").strip()
if env_pw:
    ADMIN_PASSWORDS.add(env_pw)

try:
    if "ADMIN_PASSWORD" in st.secrets:
        secret_pw = str(st.secrets["ADMIN_PASSWORD"]).strip()
        if secret_pw:
            ADMIN_PASSWORDS.add(secret_pw)
except Exception:
    pass

def check_has_auth_secret() -> bool:
    try:
        return "auth" in st.secrets
    except Exception:
        return False

# 배포 환경 감지 (Streamlit Community Cloud / 컨테이너 배포 환경)
is_cloud = (
    os.path.exists("/mount/src") or 
    "STREAMLIT_SHARING_MODE" in os.environ or 
    os.environ.get("USER") == "appuser" or
    check_has_auth_secret()
)

# 보안 강화: URL 쿼리스트링에 key 파라미터가 존재할 경우 주소창/로그 노출 방지를 위해 즉시 제거
if "key" in st.query_params:
    try:
        st.query_params.clear()
    except Exception:
        pass

# 세션 인증 상태 기본값 안전 초기화 (AttributeError 원천 차단)
if "is_authenticated" not in st.session_state:
    st.session_state.is_authenticated = False

# 1. Google OAuth 확인 (내 계정 tomsslee043@gmail.com 만 승인)
google_login_rejected = False
rejected_email = ""
if hasattr(st, "user") and getattr(st.user, "is_logged_in", False):
    user_obj = st.user
    current_user_email = ""
    if hasattr(user_obj, "email") and user_obj.email:
        current_user_email = str(user_obj.email).strip().lower()
    elif isinstance(user_obj, dict) and user_obj.get("email"):
        current_user_email = str(user_obj["email"]).strip().lower()

    if current_user_email in AUTHORIZED_EMAILS:
        st.session_state.is_authenticated = True
    else:
        google_login_rejected = True
        rejected_email = current_user_email

# 2. 클라우드 환경에서 미인증 시 전용 로그인 폼 렌더링 후 정지
if is_cloud and not st.session_state.get("is_authenticated", False):
    st.markdown("""
    <div class="login-panel">
        <h1>관리자 전용 인증</h1>
        <p>등록된 관리자 전용 비공개 퀀트 대시보드입니다.<br>
        Google 관리자 계정 로그인 또는 관리자 암호로 입장하세요.</p>
    </div>
    """, unsafe_allow_html=True)

    col_l, col_center, col_r = st.columns([1, 1.2, 1])
    with col_center:
        if google_login_rejected:
            st.error(f"⛔ 접근 불가: 승인되지 않은 계정({rejected_email})입니다. 등록된 관리자 계정으로 로그인해주세요.")
            if hasattr(st, "logout"):
                st.button("다른 계정으로 다시 로그인", on_click=st.logout, use_container_width=True)

        if hasattr(st, "login") and check_has_auth_secret():
            if st.button("Google 계정으로 로그인", use_container_width=True):
                st.login()
            st.markdown("<div class='login-divider'>또는 관리자 암호 입력</div>", unsafe_allow_html=True)

        with st.form("admin_login_form", clear_on_submit=True):
            entered_pw = st.text_input("관리자 암호", type="password", placeholder="관리자 비밀번호 입력")
            submit_btn = st.form_submit_button("대시보드 입장", use_container_width=True)
            if submit_btn:
                if not ADMIN_PASSWORDS:
                    st.error("⚠️ Streamlit Secrets에 ADMIN_PASSWORD가 설정되지 않았습니다.")
                elif entered_pw:
                    pw_input = entered_pw.strip()
                    is_valid = any(hmac.compare_digest(pw_input, valid_pw) for valid_pw in ADMIN_PASSWORDS)
                    if is_valid:
                        st.session_state.is_authenticated = True
                        st.success("인증되었습니다! 대시보드로 이동합니다...")
                        st.rerun()
                    else:
                        st.error("암호가 일치하지 않습니다. 다시 확인해주세요.")
                else:
                    st.warning("비밀번호를 입력해주세요.")

    st.stop()



# 3. 데이터 로드
def load_user_portfolio():
    path = os.path.join(os.path.dirname(__file__), 'user_portfolio.json')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'holdings': {}}

portfolio_data = load_user_portfolio()
holdings = portfolio_data.get('holdings', {})

@st.cache_data(ttl=900)
def get_realtime_usd_krw() -> float:
    """야후 파이낸스(USDKRW=X)에서 실시간 달러/원 환율을 가져옵니다."""
    try:
        ticker = yf.Ticker('USDKRW=X')
        # 미확정 당일 봉이 NaN으로 붙어 오는 경우가 있어 유효한 종가만 사용
        closes = ticker.history(period='5d', interval='1d')['Close'].dropna()
        if not closes.empty:
            return round(float(closes.iloc[-1]), 1)
    except Exception:
        pass
    return 1380.0

current_usd_krw = get_realtime_usd_krw()

if 'rec_selected_ticker' not in st.session_state:
    st.session_state.rec_selected_ticker = 'TSM'
if 'port_selected_ticker' not in st.session_state:
    st.session_state.port_selected_ticker = 'RKLX'


def render_quant_performance_tracker(key_suffix: str = ""):
    """통계 기반 성과 추적 및 버전별 가중치 보정 로그 UI 렌더러."""
    try:
        from quant_core.tracker import evaluate_and_learn_from_history, CURRENT_STRATEGY_VERSION

        col_title, col_ver = st.columns([2, 1])
        with col_title:
            st.markdown('<h3 class="qs-section">전략 버전별 실현 성과 & 통계 신뢰도</h3>', unsafe_allow_html=True)
        with col_ver:
            version_map = {
                "v1.0.0 (동결 전략)": "v1.0.0",
                "전체 통합 (All)": "all",
                "legacy (구버전)": "legacy"
            }
            selected_label = st.selectbox(
                "전략 버전 선택", options=list(version_map.keys()), index=0,
                key=f"sb_strat_ver_{key_suffix}"
            )
            target_version = version_map[selected_label]

        track_res = evaluate_and_learn_from_history(strategy_version=target_version)
        adaptive = track_res.get('adaptive_weights', {})
        comp_cnt = track_res.get('completed_count', 0)
        ong_cnt = track_res.get('ongoing_count', 0)
        tot_cnt = track_res.get('total_recs', 0)
        ev_val = track_res.get('expected_value', 0.0)
        pf_display = track_res.get('profit_factor_display', str(track_res.get('profit_factor', 0.0)))
        win_r = track_res.get('win_rate', 0.0)
        net_ret = track_res.get('avg_return_net', 0.0)
        rule_desc = {
            "v1.0.0": "<b>동결 전략 규칙 (v1.0.0)</b>: 최소 점수 68점 / 최소 손익비 1.20:1 / 20거래일 만료 / 동적 매물대 손익비 (왕복비용 0.25% 차감)",
            "legacy": "<b>구버전 이력 (legacy)</b>: 전략 버전 분리 이전의 초기 추천 데이터 (동결 규칙 미적용)",
            "all": "<b>전체 통합 통계</b>: 과거 구버전(legacy) 및 신규 동결 버전(v1.0.0) 누적 전체 표본"
        }.get(target_version, "")
        st.markdown(f"""
        <div class="qs-trust">
          <p><b>표본 신뢰도: {escape(track_res.get('sample_tier', '실험 단계'))}</b><br>
          오차범위: {escape(track_res.get('sample_error_margin', '±20%p 이상'))}</p>
          <p class="qs-caption">{escape(track_res.get('sample_tier_desc', ''))}</p>
          <p class="qs-caption">{rule_desc}</p>
        </div>
        """, unsafe_allow_html=True)
        if comp_cnt > 0:
            ev_color = "qs-positive" if ev_val >= 0 else "qs-negative"
            net_color = "qs-positive" if net_ret >= 0 else "qs-negative"
            st.markdown(f"""
            <dl class="qs-performance-grid">
              <div><dt>1회 기대값 (EV)</dt><dd class="{ev_color}">{ev_val:+}%</dd><small>1회 추천당 장기기대</small></div>
              <div><dt>Profit Factor</dt><dd class="qs-warning">{pf_display}</dd><small>총이익 / 총손실</small></div>
              <div><dt>실제 승률 (N={comp_cnt})</dt><dd class="qs-positive">{win_r}%</dd><small>{track_res.get('wins', 0)}승 {track_res.get('losses', 0)}패</small></div>
              <div><dt>순수익률 (비용차감)</dt><dd class="{net_color}">{net_ret:+}%</dd><small>슬리피지 0.25% 반영</small></div>
            </dl>
            <p class="qs-caption">[{target_version}] 누적 추천: 총 {tot_cnt}개 (완료 {comp_cnt}건 / 진행 중 {ong_cnt}건)<br>
            평균 이익 +{track_res.get('avg_win', 0.0)}% vs 평균 손실 {track_res.get('avg_loss', 0.0)}%</p>
            """, unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <p class="qs-copy"><b>[{target_version}] 현재 상태</b>: <b>{ong_cnt}개 종목 실시간 성과 추적 중</b>
            (익절/손절 도달 시 기대값·Profit Factor 자동 산출)</p>
            """, unsafe_allow_html=True)

        penalized = adaptive.get('penalized_factors', [])
        boosted = adaptive.get('boosted_factors', [])
        cooldowns = adaptive.get('cooldown_tickers', {})
        failures = adaptive.get('failure_notes', [])
        st.markdown('<h3 class="qs-section">통계 기반 동적 가중치 보정 (Feedback)</h3>', unsafe_allow_html=True)
        if penalized:
            for pf in penalized:
                st.markdown(f'<p class="qs-feedback qs-negative"><b>{escape(pf["tag"])}</b>: 최근 승률 {pf["win_rate"]}% 저조, 보정 <b>{pf["adj"]}</b></p>', unsafe_allow_html=True)
        if boosted:
            for bf in boosted:
                st.markdown(f'<p class="qs-feedback qs-positive"><b>{escape(bf["tag"])}</b>: 최근 승률 {bf["win_rate"]}% 우수, 보정 <b>{bf["adj"]}</b></p>', unsafe_allow_html=True)
        if cooldowns:
            cd_names = [f"{v['name']}({k})" for k, v in cooldowns.items()]
            st.markdown(f'<p class="qs-feedback qs-warning"><b>최근 손절 쿨다운</b>: {escape(", ".join(cd_names))} (추천 감점 -12점)</p>', unsafe_allow_html=True)
        if failures:
            st.markdown('<h3 class="qs-section">최근 실패 원인 진단 (성과 감사 로그)</h3>', unsafe_allow_html=True)
            for fn in failures[-3:]:
                st.markdown(f"""
                <div class="qs-audit"><b>{escape(fn['name'])} ({escape(fn['ticker'])})</b> / {escape(fn['date'])}<br>
                원인: <span class="qs-negative">{escape(fn['reason'])}</span></div>
                """, unsafe_allow_html=True)
        st.markdown('<p class="qs-caption">※ 매일 장 마감 후 추천 종목의 실제 주가 궤적을 추적하여 손익비와 성공/실패 원인을 엄밀하게 재산출합니다.</p>', unsafe_allow_html=True)
    except Exception as e:
        st.caption(f"성과 추적 데이터 분석 중: {e}")


# ==============================================================================
has_holdings = bool(holdings)
_tab_names = ["패턴 구간", "점수제 추천"] + (["포트폴리오"] if has_holdings else [])
_tabs = st.tabs(_tab_names)
tab_pattern, tab_rec = _tabs[0], _tabs[1]
tab_port = _tabs[2] if has_holdings else None

with tab_pattern:
    render_pattern_tab()



# ==============================================================================
# 탭 1: 🔥 오늘의 퀀트 유망주 (피보나치·추세선·다이버전스 & 통계 실증)
# ==============================================================================
with tab_rec:
    render_styles("score.css")
    top_col1, top_col2 = st.columns([3, 2])
    with top_col1:
        st.markdown('<h1 class="qs-title">점수제 추천</h1>', unsafe_allow_html=True)
        diag = get_supabase_diagnostics()
        if diag.get("is_healthy"):
            db_badge = '<span class="qs-badge positive">Supabase 연동됨 (읽기·쓰기 실증 완료)</span>'
        elif diag.get("can_read") and not diag.get("can_write"):
            db_badge = '<span class="qs-badge warning" title="현재 키는 읽기만 가능하며 비공개 RLS 쓰기가 차단됩니다. Streamlit Secrets에 service_role 키를 설정하세요.">Supabase 쓰기 권한 부족 (service_role 키 필요)</span>'
        elif diag.get("client_created") and not diag.get("can_read"):
            db_badge = '<span class="qs-badge negative" title="테이블 조회 실패. supabase_schema.sql 마이그레이션을 실행하세요.">Supabase 접근 불가 (스키마 미적용 또는 RLS 차단)</span>'
        else:
            if not diag.get("has_url") and not diag.get("has_key"):
                fail_hint = "Secrets 키 미인식"
            elif not diag.get("has_url"):
                fail_hint = "SUPABASE_URL 미인식"
            elif not diag.get("has_key"):
                fail_hint = "SUPABASE_KEY 미인식"
            elif diag.get("last_error"):
                fail_hint = "연결 오류"
            else:
                fail_hint = "로컬 모드"
            db_badge = f'<span class="qs-badge" title="{escape(str(diag.get("last_error", "")))}">로컬 스토리지 모드 ({fail_hint})</span>'
        version_badge = '<span class="qs-badge">Strategy v1.0.0</span>'
        st.markdown(f'<div class="qs-meta">{version_badge}{db_badge}</div>', unsafe_allow_html=True)
    with top_col2:
        top_btn1, top_btn2 = st.columns([1.7, 1])
        with top_btn1:
            if st.button("오늘자 재스캔", use_container_width=True):
                with st.spinner("과거 패턴 통계 검증 및 다각도 퀀트 스캔을 진행 중입니다..."):
                    run_full_market_scan(force_refresh=True)
                st.rerun()
        with top_btn2:
            if st.button("로그아웃", use_container_width=True):
                st.session_state.is_authenticated = False
                st.rerun()
    st.markdown('<p class="qs-intro">피보나치 지지·빗각 돌파·다이버전스 타점 및 최소 손익비(1.20:1 이상)·최소 퀀트 점수(68점 이상) 커트라인을 통과한 엄선 종목입니다. (뉴욕 증시 거래일 기준)</p>', unsafe_allow_html=True)
    recs = run_full_market_scan(force_refresh=False)
    if not recs:
        st.markdown("""
        <section class="qs-empty">
          <h2 class="qs-empty-title">오늘은 시장 진입 적격 종목이 없습니다 (현금 관망 권고)</h2>
          <p>현재 82개 종목 중 엄격한 리스크 관리 커트라인(최소 점수 68점 이상, 손익비 1.2 이상, 최근 손절 쿨다운 미해당)을 만족하는 안전한 타점이 포착되지 않았습니다.</p>
          <p><b>손실 위험이 높은 애매한 장세에서는 무리한 진입 대신 현금을 보유하고 관망하는 것이 가장 훌륭한 퀀트 전략입니다.</b></p>
        </section>
        """, unsafe_allow_html=True)
        with st.expander("통계 기반 성과 추적 & 규칙 기반 가중치 보정 로그", expanded=True):
            render_quant_performance_tracker(key_suffix="empty")
    else:
        active_rec = next((r for r in recs if r['ticker'] == st.session_state.rec_selected_ticker), recs[0])
        rec_col_side, rec_col_main = st.columns([1, 2], gap="large")
        with rec_col_side:
            st.markdown('<h2 class="qs-section">오늘의 유망주 리스트</h2>', unsafe_allow_html=True)
            st.markdown('<p class="qs-caption">오늘의 나스닥 퀀트 유망주 Top 7</p>', unsafe_allow_html=True)
            for idx, r in enumerate(recs, 1):
                t = r['ticker']
                is_active = (t == active_rec['ticker'])
                p_status = r.get('pattern_status', '타점 형성 중')
                if p_status == "진입 적기":
                    status_tag, status_color = "진입 적기", "qs-positive"
                elif p_status == "타점 임박":
                    status_tag, status_color = "돌파 임박", "qs-warning"
                elif "쿨다운" in p_status:
                    status_tag, status_color = "쿨다운", "qs-negative"
                else:
                    status_tag, status_color = "관심 추적", ""
                tags_html = "".join([f'<span>#{escape(tag)}</span>' for tag in r['tags']])
                t1_p = r.get('target_1_pct', round(((r['bull_target_1']/r['current_price'])-1)*100, 1))
                rr = r.get('risk_reward_ratio', 1.5)
                has_analyst_r = r.get('has_analyst_target', False)
                wall_st_note = f"월가 +{r['upside_pct']}%" if (has_analyst_r and r.get('upside_pct') is not None) else "월가 미집계"
                with st.container(key=f"card_{t}"):
                    st.markdown(f"""
                    <div class="stock-unified-card qs-stock {'active' if is_active else ''}">
                      <div class="qs-stock-top"><span>#{idx} {escape(r['name'])}</span><span class="qs-stock-ticker">{escape(t)}</span></div>
                      <div class="qs-stock-status {status_color}">{status_tag}</div>
                      <div class="qs-stock-values"><span>현재가 <b>${r['current_price']:.2f}</b></span><span class="qs-positive">1차목표 <b>+{t1_p:.1f}%</b></span></div>
                      <div class="qs-stock-notes"><span>손익비 <b>{rr}:1</b></span><span>{wall_st_note}</span></div>
                      <div class="qs-stock-tags">{tags_html}</div>
                    </div>
                    """, unsafe_allow_html=True)
                    if st.button(f"{t} 선택", key=f"btn_stock_{t}", use_container_width=True):
                        st.session_state.rec_selected_ticker = t
                        st.rerun()
        with rec_col_main:
            fib_data = active_rec.get('fibonacci', {})
            tl_data = active_rec.get('trendline', {})
            bt_data = active_rec.get('backtest_stats', {'sample_count': 0, 'win_rate': None, 'avg_return': 0.0})
            has_analyst = active_rec.get('has_analyst_target', False)
            wall_st_target = active_rec.get('target_price')
            wall_st_upside = active_rec.get('upside_pct')
            analyst_count = active_rec.get('analyst_count', 0)
            if has_analyst and wall_st_target and wall_st_upside is not None:
                analyst_label = f"월가 컨센서스 목표 <b>${wall_st_target:.2f}</b> (<span class='qs-warning'>+{wall_st_upside:.1f}%</span>, {analyst_count}명 분석)"
            else:
                analyst_label = "월가 컨센서스: 집계 표본 부족 (미제공)"
            t1_pct = active_rec.get('target_1_pct', round(((active_rec['bull_target_1']/active_rec['current_price'])-1)*100, 1))
            rr_ratio = active_rec.get('risk_reward_ratio', 1.5)
            st.markdown(f"""
            <section class="qs-detail">
              <h2 class="qs-detail-title">{escape(active_rec['name'])}<span>({escape(active_rec['ticker'])})</span></h2>
              <div class="qs-context"><span>{escape(active_rec['category'])}</span><span>{escape(active_rec.get('pattern_status', '타점 관찰'))}</span></div>
              <div class="qs-overview">
                <div class="qs-price"><small>현재가</small>${active_rec['current_price']:.2f}</div>
                <dl>
                  <div><dt>단기 1차 기대수익</dt><dd class="qs-positive">+{t1_pct:.1f}%</dd></div>
                  <div><dt>퀀트 기술 점수</dt><dd>{active_rec['total_score']}점</dd></div>
                  <div><dt>손익비</dt><dd class="qs-warning">{rr_ratio}:1</dd></div>
                </dl>
              </div>
              <p class="qs-analyst">단기 1차 목표 <b>${active_rec['bull_target_1']:.2f}</b><br>{analyst_label}</p>
            </section>
            """, unsafe_allow_html=True)
            st.markdown('<h3 class="qs-section" id="100">실전 매매 손익 및 리스크 계산 (100만원 투자 기준)</h3>', unsafe_allow_html=True)
            st.markdown(f'<p class="qs-caption">100만원 진입 시 예상 목표 수익과 최대 허용 손실을 산출합니다. (실시간 환율 $1 = {current_usd_krw:,.1f}원 적용)</p>', unsafe_allow_html=True)
            curr_p = active_rec['current_price']
            t1 = active_rec['bull_target_1']
            t2 = active_rec['bull_target_2']
            sl = active_rec['stop_loss']
            rate = current_usd_krw
            inv = 1000000
            pct_1 = active_rec.get('target_1_pct', round(((t1 / curr_p) - 1) * 100, 1))
            pct_2 = active_rec.get('target_2_pct', round(((t2 / curr_p) - 1) * 100, 1))
            pct_sl = active_rec.get('stop_loss_pct', round(((sl / curr_p) - 1) * 100, 1))
            gain_1 = inv * (pct_1 / 100)
            gain_2 = inv * (pct_2 / 100)
            loss_sl = inv * (pct_sl / 100)
            share_gain_1 = (t1 - curr_p) * rate
            share_gain_2 = (t2 - curr_p) * rate
            share_loss = (sl - curr_p) * rate
            st.markdown(f"""
            <dl class="qs-levels">
              <div><dt>1단계: 들어가기 (진입)</dt><dd>${curr_p:.2f}</dd><small>1주당 {int(curr_p*rate):,}원</small></div>
              <div><dt>2단계: 1차 목표 (익절)</dt><dd class="qs-positive">${t1:.2f}</dd><div class="qs-outcome">+{int(gain_1):,}원 이익</div><small>+{pct_1:.1f}% (1주당 +{int(share_gain_1):,}원)</small></div>
              <div><dt>3단계: 위험 손절선 (방어)</dt><dd class="qs-negative">${sl:.2f}</dd><div class="qs-outcome">{int(loss_sl):,}원 손실</div><small>{pct_sl:.1f}% (1주당 {int(share_loss):,}원)</small></div>
              <div><dt>4단계: 손익비 (Reward/Risk)</dt><dd class="qs-warning">{rr_ratio} : 1</dd><div class="qs-outcome">위험 대비 {rr_ratio}배 기대</div><small>(2차 목표 ${t2:.2f})</small></div>
            </dl>
            <p class="qs-copy"><b>100만원 투자 시 위험 대비 보상 요약:</b><br>
            지금 <b>${curr_p:.2f} (약 {int(curr_p*rate):,}원)</b>에 100만원 진입하여 1차 목표 <b>${t1:.2f}</b> 도달 시 <b class="qs-positive">+{int(gain_1):,}원 (+{pct_1:.1f}%)</b>의 이익을 기대할 수 있습니다.<br>
            반면, 기술적 손절선 <b>${sl:.2f}</b> 이탈 시 <b class="qs-negative">{int(loss_sl):,}원 ({pct_sl:.1f}%)</b>에서 손실을 철저히 방어합니다. (<b>손익비 {rr_ratio}:1</b>)</p>
            """, unsafe_allow_html=True)
            st.markdown('<h3 class="qs-section" id="4c5b61f4">추천 근거 리포트</h3>', unsafe_allow_html=True)
            for reason_item in active_rec.get('core_reasons', []):
                if isinstance(reason_item, dict):
                    cat_tag = reason_item.get('category', '퀀트 종합 분석')
                    clean_text = reason_item.get('text', '')
                else:
                    cat_tag = "퀀트 분석"
                    clean_text = str(reason_item)
                clean_text = clean_text.replace('**', '').replace('***', '').replace('*', '')
                st.markdown(f'<article class="qs-reason"><h4 class="qs-reason-title">{escape(cat_tag)}</h4><p>{escape(clean_text)}</p></article>', unsafe_allow_html=True)
        with st.expander("통계 기반 성과 추적 & 규칙 기반 가중치 보정 로그"):
            render_quant_performance_tracker(key_suffix="list")




# ==============================================================================
# 탭 2: 💼 내 보유 포트폴리오 (토스 WTS 화면 보존)
# ==============================================================================
def render_portfolio_tab(tab_container, holdings_dict, usd_krw_rate):
    with tab_container:
        total_eval = 0.0
        total_cost = 0.0
        holdings_summary = {}

        for t, h in holdings_dict.items():
            df_temp = fetch_stock_data(t, period='5d')
            p = float(df_temp['Close'].iloc[-1]) if not df_temp.empty else h['avg_price']
            prev_p = float(df_temp['Close'].iloc[-2]) if len(df_temp) > 1 else p
            shares = h['shares']
            avg_p = h['avg_price']
            
            cost = shares * avg_p
            e_val = shares * p
            pnl = e_val - cost
            pnl_pct = (pnl / cost) * 100 if cost > 0 else 0.0
            daily_change_pct = ((p - prev_p) / prev_p) * 100 if prev_p > 0 else 0.0
            
            total_eval += e_val
            total_cost += cost
            
            holdings_summary[t] = {
                'name': h.get('name', t),
                'shares': shares,
                'avg_price': avg_p,
                'curr_price': p,
                'eval_val': e_val,
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'daily_change_pct': daily_change_pct
            }

        total_pnl = total_eval - total_cost
        total_pnl_pct = (total_pnl / total_cost) * 100 if total_cost > 0 else 0.0

        curr_ticker = st.session_state.port_selected_ticker
        curr_info = holdings_summary.get(curr_ticker, list(holdings_summary.values())[0])

        df_active = fetch_stock_data(curr_ticker, period='1y')
        if df_active.empty:
            df_active = fetch_stock_data(curr_ticker, period='6mo')
        df_active = calculate_all_indicators(df_active)
        health = evaluate_technical_health(df_active)
        pred = predict_price_scenarios(df_active, days_ahead=5)

        curr_price = float(df_active['Close'].iloc[-1]) if not df_active.empty else curr_info['curr_price']
        prev_price = float(df_active['Close'].iloc[-2]) if len(df_active) > 1 else curr_price
        diff = curr_price - prev_price
        diff_pct = (diff / prev_price) * 100 if prev_price > 0 else 0.0

        is_up = diff >= 0
        color_hex = "#f04452" if is_up else "#3182f6"
        badge_text = "3x" if "3X" in PORTFOLIO_CONFIG.get(curr_ticker, {}).get('name', '') else ("2x" if "2X" in PORTFOLIO_CONFIG.get(curr_ticker, {}).get('name', '') else "1x")

        p_h1, p_h2, p_h3 = st.columns([3, 2.5, 1.5])
        with p_h1:
            st.markdown(f"""
            <div style="display:flex; align-items:center; gap:8px;">
                <span class="toss-tag-clean" style="background-color:#ff9100; color:#ffffff; font-weight:800;">{badge_text}</span>
                <span style="font-size:20px; font-weight:800; color:#ffffff;">{PORTFOLIO_CONFIG.get(curr_ticker, {}).get('name', curr_ticker)}</span>
                <span style="font-size:13px; color:#7b7f94;">{curr_ticker}</span>
            </div>
            <div style="display:flex; align-items:baseline; margin-top:4px;">
                <span style="font-size:28px; font-weight:800; color:#ffffff;">${curr_price:.2f}</span>
                <span style="font-size:13px; color:#7b7f94; margin-left:8px;">{int(curr_price * usd_krw_rate):,}원</span>
                <span style="color:{color_hex}; font-weight:700; margin-left:14px; font-size:15px;">
                    {'▲' if is_up else '▼'} ${abs(diff):.2f} ({diff_pct:+.2f}%)
                </span>
            </div>
            """, unsafe_allow_html=True)
        with p_h2:
            high_1d = float(df_active['High'].iloc[-1]) if not df_active.empty else curr_price * 1.02
            low_1d = float(df_active['Low'].iloc[-1]) if not df_active.empty else curr_price * 0.98
            high_52 = float(df_active['High'].max()) if not df_active.empty else curr_price * 1.5
            low_52 = float(df_active['Low'].min()) if not df_active.empty else curr_price * 0.5
            st.markdown(f"""
            <div style="padding-top:8px; font-size:12px; color:#7b7f94;">
                <div>1일 범위: <b>${low_1d:.2f}</b> ~ <b>${high_1d:.2f}</b></div>
                <div style="margin-top:4px;">52주 범위: <b>${low_52:.2f}</b> ~ <b>${high_52:.2f}</b></div>
            </div>
            """, unsafe_allow_html=True)
        with p_h3:
            st.markdown(f"""
            <div style="background-color:#17181f; border-radius:10px; padding:8px 14px; text-align:right;">
                <div style="font-size:11px; color:#7b7f94;">내 총 평가액</div>
                <div style="font-size:17px; font-weight:800; color:#ffffff;">${total_eval:,.2f}</div>
                <div style="font-size:11px; color:{'#f04452' if total_pnl>=0 else '#3182f6'};">{total_pnl:+,.2f} USD ({total_pnl_pct:+.2f}%)</div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("<hr style='border:0; border-top:1px solid #23252e; margin: 10px 0 14px 0;'>", unsafe_allow_html=True)

        p_main, p_side = st.columns([2.8, 1.2], gap="medium")

        with p_side:
            st.markdown("<b style='font-size:16px; color:#ffffff;'>내 보유 종목</b>", unsafe_allow_html=True)
            for ticker_key, item in holdings_summary.items():
                is_sel = (ticker_key == curr_ticker)
                p_c = "#f04452" if item['pnl'] >= 0 else "#3182f6"
                
                with st.container(key=f"card_port_{ticker_key}"):
                    st.markdown(f"""
                    <div class="stock-unified-card {'active' if is_sel else ''}">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <b style="font-size:15px; color:#ffffff;">{ticker_key} <span style="font-size:12px; color:#8b90a4; font-weight:normal;">({item.get('name', ticker_key)})</span></b>
                            <span style="font-size:12px; color:#9094a6;">보유 <b>{item['shares']}주</b></span>
                        </div>
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-top:8px;">
                            <span style="font-size:12px; color:#8b90a4;">평단 ${item['avg_price']:.2f}</span>
                            <div style="text-align:right;">
                                <span style="font-size:13px; font-weight:700; color:#ffffff;">${item['eval_val']:,.2f}</span>
                                <span style="font-size:12px; font-weight:700; color:{p_c}; margin-left:6px;">{item['pnl']:+,.2f} ({item['pnl_pct']:+.1f}%)</span>
                            </div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    if st.button(f"port_sel_{ticker_key}", key=f"port_btn_{ticker_key}", use_container_width=True):
                        st.session_state.port_selected_ticker = ticker_key
                        st.rerun()

        with p_main:
            my_avg = curr_info['avg_price']
            fig_port = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.75, 0.25])
            fig_port.add_trace(go.Candlestick(
                x=df_active.index,
                open=df_active['Open'], high=df_active['High'], low=df_active['Low'], close=df_active['Close'],
                increasing_line_color='#f04452', decreasing_line_color='#3182f6',
                name='주가'
            ), row=1, col=1)

            if 'EMA_21' in df_active.columns:
                fig_port.add_trace(go.Scatter(x=df_active.index, y=df_active['EMA_21'], line=dict(color='#ffa726', width=1.2), name='EMA 20'), row=1, col=1)
            
            if my_avg > 0:
                pnl_curr = ((curr_price / my_avg) - 1) * 100
                t_color = "#f04452" if pnl_curr >= 0 else "#3182f6"
                fig_port.add_hline(
                    y=my_avg, line_dash="dot", line_color="#ffffff", line_width=1.5,
                    annotation_text=f"내 평균 ${my_avg:.2f} ({pnl_curr:+.1f}%)",
                    annotation_position="top left", annotation_bgcolor=t_color, annotation_font_color="#ffffff",
                    row=1, col=1
                )
                
            fig_port.add_trace(go.Bar(x=df_active.index, y=df_active['Volume'], marker_color=['#f04452' if c>=o else '#3182f6' for c, o in zip(df_active['Close'], df_active['Open'])], name='거래량'), row=2, col=1)
            fig_port.update_layout(
                height=380, template='plotly_dark', paper_bgcolor='#17181f', plot_bgcolor='#17181f',
                margin=dict(l=10, r=10, t=10, b=10), xaxis_rangeslider_visible=False,
                xaxis=dict(showgrid=False), yaxis=dict(showgrid=True, gridcolor='#23252e', side='right'), yaxis2=dict(showgrid=False),
                showlegend=False
            )
            st.plotly_chart(fig_port, use_container_width=True)

            st.markdown(f"""
            <div class="toss-panel">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <b style="font-size:15px; color:#ffffff;">💡 {curr_ticker} 기술 진단: <span style="color:{'#00e676' if health['score']>=20 else ('#f04452' if health['score']<=-20 else '#ffa726')};">{health['signal']}</span></b>
                    <span style="font-size:13px; color:#9094a6;">내 평단: <b>${my_avg:.2f}</b> ({curr_info['shares']}주)</span>
                </div>
                <div style="font-size:13px; color:#cfcfd4; margin-top:8px; line-height:1.7;">
                    • <b>1차 반등 목표가</b>: <span style="color:#00e676; font-weight:700;">${pred['bull_target_1']:.2f}</span> ({(pred['bull_target_1']/curr_price - 1)*100:+.1f}%)<br>
                    • <b>위험 손절 마지노선</b>: <span style="color:#ff5252; font-weight:700;">${pred['stop_loss']:.2f}</span> ({pred['stop_loss_pct']:+.1f}%)<br>
                    • <b>대응 전략</b>: {'🚨 평단가까지 거리가 멉니다. 1차 목표가 부근까지 반등 시 분할 매도로 현금을 확보하세요.' if curr_info['pnl'] < 0 else '🎉 수익 구간입니다! 내 평단가에 안전 스탑로스(익절선)를 걸어두세요.'}
                </div>
            </div>
            """, unsafe_allow_html=True)

if has_holdings:
    render_portfolio_tab(tab_port, holdings, current_usd_krw)

