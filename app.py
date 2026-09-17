"""
app.py
토스증권(Toss Securities) WTS 스타일 퀀트 트레이딩 대시보드
- 모드 1: 🔥 오늘의 퀀트 유망주 (피보나치·추세선돌파·다이버전스 & 통계 실증 검증)
- 모드 2: 💼 내 보유 포트폴리오 (토스 WTS 뷰 & 실시간 계좌)
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
import os
import hmac
from datetime import datetime
import yfinance as yf

from quant_core.data_loader import fetch_stock_data, PORTFOLIO_CONFIG
from quant_core.indicators import calculate_all_indicators
from quant_core.prediction import evaluate_technical_health, predict_price_scenarios
from quant_core.screener import run_full_market_scan
from quant_core.db import is_supabase_enabled, get_supabase_diagnostics

# 1. 페이지 설정
st.set_page_config(
    page_title="토스증권 스타일 퀀트 WTS",
    page_icon="💸",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# -----------------------------------------------------------------------------
# 🔒 보안 인증: 등록된 관리자 전용 열람 2중 잠금 (Default Deny + 암호/매직키 지원)
# -----------------------------------------------------------------------------
AUTHORIZED_EMAILS = {"tomsslee043@gmail.com"}

ADMIN_PASSWORDS = {
    "townboy",
    "tomsslee043",
    "0403",
    "makeownmoney"
}

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

try:
    if "ADMIN_PASSWORD" in st.secrets:
        ADMIN_PASSWORDS.add(str(st.secrets["ADMIN_PASSWORD"]).strip().lower())
except Exception:
    pass

# 보안 강화: URL 쿼리스트링에 key 파라미터가 존재할 경우 주소창/로그 노출 방지를 위해 즉시 제거
if "key" in st.query_params:
    try:
        st.query_params.clear()
    except Exception:
        pass

if "is_authenticated" not in st.session_state:
    st.session_state.is_authenticated = False

# Google OAuth 확인 (st.user)
if hasattr(st, "user") and getattr(st.user, "is_logged_in", False):
    current_user_email = (st.user.get("email") or "").strip().lower()
    if current_user_email in AUTHORIZED_EMAILS:
        st.session_state.is_authenticated = True

# 클라우드 환경에서 미인증 시 전용 로그인 폼 렌더링 후 정지 (URL 노출 차단 & 안전한 세션 인증)
if is_cloud and not st.session_state.is_authenticated:
    st.markdown("""
    <style>
        @import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css');
        html, body, [class*="css"], .stApp {
            background-color: #0f1015 !important;
            color: #ffffff !important;
            font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', Roboto, sans-serif !important;
        }
    </style>
    <div style="max-width:440px; margin:70px auto 20px auto; background-color:#17181f; border:1px solid #282a36; border-radius:16px; padding:32px 24px; text-align:center;">
        <div style="font-size:38px; margin-bottom:12px;">🔒</div>
        <div style="font-size:20px; font-weight:800; color:#ffffff; margin-bottom:6px;">관리자 전용 인증</div>
        <div style="font-size:13px; color:#8b90a4; line-height:1.6; margin-bottom:16px;">
            등록된 관리자 전용 비공개 퀀트 대시보드입니다.<br>
            대시보드 입장을 위해 관리자 암호를 입력해주세요.
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    col_l, col_center, col_r = st.columns([1, 1.2, 1])
    with col_center:
        with st.form("admin_login_form", clear_on_submit=True):
            entered_pw = st.text_input("관리자 암호", type="password", placeholder="암호 입력 (기본: townboy)", label_visibility="collapsed")
            submit_btn = st.form_submit_button("🔑 대시보드 입장", use_container_width=True)
            if submit_btn and entered_pw:
                pw_input = entered_pw.strip().lower()
                is_valid = any(hmac.compare_digest(pw_input, valid_pw) for valid_pw in ADMIN_PASSWORDS)
                if is_valid:
                    st.session_state.is_authenticated = True
                    st.success("인증되었습니다! 대시보드로 이동합니다...")
                    st.rerun()
                else:
                    st.error("암호가 일치하지 않습니다. 다시 확인해주세요.")

        if hasattr(st, "login") and check_has_auth_secret():
            st.markdown("<div style='text-align:center; margin:12px 0 8px 0; color:#606478; font-size:12px;'>또는</div>", unsafe_allow_html=True)
            if st.button("🌐 Google 계정으로 로그인", use_container_width=True):
                st.login()

    st.stop()



# 2. 토스증권 프리미엄 미니멀 다크 CSS (눈 피로도 0% 차분한 톤)
st.markdown("""
<style>
    @import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css');

    html, body, [class*="css"], .stApp {
        background-color: #0f1015 !important;
        color: #ffffff !important;
        font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', Roboto, sans-serif !important;
        letter-spacing: -0.3px;
    }

    /* 토스 패널 (차분한 다크 그레이) */
    .toss-panel {
        background-color: #17181f;
        border-radius: 14px;
        border: 1px solid #23252f;
        padding: 16px;
        margin-bottom: 12px;
    }

    /* 차분하고 세련된 미니멀 태그 (눈이 편안한 톤) */
    .toss-tag-clean {
        background-color: #21232d;
        color: #9ba0b4;
        border-radius: 6px;
        padding: 3px 8px;
        font-size: 11.5px;
        font-weight: 500;
        display: inline-block;
        margin-right: 4px;
        margin-top: 4px;
        border: 1px solid #2a2d3a;
    }

    /* 상태 뱃지 */
    .status-badge-ready {
        background-color: rgba(0, 230, 118, 0.12);
        color: #00e676;
        border: 1px solid rgba(0, 230, 118, 0.25);
        border-radius: 6px;
        padding: 2px 7px;
        font-size: 11.5px;
        font-weight: 700;
    }

    .status-badge-approaching {
        background-color: rgba(255, 215, 0, 0.12);
        color: #ffd700;
        border: 1px solid rgba(255, 215, 0, 0.25);
        border-radius: 6px;
        padding: 2px 7px;
        font-size: 11.5px;
        font-weight: 700;
    }

    .status-badge-watching {
        background-color: #242632;
        color: #8b90a4;
        border-radius: 6px;
        padding: 2px 7px;
        font-size: 11.5px;
        font-weight: 600;
    }

    /* 백테스트 실증 검증 박스 */
    .backtest-box {
        background-color: #1a1c25;
        border-left: 3px solid #3b82f6;
        border-radius: 8px;
        padding: 10px 14px;
        margin-bottom: 12px;
        font-size: 13px;
        color: #d1d5db;
    }

    /* 추천 근거 카드 (깔끔한 다크톤) */
    .reason-box-clean {
        background-color: #1a1c24;
        border: 1px solid #262835;
        border-radius: 10px;
        padding: 12px 14px;
        margin-bottom: 8px;
        font-size: 13.5px;
        line-height: 1.6;
        color: #e2e4ea;
    }

    /* 탭 메뉴 */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        background-color: #14151b;
        padding: 6px;
        border-radius: 12px;
        border: 1px solid #23252e;
    }
    .stTabs [data-baseweb="tab"] {
        color: #8b95a1 !important;
        font-weight: 700 !important;
        font-size: 15px !important;
        border-radius: 8px !important;
        padding: 8px 18px !important;
    }
    .stTabs [aria-selected="true"] {
        background-color: #262835 !important;
        color: #ffffff !important;
    }

    /* 버튼 스타일 */
    .stButton>button {
        background-color: #232530 !important;
        color: #ffffff !important;
        border: 1px solid #313444 !important;
        border-radius: 8px !important;
        font-weight: 600 !important;
    }
    .stButton>button:hover {
        background-color: #313444 !important;
        border-color: #42465a !important;
    }

    /* 종목 일체형 클릭 카드 컨테이너 (버튼 오버레이 기법) */
    div[class*="st-key-card_"] {
        position: relative !important;
        margin-bottom: 12px !important;
    }
    div[class*="st-key-card_"] div[data-testid="stElementContainer"]:last-child {
        position: absolute !important;
        top: 0 !important;
        left: 0 !important;
        width: 100% !important;
        height: 100% !important;
        z-index: 10 !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    div[class*="st-key-card_"] div[data-testid="stButton"] {
        width: 100% !important;
        height: 100% !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    div[class*="st-key-card_"] div[data-testid="stButton"] button {
        width: 100% !important;
        height: 100% !important;
        opacity: 0 !important;
        cursor: pointer !important;
        border: none !important;
        background: transparent !important;
    }
    .stock-unified-card {
        background-color: #17181f;
        border: 1px solid #23252f;
        border-radius: 12px;
        padding: 12px 14px;
        transition: all 0.2s ease-in-out;
    }
    div[class*="st-key-card_"]:hover .stock-unified-card {
        border-color: #3b82f6 !important;
        background-color: #1c1e29 !important;
    }
    .stock-unified-card.active {
        background-color: #1d2232 !important;
        border: 1.8px solid #3b82f6 !important;
        box-shadow: 0 0 12px rgba(59, 130, 246, 0.15);
    }
</style>
""", unsafe_allow_html=True)


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
        df = ticker.history(period='5d', interval='1d')
        if not df.empty:
            return round(float(df['Close'].iloc[-1]), 1)
    except Exception:
        pass
    return 1380.0

current_usd_krw = get_realtime_usd_krw()

if 'rec_selected_ticker' not in st.session_state:
    st.session_state.rec_selected_ticker = 'TSM'
if 'port_selected_ticker' not in st.session_state:
    st.session_state.port_selected_ticker = 'RKLX'


# ==============================================================================
has_holdings = bool(holdings)
if has_holdings:
    tab_rec, tab_port = st.tabs(["🔥 오늘의 퀀트 유망주", "💼 내 보유 포트폴리오 (토스 WTS)"])
else:
    tab_rec = st.container()



# ==============================================================================
# 탭 1: 🔥 오늘의 퀀트 유망주 (피보나치·추세선·다이버전스 & 통계 실증)
# ==============================================================================
with tab_rec:
    top_col1, top_col2 = st.columns([3, 1])
    with top_col1:
        diag = get_supabase_diagnostics()
        if is_supabase_enabled():
            db_badge = '<span style="font-size:11px; background:#1e293b; color:#10b981; padding:2px 8px; border-radius:12px; border:1px solid #059669; font-weight:600; margin-left:8px;">🟢 Supabase DB 연동됨</span>'
        else:
            if not diag.get("has_url") and not diag.get("has_key"):
                fail_hint = "Secrets 키 미인식 (재부팅 대기중)"
            elif not diag.get("has_url"):
                fail_hint = "SUPABASE_URL 미인식"
            elif not diag.get("has_key"):
                fail_hint = "SUPABASE_KEY 미인식"
            elif diag.get("last_error"):
                fail_hint = "연결 오류 (로그 확인)"
            else:
                fail_hint = "폴백 모드"
            db_badge = f'<span style="font-size:11px; background:#1e293b; color:#94a3b8; padding:2px 8px; border-radius:12px; border:1px solid #475569; font-weight:500; margin-left:8px;" title="{diag.get("last_error", "")}">⚪ 로컬 스토리지 모드 ({fail_hint})</span>'
        st.markdown(f"<h3 style='margin:0; font-weight:800; color:#ffffff; display:flex; align-items:center;'>오늘의 나스닥 퀀트 유망주 Top 7 {db_badge}</h3>", unsafe_allow_html=True)
        st.caption("피보나치 지지·빗각 돌파·다이버전스 타점 및 최소 손익비(1.2:1)·점수 커트라인을 통과한 엄선 종목입니다.")
    with top_col2:
        top_btn1, top_btn2 = st.columns([1.7, 1])
        with top_btn1:
            if st.button("🔄 오늘자 재스캔", use_container_width=True):
                with st.spinner("과거 패턴 통계 검증 및 다각도 퀀트 스캔을 진행 중입니다..."):
                    run_full_market_scan(force_refresh=True)
                st.rerun()
        with top_btn2:
            if st.button("🔒 로그아웃", use_container_width=True):
                st.session_state.is_authenticated = False
                st.rerun()

    recs = run_full_market_scan(force_refresh=False)

    # 추천 적격 종목이 없을 때: 토스형 "현금 관망 권고" 안내 배너
    if not recs:
        st.markdown("""
        <div class="toss-panel" style="text-align:center; padding:36px 20px; border-left:4px solid #3182f6; margin-top:16px;">
            <div style="font-size:36px; margin-bottom:8px;">🛡️</div>
            <div style="font-size:20px; font-weight:800; color:#ffffff; margin-bottom:8px;">
                오늘은 시장 진입 적격 종목이 없습니다 (현금 관망 권고)
            </div>
            <div style="font-size:14px; color:#9ba0b4; max-width:620px; margin:0 auto; line-height:1.6;">
                현재 82개 종목 중 엄격한 리스크 관리 커트라인(최소 점수 68점 이상, 손익비 1.2 이상, 최근 손절 쿨다운 미해당)을 만족하는 안전한 타점이 포착되지 않았습니다.<br>
                <b style="color:#ffffff;">손실 위험이 높은 애매한 장세에서는 무리한 진입 대신 현금을 보유하고 관망하는 것이 가장 훌륭한 퀀트 전략입니다.</b>
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("<div style='margin-top:20px;'></div>", unsafe_allow_html=True)
        with st.expander("📊 통계 기반 성과 추적 & 규칙 기반 가중치 보정 로그", expanded=True):
            try:
                from quant_core.tracker import evaluate_and_learn_from_history
                track_res = evaluate_and_learn_from_history()
                adaptive = track_res.get('adaptive_weights', {})
                
                if track_res.get('completed_count', 0) > 0:
                    st.markdown(f"""
                    <div style="font-size:13px; line-height:1.7;">
                        • <b>누적 추천 표본수</b>: {track_res['total_recs']}개 (완료 {track_res['completed_count']}건 · 진행 {track_res['ongoing_count']}건)<br>
                        • <b>실제 적중 승률</b>: <b style="color:#00e676;">{track_res['win_rate']}%</b> ({track_res['wins']}승 {track_res['completed_count']-track_res['wins']}패)<br>
                        • <b>평균 실현 손익률</b>: <b style="color:{'#00e676' if track_res['avg_return']>=0 else '#f04452'};">{track_res['avg_return']:+}%</b>
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.markdown(f"""
                    <div style="font-size:12.5px; line-height:1.7; color:#d1d5db;">
                        • <b>현재 상태</b>: <b>{track_res.get('ongoing_count', 0)}개 종목 실시간 성과 추적 중</b> (익절/손절 도달 시 자동 집계)
                    </div>
                    """, unsafe_allow_html=True)

                penalized = adaptive.get('penalized_factors', [])
                boosted = adaptive.get('boosted_factors', [])
                cooldowns = adaptive.get('cooldown_tickers', {})
                failures = adaptive.get('failure_notes', [])

                st.markdown("<div style='font-size:12px; font-weight:700; color:#ffffff; margin-top:10px; border-top:1px dashed #282a36; padding-top:8px;'>⚡ 통계 기반 동적 가중치 보정 (Feedback)</div>", unsafe_allow_html=True)
                
                if penalized:
                    for pf in penalized:
                        st.markdown(f"<div style='font-size:12px; color:#ff5252;'>• ⚠️ <b>{pf['tag']}</b>: 최근 승률 {pf['win_rate']}% 저조 ➡️ <b style='color:#ff5252;'>{pf['adj']}</b></div>", unsafe_allow_html=True)
                
                if boosted:
                    for bf in boosted:
                        st.markdown(f"<div style='font-size:12px; color:#00e676;'>• 🎯 <b>{bf['tag']}</b>: 최근 승률 {bf['win_rate']}% 우수 ➡️ <b style='color:#00e676;'>{bf['adj']}</b></div>", unsafe_allow_html=True)

                if cooldowns:
                    cd_names = [f"{v['name']}({k})" for k, v in cooldowns.items()]
                    st.markdown(f"<div style='font-size:12px; color:#ffa726;'>• 🧊 <b>최근 손절 쿨다운</b>: {', '.join(cd_names)} (추천 감점 -12점)</div>", unsafe_allow_html=True)

                if failures:
                    st.markdown("<div style='font-size:12px; font-weight:700; color:#ffffff; margin-top:8px;'>📝 최근 실패 원인 진단 (성과 감사 로그)</div>", unsafe_allow_html=True)
                    for fn in failures[-3:]:
                        st.markdown(f"""
                        <div style="background:#111218; border-radius:6px; padding:6px 10px; margin-top:4px; font-size:11.5px; color:#b0b4c3;">
                            <b style="color:#ffffff;">{fn['name']} ({fn['ticker']})</b> · {fn['date']} <br>
                            원인: <span style="color:#ff8a80;">{fn['reason']}</span>
                        </div>
                        """, unsafe_allow_html=True)

                st.markdown("<div style='font-size:11px; color:#707488; margin-top:6px;'>※ 매일 장 마감 후 추천 종목의 실제 주가 궤적을 추적하여 손익비와 성공/실패 원인을 엄밀하게 재산출합니다.</div>", unsafe_allow_html=True)

            except Exception as e:
                st.caption(f"성과 추적 데이터 분석 중: {e}")

    else:
        # 좌측(상세 분석 및 가이드 1.9) vs 우측(추천 종목 리스트 1.1)
        rec_col_main, rec_col_side = st.columns([1.9, 1.1], gap="medium")

        # [좌측] 선택된 추천 종목의 상세 설명 & 계산 가이드
        with rec_col_main:
            active_rec = next((r for r in recs if r['ticker'] == st.session_state.rec_selected_ticker), recs[0])
            fib_data = active_rec.get('fibonacci', {})
            tl_data = active_rec.get('trendline', {})
            bt_data = active_rec.get('backtest_stats', {'sample_count': 0, 'win_rate': None, 'avg_return': 0.0})
            
            has_analyst = active_rec.get('has_analyst_target', False)
            wall_st_target = active_rec.get('target_price')
            wall_st_upside = active_rec.get('upside_pct')
            analyst_count = active_rec.get('analyst_count', 0)
            
            if has_analyst and wall_st_target and wall_st_upside is not None:
                analyst_label = f"월가 컨센서스 목표 <b>${wall_st_target:.2f}</b> (<span style='color:#ffd700;'>+{wall_st_upside:.1f}%</span>, {analyst_count}명 분석)"
            else:
                analyst_label = "월가 컨센서스: <i>집계 표본 부족 (미제공)</i>"

            t1_pct = active_rec.get('target_1_pct', round(((active_rec['bull_target_1']/active_rec['current_price'])-1)*100, 1))
            rr_ratio = active_rec.get('risk_reward_ratio', 1.5)

            # 상단 요약 패널
            st.markdown(f"""
            <div class="toss-panel">
                <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                    <div>
                        <span style="font-size:12px; color:#9094a6; font-weight:600;">{active_rec['category']} · {active_rec.get('pattern_status', '타점 관찰')}</span>
                        <div style="font-size:24px; font-weight:800; color:#ffffff; margin-top:2px;">
                            {active_rec['name']} ({active_rec['ticker']})
                        </div>
                        <div style="font-size:13.5px; color:#9094a6; margin-top:4px;">
                            현재가 <b>${active_rec['current_price']:.2f}</b> · 단기 1차 목표 <b>${active_rec['bull_target_1']:.2f}</b> (<span style="color:#00e676; font-weight:700;">+{t1_pct:.1f}%</span>)<br>
                            <span style="font-size:12px; color:#7b7f94;">{analyst_label}</span>
                        </div>
                    </div>
                    <div style="text-align:right;">
                        <div style="font-size:11.5px; color:#9094a6;">단기 1차 기대수익</div>
                        <div style="font-size:26px; font-weight:800; color:#00e676;">+{t1_pct:.1f}%</div>
                        <div style="font-size:12px; color:#ffffff; font-weight:700; margin-top:2px;">퀀트 기술 점수 {active_rec['total_score']}점</div>
                        <div style="font-size:11.5px; color:#ffd700; font-weight:600; margin-top:2px;">손익비 <b>{rr_ratio}:1</b></div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            # 실전 매매 손익 계산 (100만원 투자 고정)
            st.markdown("<h4 style='font-weight:800; margin-top:16px; margin-bottom:4px;'>실전 매매 손익 및 리스크 계산 (100만원 투자 기준)</h4>", unsafe_allow_html=True)
            st.caption(f"100만원 진입 시 예상 목표 수익과 최대 허용 손실을 산출합니다. (실시간 환율 $1 = {current_usd_krw:,.1f}원 적용)")

            curr_p = active_rec['current_price']
            t1 = active_rec['bull_target_1']
            t2 = active_rec['bull_target_2']
            sl = active_rec['stop_loss']
            rate = current_usd_krw

            inv = 1000000  # 100만원 투자금 고정
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
            <div class="toss-panel" style="padding:16px; margin-bottom:14px;">
                <div style="font-size:13.5px; color:#cfcfd4; line-height:1.7; margin-bottom:14px; border-bottom:1px solid #23252e; padding-bottom:10px;">
                    <b>100만원 투자 시 위험 대비 보상 요약:</b><br>
                    지금 <b>${curr_p:.2f} (약 {int(curr_p*rate):,}원)</b>에 100만원 진입하여 1차 목표 <b>${t1:.2f}</b> 도달 시 <b style="color:#00e676;">+{int(gain_1):,}원 (+{pct_1:.1f}%)</b>의 이익을 기대할 수 있습니다.<br>
                    반면, 기술적 손절선 <b>${sl:.2f}</b> 이탈 시 <b style="color:#f04452;">{int(loss_sl):,}원 ({pct_sl:.1f}%)</b>에서 손실을 철저히 방어합니다. (<b>손익비 {rr_ratio}:1</b>)
                </div>
                <div style="display:grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap:10px;">
                    <div style="background:#1f212c; border: 1px solid #3b82f6; border-radius:10px; padding:12px; text-align:center;">
                        <div style="font-size:11.5px; color:#8b95a1; font-weight:700;">1단계: 들어가기 (진입)</div>
                        <div style="font-size:18px; font-weight:800; color:#ffffff; margin-top:4px;">${curr_p:.2f}</div>
                        <div style="font-size:11.5px; color:#7b7f94; margin-top:2px;">1주당 {int(curr_p*rate):,}원</div>
                    </div>
                    <div style="background:rgba(0,230,118,0.06); border: 1px solid #00e676; border-radius:10px; padding:12px; text-align:center;">
                        <div style="font-size:11.5px; color:#00e676; font-weight:700;">2단계: 1차 목표 (익절)</div>
                        <div style="font-size:18px; font-weight:800; color:#00e676; margin-top:4px;">${t1:.2f}</div>
                        <div style="font-size:13px; font-weight:800; color:#ffffff; margin-top:2px;">+{int(gain_1):,}원 이익</div>
                        <div style="font-size:11px; color:#00e676;">+{pct_1:.1f}% (1주당 +{int(share_gain_1):,}원)</div>
                    </div>
                    <div style="background:rgba(240,68,82,0.08); border: 1.5px solid #f04452; border-radius:10px; padding:12px; text-align:center;">
                        <div style="font-size:11.5px; color:#f04452; font-weight:700;">3단계: 위험 손절선 (방어)</div>
                        <div style="font-size:18px; font-weight:800; color:#f04452; margin-top:4px;">${sl:.2f}</div>
                        <div style="font-size:13px; font-weight:800; color:#ffffff; margin-top:2px;">{int(loss_sl):,}원 손실</div>
                        <div style="font-size:11px; color:#f04452;">{pct_sl:.1f}% (1주당 {int(share_loss):,}원)</div>
                    </div>
                    <div style="background:rgba(255,215,0,0.06); border: 1px solid #ffd700; border-radius:10px; padding:12px; text-align:center;">
                        <div style="font-size:11.5px; color:#ffd700; font-weight:700;">4단계: 손익비 (Reward/Risk)</div>
                        <div style="font-size:18px; font-weight:800; color:#ffd700; margin-top:4px;">{rr_ratio} : 1</div>
                        <div style="font-size:12px; font-weight:700; color:#ffffff; margin-top:2px;">위험 대비 {rr_ratio}배 기대</div>
                        <div style="font-size:11px; color:#9ba0b4;">(2차 목표 ${t2:.2f})</div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            # 다각도 추천 근거 리포트 (카테고리 1:1 동적 매핑으로 순서 버그 박멸)
            st.markdown("<h4 style='font-weight:800; margin-top:14px; margin-bottom:10px;'>추천 근거 리포트</h4>", unsafe_allow_html=True)
            
            for reason_item in active_rec.get('core_reasons', []):
                if isinstance(reason_item, dict):
                    cat_tag = reason_item.get('category', '퀀트 종합 분석')
                    clean_text = reason_item.get('text', '')
                else:
                    cat_tag = "퀀트 분석"
                    clean_text = str(reason_item)
                clean_text = clean_text.replace('**', '').replace('***', '').replace('*', '')
                st.markdown(f"""
                <div class="reason-box-clean">
                    <div style="font-size:12px; font-weight:700; color:#8b95a1; margin-bottom:4px;">{cat_tag}</div>
                    <div>{clean_text}</div>
                </div>
                """, unsafe_allow_html=True)

        # [우측] 추천 종목 카드 리스트 (일체형 클릭 카드)
        with rec_col_side:
            st.markdown("<div style='font-size:16px; font-weight:800; color:#ffffff; margin-bottom:12px;'>오늘의 유망주 리스트</div>", unsafe_allow_html=True)
            
            for idx, r in enumerate(recs, 1):
                t = r['ticker']
                is_active = (t == st.session_state.rec_selected_ticker)
                
                p_status = r.get('pattern_status', '타점 형성 중')
                if p_status == "진입 적기":
                    status_tag = "● 진입 적기"
                    status_color = "#00e676"
                elif p_status == "타점 임박":
                    status_tag = "▲ 돌파 임박"
                    status_color = "#ffd700"
                elif "쿨다운" in p_status:
                    status_tag = "🧊 쿨다운"
                    status_color = "#f04452"
                else:
                    status_tag = "관심 추적"
                    status_color = "#8b90a4"

                tags_html = "".join([f"<span class='toss-tag-clean'>#{tag}</span>" for tag in r['tags']])
                t1_p = r.get('target_1_pct', round(((r['bull_target_1']/r['current_price'])-1)*100, 1))
                rr = r.get('risk_reward_ratio', 1.5)
                has_analyst_r = r.get('has_analyst_target', False)
                wall_st_note = f"월가 +{r['upside_pct']}%" if (has_analyst_r and r.get('upside_pct') is not None) else "월가 미집계"
                
                # 버튼과 하단 근거들을 하나의 완벽한 카드로 통합
                with st.container(key=f"card_{t}"):
                    st.markdown(f"""
                    <div class="stock-unified-card {'active' if is_active else ''}">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <div style="font-size:15px; font-weight:800; color:#ffffff;">
                                #{idx} {r['name']} <span style="font-size:13px; color:#8b90a4; font-weight:600;">({t})</span>
                            </div>
                            <span style="color:{status_color}; font-size:12px; font-weight:700;">{status_tag}</span>
                        </div>
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-top:8px;">
                            <span style="font-size:12.5px; color:#cfcfd4;">현재가 <b>${r['current_price']:.2f}</b></span>
                            <span style="font-size:12.5px; font-weight:700; color:#00e676;">1차목표 <b>+{t1_p:.1f}%</b></span>
                        </div>
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-top:4px; font-size:11px; color:#8b90a4;">
                            <span>손익비 <b>{rr}:1</b></span>
                            <span>{wall_st_note}</span>
                        </div>
                        <div style="margin-top:8px; display:flex; flex-wrap:wrap; gap:4px;">
                            {tags_html}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    if st.button(f"sel_{t}", key=f"btn_stock_{t}", use_container_width=True):
                        st.session_state.rec_selected_ticker = t
                        st.rerun()

            # 📊 통계 기반 성과 추적 & 규칙 기반 가중치 보정 로그
            st.markdown("<hr style='border:0; border-top:1px solid #23252e; margin: 12px 0 10px 0;'>", unsafe_allow_html=True)
            with st.expander("📊 통계 기반 성과 추적 & 규칙 기반 가중치 보정 로그"):
                try:
                    from quant_core.tracker import evaluate_and_learn_from_history
                    track_res = evaluate_and_learn_from_history()
                    adaptive = track_res.get('adaptive_weights', {})
                    
                    if track_res.get('completed_count', 0) > 0:
                        st.markdown(f"""
                        <div style="font-size:13px; line-height:1.7;">
                            • <b>누적 추천 표본수</b>: {track_res['total_recs']}개 (완료 {track_res['completed_count']}건 · 진행 {track_res['ongoing_count']}건)<br>
                            • <b>실제 적중 승률</b>: <b style="color:#00e676;">{track_res['win_rate']}%</b> ({track_res['wins']}승 {track_res['completed_count']-track_res['wins']}패)<br>
                            • <b>평균 실현 손익률</b>: <b style="color:{'#00e676' if track_res['avg_return']>=0 else '#f04452'};">{track_res['avg_return']:+}%</b>
                        </div>
                        """, unsafe_allow_html=True)
                    else:
                        st.markdown(f"""
                        <div style="font-size:12.5px; line-height:1.7; color:#d1d5db;">
                            • <b>현재 상태</b>: <b>{track_res.get('ongoing_count', 0)}개 종목 실시간 성과 추적 중</b> (익절/손절 도달 시 자동 집계)
                        </div>
                        """, unsafe_allow_html=True)

                    penalized = adaptive.get('penalized_factors', [])
                    boosted = adaptive.get('boosted_factors', [])
                    cooldowns = adaptive.get('cooldown_tickers', {})
                    failures = adaptive.get('failure_notes', [])

                    st.markdown("<div style='font-size:12px; font-weight:700; color:#ffffff; margin-top:10px; border-top:1px dashed #282a36; padding-top:8px;'>⚡ 통계 기반 동적 가중치 보정 (Feedback)</div>", unsafe_allow_html=True)
                    
                    if penalized:
                        for pf in penalized:
                            st.markdown(f"<div style='font-size:12px; color:#ff5252;'>• ⚠️ <b>{pf['tag']}</b>: 최근 승률 {pf['win_rate']}% 저조 ➡️ <b style='color:#ff5252;'>{pf['adj']}</b></div>", unsafe_allow_html=True)
                    
                    if boosted:
                        for bf in boosted:
                            st.markdown(f"<div style='font-size:12px; color:#00e676;'>• 🎯 <b>{bf['tag']}</b>: 최근 승률 {bf['win_rate']}% 우수 ➡️ <b style='color:#00e676;'>{bf['adj']}</b></div>", unsafe_allow_html=True)

                    if cooldowns:
                        cd_names = [f"{v['name']}({k})" for k, v in cooldowns.items()]
                        st.markdown(f"<div style='font-size:12px; color:#ffa726;'>• 🧊 <b>최근 손절 쿨다운</b>: {', '.join(cd_names)} (추천 감점 -12점)</div>", unsafe_allow_html=True)

                    if failures:
                        st.markdown("<div style='font-size:12px; font-weight:700; color:#ffffff; margin-top:8px;'>📝 최근 실패 원인 진단 (성과 감사 로그)</div>", unsafe_allow_html=True)
                        for fn in failures[-3:]:
                            st.markdown(f"""
                            <div style="background:#111218; border-radius:6px; padding:6px 10px; margin-top:4px; font-size:11.5px; color:#b0b4c3;">
                                <b style="color:#ffffff;">{fn['name']} ({fn['ticker']})</b> · {fn['date']} <br>
                                원인: <span style="color:#ff8a80;">{fn['reason']}</span>
                            </div>
                            """, unsafe_allow_html=True)

                    st.markdown("<div style='font-size:11px; color:#707488; margin-top:6px;'>※ 매일 장 마감 후 추천 종목의 실제 주가 궤적을 추적하여 손익비와 성공/실패 원인을 엄밀하게 재산출합니다.</div>", unsafe_allow_html=True)

                except Exception as e:
                    st.caption(f"성과 추적 데이터 분석 중: {e}")




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

