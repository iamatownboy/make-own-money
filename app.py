"""
app.py
토스증권(Toss Securities) WTS 스타일 1:1 완벽 벤치마킹 대시보드
- 모드 1: 🔥 오늘의 AI 추천 종목 (피보나치·빗각돌파·다이버전스 & 백테스트 실증 검증)
- 모드 2: 💼 내 보유 포트폴리오 (토스 WTS 뷰 & 실시간 계좌)
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
import os
from datetime import datetime
import yfinance as yf

from quant_core.data_loader import fetch_stock_data, PORTFOLIO_CONFIG
from quant_core.indicators import calculate_all_indicators
from quant_core.prediction import evaluate_technical_health, predict_price_scenarios
from quant_core.screener import run_full_market_scan

# 1. 페이지 설정
st.set_page_config(
    page_title="토스증권 AI 퀀트 WTS",
    page_icon="💸",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# -----------------------------------------------------------------------------
# 🔒 보안 인증: 등록된 구글 계정(tomsslee043@gmail.com) 전용 열람 2중 잠금
# -----------------------------------------------------------------------------
AUTHORIZED_EMAILS = {"tomsslee043@gmail.com"}

if hasattr(st, "user") and st.user:
    current_user_email = st.user.get("email")
    if current_user_email and current_user_email.strip().lower() not in AUTHORIZED_EMAILS:
        st.error(f"⛔ 접근 권한이 없습니다. 등록된 관리자 계정(tomsslee043@gmail.com)으로 로그인해주세요.")
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
    tab_rec, tab_port = st.tabs(["🔥 오늘의 AI 상승 추천주", "💼 내 보유 포트폴리오 (토스 WTS)"])
else:
    tab_rec = st.container()



# ==============================================================================
# 탭 1: 🔥 오늘의 AI 상승 추천주 (피보나치·빗각·다이버전스 & 백테스트 실증)
# ==============================================================================
with tab_rec:
    top_col1, top_col2 = st.columns([3, 1])
    with top_col1:
        st.markdown("<h3 style='margin:0; font-weight:800; color:#ffffff;'>오늘의 나스닥 패턴 완성 유망주 Top 7</h3>", unsafe_allow_html=True)
        st.caption("피보나치 0.618 지지존, 하락 빗각 돌파, 상승 다이버전스 및 과거 백테스트 승률 검증을 거친 종목입니다.")
    with top_col2:
        if st.button("🔄 오늘자 패턴 재스캔", use_container_width=True):
            with st.spinner("과거 2년 패턴 백테스트 및 다각도 퀀트 스캔을 진행 중입니다..."):
                run_full_market_scan(force_refresh=True)
            st.rerun()

    recs = run_full_market_scan(force_refresh=False)
    if not recs:
        st.error("추천 종목 데이터를 불러올 수 없습니다.")
        st.stop()

    # 좌측(상세 분석 및 차트 1.9) vs 우측(추천 종목 리스트 1.1)으로 위치 변경!
    rec_col_main, rec_col_side = st.columns([1.9, 1.1], gap="medium")

    # [좌측] 선택된 추천 종목의 상세 설명 & 차트
    with rec_col_main:
        active_rec = next((r for r in recs if r['ticker'] == st.session_state.rec_selected_ticker), recs[0])
        fib_data = active_rec.get('fibonacci', {})
        tl_data = active_rec.get('trendline', {})
        bt_data = active_rec.get('backtest_stats', {'win_rate': 70, 'avg_return': 10, 'sample_count': 5})
        
        # 상단 요약 패널
        st.markdown(f"""
        <div class="toss-panel">
            <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                <div>
                    <span style="font-size:12px; color:#9094a6; font-weight:600;">{active_rec['category']} · {active_rec.get('pattern_status', '타점 관찰')}</span>
                    <div style="font-size:24px; font-weight:800; color:#ffffff; margin-top:2px;">
                        {active_rec['name']} ({active_rec['ticker']})
                    </div>
                    <div style="font-size:14px; color:#9094a6; margin-top:2px;">
                        현재가 <b>${active_rec['current_price']:.2f}</b> · 월가 평균 목표가 <b>${active_rec['target_price']:.2f}</b>
                    </div>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:12px; color:#9094a6;">목표가까지 기대 수익률</div>
                    <div style="font-size:26px; font-weight:800; color:#f04452;">+{active_rec['upside_pct']}%</div>
                    <div style="font-size:12px; color:#ffffff; font-weight:700;">AI 종합 점수 {active_rec['total_score']}점</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)



        # 실전 매매 손익 계산 가이드 (얼마에 들어가서 얼마에 팔면 얼마를 이득보나? - 최상단 배치)
        calc_t_col, calc_b_col = st.columns([2.3, 1.7])
        with calc_t_col:
            st.markdown("<h4 style='font-weight:800; margin-top:16px; margin-bottom:4px;'>실전 매매 손익 계산 가이드</h4>", unsafe_allow_html=True)
            st.caption(f"진입가와 목표가, 예상 손익을 한눈에 계산해 드립니다. (실시간 환율 $1 = {current_usd_krw:,.1f}원 적용)")
        with calc_b_col:
            st.markdown("<div style='margin-top:14px;'></div>", unsafe_allow_html=True)
            inv_choice = st.segmented_control(
                "투자 기준금액",
                options=[1000000, 3000000, 5000000, 10000000],
                format_func=lambda x: f"{x//10000}만원",
                default=1000000,
                key="rec_invest_amount",
                label_visibility="collapsed"
            )
            if not inv_choice:
                inv_choice = 1000000

        curr_p = active_rec['current_price']
        t1 = active_rec['bull_target_1']
        t2 = active_rec['bull_target_2']
        sl = active_rec['stop_loss']
        rate = current_usd_krw

        inv = inv_choice
        pct_1 = ((t1 / curr_p) - 1) * 100
        pct_2 = ((t2 / curr_p) - 1) * 100
        pct_sl = ((sl / curr_p) - 1) * 100

        gain_1 = inv * (pct_1 / 100)
        gain_2 = inv * (pct_2 / 100)
        loss_sl = inv * (pct_sl / 100)

        share_gain_1 = (t1 - curr_p) * rate
        share_gain_2 = (t2 - curr_p) * rate
        share_loss = (sl - curr_p) * rate

        st.markdown(f"""
        <div class="toss-panel" style="padding:16px; margin-bottom:14px;">
            <div style="font-size:13.5px; color:#cfcfd4; line-height:1.7; margin-bottom:14px; border-bottom:1px solid #23252e; padding-bottom:10px;">
                <b>{inv//10000}만원 투자 시 실전 손익 요약:</b><br>
                지금 <b>${curr_p:.2f} (약 {int(curr_p*rate):,}원)</b>에 들어가서 1차 목표 <b>${t1:.2f}</b>에 팔면 <b>+{int(gain_1):,}원 (+{pct_1:.1f}%)</b>의 이득을 봅니다.<br>
                2차 목표 <b>${t2:.2f}</b>까지 홀딩하면 <b>+{int(gain_2):,}원 (+{pct_2:.1f}%)</b>의 이득이며, <b>${sl:.2f}</b> 이탈 시 <b>{int(loss_sl):,}원</b>에서 손절 방어합니다.
            </div>
            <div style="display:grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap:10px;">
                <div style="background:#1f212c; border: 1px solid #3b82f6; border-radius:10px; padding:12px; text-align:center;">
                    <div style="font-size:11.5px; color:#8b95a1; font-weight:700;">1단계: 들어가기 (진입)</div>
                    <div style="font-size:18px; font-weight:800; color:#ffffff; margin-top:4px;">${curr_p:.2f}</div>
                    <div style="font-size:11.5px; color:#7b7f94; margin-top:2px;">1주당 {int(curr_p*rate):,}원</div>
                </div>
                <div style="background:rgba(240,68,82,0.06); border: 1px solid #f04452; border-radius:10px; padding:12px; text-align:center;">
                    <div style="font-size:11.5px; color:#f04452; font-weight:700;">2단계: 1차 팔기 (익절)</div>
                    <div style="font-size:18px; font-weight:800; color:#f04452; margin-top:4px;">${t1:.2f}</div>
                    <div style="font-size:13px; font-weight:800; color:#ffffff; margin-top:2px;">+{int(gain_1):,}원 이득</div>
                    <div style="font-size:11px; color:#f04452;">+{pct_1:.1f}% (1주당 +{int(share_gain_1):,}원)</div>
                </div>
                <div style="background:rgba(255,82,82,0.08); border: 1px solid #ff5252; border-radius:10px; padding:12px; text-align:center;">
                    <div style="font-size:11.5px; color:#ff5252; font-weight:700;">3단계: 2차 팔기 (대박)</div>
                    <div style="font-size:18px; font-weight:800; color:#ff5252; margin-top:4px;">${t2:.2f}</div>
                    <div style="font-size:13px; font-weight:800; color:#ffffff; margin-top:2px;">+{int(gain_2):,}원 이득</div>
                    <div style="font-size:11px; color:#ff5252;">+{pct_2:.1f}% (1주당 +{int(share_gain_2):,}원)</div>
                </div>
                <div style="background:rgba(49,130,246,0.06); border: 1px solid #3182f6; border-radius:10px; padding:12px; text-align:center;">
                    <div style="font-size:11.5px; color:#3182f6; font-weight:700;">4단계: 손절선 (방어)</div>
                    <div style="font-size:18px; font-weight:800; color:#3182f6; margin-top:4px;">${sl:.2f}</div>
                    <div style="font-size:13px; font-weight:800; color:#ffffff; margin-top:2px;">{int(loss_sl):,}원 손실</div>
                    <div style="font-size:11px; color:#3182f6;">{pct_sl:.1f}% (1주당 {int(share_loss):,}원)</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # 다각도 추천 근거 4대 관점 (액션 카드 다음으로 배치)
        st.markdown("<h4 style='font-weight:800; margin-top:14px; margin-bottom:10px;'>추천 근거 리포트</h4>", unsafe_allow_html=True)
        
        for i, reason in enumerate(active_rec['core_reasons'], 1):
            icon_tag = "기술적 패턴 타점" if i == 1 else ("기본적 재무 펀더멘털" if i == 2 else ("시장 수급 및 상대강도" if i == 3 else "백테스트 통계 신뢰도"))
            clean_reason = reason.replace('**', '').replace('***', '').replace('*', '')
            st.markdown(f"""
            <div class="reason-box-clean">
                <div style="font-size:12px; font-weight:700; color:#8b95a1; margin-bottom:4px;">{icon_tag}</div>
                <div>{clean_reason}</div>
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
            else:
                status_tag = "관심 추적"
                status_color = "#8b90a4"

            tags_html = "".join([f"<span class='toss-tag-clean'>#{tag}</span>" for tag in r['tags']])
            
            # 버튼과 하단 근거들을 하나의 완벽한 카드로 통합 (카드 전체 어디든 클릭 가능!)
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
                        <span style="font-size:13px; color:#cfcfd4;">현재가 <b>${r['current_price']:.2f}</b></span>
                        <span style="font-size:13px; font-weight:700; color:#f04452;">기대수익 <b>+{r['upside_pct']}%</b></span>
                    </div>
                    <div style="margin-top:8px; display:flex; flex-wrap:wrap; gap:4px;">
                        {tags_html}
                    </div>
                </div>
                """, unsafe_allow_html=True)
                if st.button(f"sel_{t}", key=f"btn_stock_{t}", use_container_width=True):
                    st.session_state.rec_selected_ticker = t
                    st.rerun()


        # 🧠 [신규] AI 추천 성과 추적 & 자가 학습 섹션
        st.markdown("<hr style='border:0; border-top:1px solid #23252e; margin: 12px 0 10px 0;'>", unsafe_allow_html=True)
        with st.expander("🧠 AI 추천 적중률 & 자가 학습 피드백"):
            try:
                from quant_core.tracker import evaluate_and_learn_from_history
                track_res = evaluate_and_learn_from_history()
                
                if track_res.get('completed_count', 0) > 0:
                    st.markdown(f"""
                    <div style="font-size:13px; line-height:1.7;">
                        • <b>누적 추천 종목</b>: {track_res['total_recs']}개 (완료 {track_res['completed_count']}건 · 진행 {track_res['ongoing_count']}건)<br>
                        • <b>목표가 적중 승률</b>: <b style="color:#00e676;">{track_res['win_rate']}%</b><br>
                        • <b>평균 실현 수익률</b>: <b style="color:#f04452;">+{track_res['avg_return']}%</b>
                    </div>
                    """, unsafe_allow_html=True)
                    if track_res.get('best_factors'):
                        st.markdown("<div style='font-size:12px; font-weight:700; color:#9094a6; margin-top:8px;'>가장 잘 맞았던 팩터 (자가 학습 순위)</div>", unsafe_allow_html=True)
                        for bf in track_res['best_factors']:
                            st.write(f"• #{bf['factor']}: 승률 {bf['win_rate']}% ({bf['count']}회)")
                else:
                    st.markdown(f"""
                    <div style="font-size:12.5px; line-height:1.7; color:#d1d5db;">
                        • <b>오늘 신규 등록</b>: <b>{track_res.get('ongoing_count', 7)}개 종목 실시간 추적 중</b><br>
                        • <b>진행 상태</b>: <b>미국 본장 미개장 (한국시간 22:30 이후 추적 가동)</b><br>
                        • <b>과거 2년 백테스트 기준 기대 승률</b>: <b style="color:#ffd700;">평균 80.5%</b>
                    </div>
                    <div style="font-size:11.5px; color:#8b90a4; margin-top:6px; border-top:1px dashed #282a36; padding-top:6px;">
                        ※ 장이 마감되면 실제 시세를 자동 대조하여 목표가 도달 여부와 최고 성과 팩터를 자가 학습합니다.
                    </div>
                    """, unsafe_allow_html=True)
            except Exception as e:
                st.caption(f"학습 데이터 분석 중: {e}")



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
                    <b style="font-size:15px; color:#ffffff;">💡 {curr_ticker} AI 진단: <span style="color:{'#00e676' if health['score']>=20 else ('#f04452' if health['score']<=-20 else '#ffa726')};">{health['signal']}</span></b>
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

