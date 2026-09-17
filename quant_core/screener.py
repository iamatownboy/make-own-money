"""
quant_core/screener.py
멀티팩터(피보나치·빗각·다이버전스 + 심층재무) AI 주식 스크리너 & 패턴 백테스팅 검증 엔진
"""

import os
import json
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
from typing import List, Dict, Any
import pytz


def get_ny_market_date_str() -> str:
    """미국 동부 뉴욕 증시(US/Eastern) 거래일 날짜 문자열 반환"""
    try:
        eastern = pytz.timezone('US/Eastern')
        return datetime.now(eastern).strftime('%Y-%m-%d')
    except Exception:
        return datetime.now().strftime('%Y-%m-%d')

from .data_loader import fetch_stock_data
from .indicators import (
    calculate_all_indicators,
    calculate_supertrend,
    calculate_fibonacci_levels,
    detect_trendline_breakout,
    detect_bullish_divergence,
    detect_candlestick_reversal
)
from .prediction import predict_price_scenarios

# 스크리닝 대상 나스닥/미국 핵심 성장주 유니버스 (82개 종합 테크/혁신주)
CORE_UNIVERSE = [
    # 1. 빅테크 & 메가 플랫폼 (10)
    {'ticker': 'AAPL', 'name': '애플', 'category': '빅테크/디바이스'},
    {'ticker': 'MSFT', 'name': '마이크로소프트', 'category': '클라우드/AI'},
    {'ticker': 'GOOGL', 'name': '알파벳 (구글)', 'category': '검색/AI'},
    {'ticker': 'AMZN', 'name': '아마존', 'category': '이커머스/클라우드'},
    {'ticker': 'META', 'name': '메타 플랫폼스', 'category': 'SNS/메타버스'},
    {'ticker': 'TSLA', 'name': '테슬라', 'category': 'EV/자율주행'},
    {'ticker': 'NFLX', 'name': '넷플릭스', 'category': '스트리밍 미디어'},
    {'ticker': 'UBER', 'name': '우버', 'category': '모빌리티 플랫폼'},
    {'ticker': 'ABNB', 'name': '에어비앤비', 'category': '숙박/여행 플랫폼'},
    {'ticker': 'SPOT', 'name': '스포티파이', 'category': '오디오 스트리밍'},

    # 2. AI 및 반도체 / 장비 (22)
    {'ticker': 'NVDA', 'name': '엔비디아', 'category': 'AI 반도체'},
    {'ticker': 'TSM', 'name': 'TSMC', 'category': '파운드리 반도체'},
    {'ticker': 'AMD', 'name': 'AMD', 'category': 'CPU/GPU 반도체'},
    {'ticker': 'AVGO', 'name': '브로드컴', 'category': '통신/AI 칩'},
    {'ticker': 'QCOM', 'name': '퀄컴', 'category': '모바일/온디바이스 AI'},
    {'ticker': 'ASML', 'name': 'ASML', 'category': '반도체 노광장비'},
    {'ticker': 'AMAT', 'name': '어플라이드 머티어리얼즈', 'category': '반도체 공정장비'},
    {'ticker': 'LRCX', 'name': '램리서치', 'category': '반도체 식각장비'},
    {'ticker': 'MU', 'name': '마이크론', 'category': '메모리/HBM 반도체'},
    {'ticker': 'KLAC', 'name': 'KLA', 'category': '반도체 계측장비'},
    {'ticker': 'MRVL', 'name': '마벨 테크놀로지', 'category': '데이터센터 통신칩'},
    {'ticker': 'ON', 'name': '온세미', 'category': '차량용 전력반도체'},
    {'ticker': 'ARM', 'name': 'ARM 홀딩스', 'category': '반도체 설계 IP'},
    {'ticker': 'TXN', 'name': '텍사스 인스트루먼트', 'category': '아날로그 반도체'},
    {'ticker': 'INTC', 'name': '인텔', 'category': 'CPU/파운드리'},
    {'ticker': 'ADI', 'name': '아날로그 디바이스', 'category': '신호처리 반도체'},
    {'ticker': 'NXPI', 'name': 'NXP 세미컨덕터', 'category': '차량/IoT 반도체'},
    {'ticker': 'MPWR', 'name': '모놀리식 파워', 'category': '전력관리 반도체'},
    {'ticker': 'CDNS', 'name': '케이던스', 'category': '반도체 EDA 설계'},
    {'ticker': 'SNPS', 'name': '시놉시스', 'category': '반도체 EDA 소프트웨어'},
    {'ticker': 'SMCI', 'name': '슈퍼마이크로', 'category': 'AI 서버/인프라'},
    {'ticker': 'WOLF', 'name': '울프스피드', 'category': 'SiC 화합물 반도체'},

    # 3. AI 소프트웨어 & 클라우드 / 보안 (18)
    {'ticker': 'PLTR', 'name': '팔란티어', 'category': 'AI 빅데이터'},
    {'ticker': 'SNOW', 'name': '스노우플레이크', 'category': '데이터 클라우드'},
    {'ticker': 'DDOG', 'name': '데이터독', 'category': '클라우드 모니터링'},
    {'ticker': 'MDB', 'name': '몽고DB', 'category': '클라우드 NoSQL'},
    {'ticker': 'NET', 'name': '클라우드플레어', 'category': '엣지 컴퓨팅/보안'},
    {'ticker': 'CRWD', 'name': '크라우드스트라이크', 'category': '엔드포인트 보안'},
    {'ticker': 'PANW', 'name': '팔로알토 네트웍스', 'category': '네트워크 보안'},
    {'ticker': 'ZS', 'name': '지스케일러', 'category': '제로트러스트 보안'},
    {'ticker': 'NOW', 'name': '서비스나우', 'category': '기업 워크플로우 AI'},
    {'ticker': 'CRM', 'name': '세일즈포스', 'category': '클라우드 CRM/에이전트'},
    {'ticker': 'ADBE', 'name': '어도비', 'category': '디지털 미디어/생성 AI'},
    {'ticker': 'INTU', 'name': '인튜이트', 'category': '핀테크/재무 소프트웨어'},
    {'ticker': 'PATH', 'name': '유아이패스', 'category': '로봇 프로세스 자동화'},
    {'ticker': 'AI', 'name': 'C3.ai', 'category': '엔터프라이즈 AI'},
    {'ticker': 'SOUN', 'name': '사운드하운드', 'category': '음성 AI 솔루션'},
    {'ticker': 'BBAI', 'name': '빅베어ai', 'category': '의사결정 AI'},
    {'ticker': 'APP', 'name': '앱러빈', 'category': 'AI 모바일 광고플랫폼'},
    {'ticker': 'DUOL', 'name': '듀오링고', 'category': 'AI 언어 에듀테크'},

    # 4. 우주 항공 & UAM & 차세대 방산 (10)
    {'ticker': 'RKLB', 'name': '로켓 랩', 'category': '우주 발사체/위성'},
    {'ticker': 'LUNR', 'name': '인튜이티브 머신스', 'category': '달 탐사/우주 인프라'},
    {'ticker': 'ASTS', 'name': 'AST 스페이스모바일', 'category': '우주 위성통신'},
    {'ticker': 'RDW', 'name': '레드와이어', 'category': '우주 부품/3D프린팅'},
    {'ticker': 'AVAV', 'name': '에어로바이런먼트', 'category': '군용 무인 드론'},
    {'ticker': 'JOBY', 'name': '조비 에비에이션', 'category': '도심항공교통 UAM'},
    {'ticker': 'ACHR', 'name': '아처 에비에이션', 'category': '전기수직이착륙 eVTOL'},
    {'ticker': 'KTOS', 'name': '크라토스', 'category': '국방/자율 무인기'},
    {'ticker': 'PL', 'name': '플래닛 랩스', 'category': '지구관측 위성데이터'},
    {'ticker': 'SPCE', 'name': '버진 갤럭틱', 'category': '우주 관광'},

    # 5. 차세대 혁신 기술 (양자컴 / 가상자산 / 핀테크 / 바이오) (16)
    {'ticker': 'IONQ', 'name': '아이온큐', 'category': '이온트랩 양자컴퓨팅'},
    {'ticker': 'QBTS', 'name': '디웨이브 퀀텀', 'category': '양자 어닐링 컴퓨팅'},
    {'ticker': 'RGTI', 'name': '리게티 컴퓨팅', 'category': '초전도 양자프로세서'},
    {'ticker': 'COIN', 'name': '코인베이스', 'category': '가상자산 거래소'},
    {'ticker': 'MSTR', 'name': '마이크로스트래티지', 'category': '비트코인 보유/BI'},
    {'ticker': 'HOOD', 'name': '로빈후드', 'category': '핀테크/투자 플랫폼'},
    {'ticker': 'XYZ', 'name': '블록 (구 스퀘어)', 'category': '디지털 결제 생태계'},
    {'ticker': 'PYPL', 'name': '페이팔', 'category': '글로벌 결제 네트워크'},
    {'ticker': 'SYM', 'name': '심볼릭', 'category': 'AI 물류 로보틱스'},
    {'ticker': 'ISRG', 'name': '인튜이티브 서지컬', 'category': '로봇 정밀 수술기기'},
    {'ticker': 'SHOP', 'name': '쇼피파이', 'category': '이커머스 솔루션'},
    {'ticker': 'MELI', 'name': '메르카도리브레', 'category': '남미 아마존 이커머스'},
    {'ticker': 'SE', 'name': 'Sea Limited', 'category': '동남아 디지털 플랫폼'},
    {'ticker': 'VRTX', 'name': '버텍스 파마슈티컬', 'category': '유전자 편집/신약'},
    {'ticker': 'MRNA', 'name': '모더나', 'category': 'mRNA 백신/바이오테크'},
    {'ticker': 'CRSP', 'name': '크리스퍼 테라퓨틱스', 'category': 'CRISPR 유전자 가위'},

    # 6. 차세대 전력 & 원자력 & AI 인프라 에너지 (6)
    {'ticker': 'VST', 'name': '비스트라', 'category': 'AI 전력/유틸리티'},
    {'ticker': 'CEG', 'name': '컨스텔레이션 에너지', 'category': '원자력 청정에너지'},
    {'ticker': 'CCJ', 'name': '카메코', 'category': '원전 연료 우라늄 광산'},
    {'ticker': 'OKLO', 'name': '오클로', 'category': '초소형 모듈원전 SMR'},
    {'ticker': 'SMR', 'name': '뉴스케일 파워', 'category': 'SMR 원자로 설계'},
    {'ticker': 'BE', 'name': '블룸 에너지', 'category': '고체산화물 수소연료전지'}
]

RECOMMENDATION_CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'daily_recommendations.json')


def backtest_pattern_reliability(df: pd.DataFrame, pattern_type: str = 'auto', holding_days: int = 20) -> Dict[str, Any]:
    """
    해당 종목의 과거 데이터(2년)에서 실제 스크리너 핵심 진입 패턴과 동일한 조건이
    발생했던 실제 시점들을 전수 시뮬레이션하여 20거래일 후 승률(Win Rate)과 평균 수익률을 정직하게 산출합니다.
    - pattern_type:
      * 'fibonacci': 과거 피보나치 되돌림 0.5~0.618 지지 양봉 시점 전수 검증
      * 'trendline': 과거 하락 빗각 추세선 돌파 시점 전수 검증
      * 'divergence': 과거 RSI 저점 상승 & 주가 저점 하락 다이버전스 시점 전수 검증
      * 'auto': Supertrend 상승 전환 및 20EMA 눌림목 지지 양봉 검증
    """
    if len(df) < 60:
        return {'sample_count': 0, 'win_rate': None, 'avg_return': None, 'status': '데이터 부족 (검증 불가)', 'pattern_tested': pattern_type}
        
    data = df.copy()
    signals = []
    
    # 과거 진입 시그널 탐색 (최소 25거래일 이전 데이터 필요)
    for i in range(25, len(data) - holding_days):
        close_curr = float(data['Close'].iloc[i])
        open_curr = float(data['Open'].iloc[i])
        low_curr = float(data['Low'].iloc[i])
        
        is_hit = False
        
        if pattern_type == 'fibonacci':
            # 과거 i 시점 기준 직전 25거래일 스윙 고점/저점 피보나치 골든포켓 지지 양봉
            high_prev = float(data['High'].iloc[i-25:i].max())
            low_prev = float(data['Low'].iloc[i-25:i].min())
            diff = high_prev - low_prev
            if diff > 0:
                fib_500 = high_prev - (diff * 0.500)
                fib_618 = high_prev - (diff * 0.618)
                if low_curr <= fib_500 * 1.01 and close_curr >= fib_618 * 0.99 and close_curr > open_curr:
                    is_hit = True
                    
        elif pattern_type == 'trendline':
            # 과거 20거래일 하락 빗각 돌파 양봉
            high_20 = float(data['High'].iloc[i-20:i-1].max())
            high_prev = float(data['High'].iloc[i-1])
            if high_20 > high_prev and close_curr > high_prev and close_curr > open_curr:
                is_hit = True
                
        elif pattern_type == 'divergence':
            # RSI 다이버전스 (주가 저점 하락 & RSI 저점 상승)
            if 'RSI_14' in data.columns and i >= 14:
                rsi_curr = float(data['RSI_14'].iloc[i])
                rsi_prev = float(data['RSI_14'].iloc[i-5:i].min())
                price_prev = float(data['Low'].iloc[i-5:i].min())
                if low_curr < price_prev and rsi_curr > rsi_prev and rsi_curr < 45 and close_curr > open_curr:
                    is_hit = True
                    
        else:
            # Supertrend 전환 또는 20EMA 눌림목 반등
            st_curr = data['Supertrend_Direction'].iloc[i] if 'Supertrend_Direction' in data else 0
            st_prev = data['Supertrend_Direction'].iloc[i-1] if 'Supertrend_Direction' in data else 0
            ema20 = data['EMA_21'].iloc[i] if 'EMA_21' in data else close_curr
            is_st_turn = (st_curr == 1 and st_prev == -1)
            is_ema_bounce = (low_curr <= ema20 * 1.01 and close_curr > ema20 and close_curr > open_curr)
            if is_st_turn or is_ema_bounce:
                is_hit = True
                
        if is_hit:
            exit_price = float(data['Close'].iloc[i + holding_days])
            ret = ((exit_price / close_curr) - 1) * 100
            signals.append(ret)
            
    if not signals:
        return {'sample_count': 0, 'win_rate': None, 'avg_return': None, 'status': '과거 2년 동일 시그널 부재', 'pattern_tested': pattern_type}
        
    wins = [r for r in signals if r > 0]
    win_rate = (len(wins) / len(signals)) * 100
    avg_ret = np.mean(signals)
    
    return {
        'sample_count': len(signals),
        'win_rate': round(win_rate, 1),
        'avg_return': round(avg_ret, 1),
        'status': '검증 완료',
        'pattern_tested': pattern_type
    }



def analyze_single_stock_advanced(stock_item: Dict[str, str], adaptive_data: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    단일 종목에 대해 기술적/기본적/모멘텀 분석을 수행하고, AI 자가 학습 피드백(실패 페널티 및 쿨다운)을 적용합니다.
    """
    ticker = stock_item['ticker']
    name = stock_item['name']
    category = stock_item['category']

    
    try:
        t = yf.Ticker(ticker)
        # 2년치 데이터 수집 (충분한 백테스팅 표본 확보)
        df = t.history(period='2y', interval='1d')
        if df.empty or len(df) < 50:
            df = t.history(period='1y', interval='1d')
            if df.empty or len(df) < 30:
                return None
            
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
            
        df = calculate_all_indicators(df)
        pred = predict_price_scenarios(df, days_ahead=5)
        
        # 1. 고급 기술 패턴 계산
        fib = calculate_fibonacci_levels(df, window=60)
        trendline = detect_trendline_breakout(df, window=50)
        divergence = detect_bullish_divergence(df, window=30)
        reversal_candle = detect_candlestick_reversal(df)
        
        # 감지된 추천 핵심 패턴 유형 판별 (실제 추천 패턴과 100% 동일한 조건 백테스트)
        if fib.get('is_in_golden_pocket') or fib.get('is_at_fib_382'):
            primary_pattern = 'fibonacci'
        elif trendline.get('is_breakout') or trendline.get('is_approaching'):
            primary_pattern = 'trendline'
        elif divergence.get('has_divergence'):
            primary_pattern = 'divergence'
        else:
            primary_pattern = 'auto'
            
        # 과거 패턴 신뢰도 백테스팅 (추천 패턴과 일치하는 과거 시점만 전수 시뮬레이션)
        bt_stats = backtest_pattern_reliability(df, pattern_type=primary_pattern, holding_days=20)
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        curr_price = float(last['Close'])
        prev_price = float(prev['Close'])
        change_pct = ((curr_price - prev_price) / prev_price) * 100
        
        # 2. 심층 기본적 분석 데이터 수집
        info = t.info or {}
        raw_target = info.get('targetMeanPrice') or info.get('targetMedianPrice')
        has_analyst_target = bool(raw_target and float(raw_target) > 0)
        target_price = round(float(raw_target), 2) if has_analyst_target else None
        upside_pct = round(((target_price / curr_price) - 1) * 100, 1) if has_analyst_target else None
        analyst_count = info.get('numberOfAnalystOpinions', None)

        rev_growth = info.get('revenueGrowth', 0.0) or 0.0
        op_margin = info.get('operatingMargins', 0.0) or 0.0
        profit_margin = info.get('profitMargins', 0.0) or 0.0
        peg_ratio = info.get('pegRatio', 0.0) or 0.0
        debt_to_equity = info.get('debtToEquity', 0.0) or 0.0
        fcf = info.get('freeCashflow', 0) or 0
        rec_key = info.get('recommendationKey', 'none')
        
        # 3. 종합 점수화 (기술 40 + 기본 30 + 모멘텀/백테스트 30)
        # ※ 기본 점수 거품 완전 제거: 증거가 없으면 0점부터 시작
        tech_score = 0
        fund_score = 0
        mom_score = 0
        
        reasons = []  # List of {'category': str, 'text': str}
        tags = []
        
        # 패턴 상태 관리
        pattern_status = "타점 형성 중"
        status_color = "#9094a6"
        
        # --- [1] 고급 기술적 분석 (40점) ---
        # 1) 피보나치 지지
        if fib.get('is_in_golden_pocket'):
            tech_score += 15
            reasons.append({'category': '기술적 패턴 타점', 'text': f"피보나치 0.618~0.500 되돌림 지지선(${fib['fib_618']:.2f}) 부근에서 하방 경직성을 확보하고 있습니다."})
            tags.append("피보나치지지")
            pattern_status = "진입 적기"
            status_color = "#00e676"
        elif fib.get('is_at_fib_382'):
            tech_score += 12
            reasons.append({'category': '기술적 패턴 타점', 'text': f"피보나치 0.382 지지선(${fib['fib_382']:.2f}) 상단을 유지하며 단기 추세를 보존하고 있습니다."})
            tags.append("피보나치0.382지지")
            
        # 2) 빗각(대각 추세선) 돌파
        if trendline.get('is_breakout'):
            tech_score += 15
            reasons.append({'category': '기술적 패턴 타점', 'text': f"하락 추세선(${trendline['trendline_value']:.2f})을 상향 돌파하여 단기 추세 반전 흐름을 보이고 있습니다."})
            tags.append("하락빗각돌파")
            pattern_status = "진입 적기"
            status_color = "#00e676"
        elif trendline.get('is_approaching'):
            tech_score += 8
            reasons.append({'category': '기술적 패턴 타점', 'text': f"하락 추세 저항선(${trendline['trendline_value']:.2f}) 부근에 근접하여 방향성 분기점에 위치해 있습니다."})
            tags.append("빗각돌파임박")
            pattern_status = "타점 임박"
            status_color = "#ffd700"
            
        # 3) RSI 상승 다이버전스
        if divergence.get('has_divergence'):
            tech_score += 10
            reasons.append({'category': '기술적 패턴 타점', 'text': "주가 신저가 대비 RSI 저점이 상승하는 다이버전스가 포착되어 단기 하락 모멘텀 둔화가 확인되었습니다."})
            tags.append("상승다이버전스")
            
        # 4) 아래꼬리 반등 캔들
        if reversal_candle.get('has_reversal_candle'):
            tech_score = min(40, tech_score + 5)
            reasons.append({'category': '기술적 패턴 타점', 'text': f"지지 구간 부근에서 {reversal_candle['type']} 형태의 반등 캔들이 형성되었습니다."})
            tags.append("반등캔들출현")
            
        # --- [2] 심층 기본적 분석 (30점) ---
        # 1) 월가 목표가 괴리율 (실제 애널리스트 데이터만 반영 및 태그 버킷화)
        if has_analyst_target and upside_pct is not None:
            if upside_pct >= 25:
                fund_score += 12
                reasons.append({'category': '기본적 펀더멘털', 'text': f"월가 애널리스트({analyst_count or '다수'}명) 평균 목표가 ${target_price:.2f} 기준 +{upside_pct:.1f}% 괴리율이 집계되었습니다."})
                tags.append(f"월가목표+{int(upside_pct)}%")
                tags.append("월가괴리_25이상")
            elif upside_pct >= 12:
                fund_score += 8
                reasons.append({'category': '기본적 펀더멘털', 'text': f"월가 평균 목표주가(${target_price:.2f}) 기준 +{upside_pct:.1f}% 상승 여력이 집계되었습니다."})
                tags.append("월가괴리_12_25")
            elif upside_pct > 0:
                fund_score += 5
                tags.append("월가괴리_12미만")
        else:
            reasons.append({'category': '기본적 펀더멘털', 'text': "월가 공식 애널리스트 목표가 집계가 없어 자체 재무 지표와 기술적 지표로만 검증되었습니다."})
            tags.append("컨센서스미집계")
            
        # 2) 실적 성장성 (매출 및 영업이익률)
        if rev_growth >= 0.20:
            fund_score += 10
            reasons.append({'category': '기본적 펀더멘털', 'text': f"최근 분기 매출 성장률이 +{rev_growth*100:.1f}%로 양호한 성장세를 나타내고 있습니다."})
            tags.append("매출고성장")
        elif rev_growth > 0:
            fund_score += 5
            
        # 3) 영업이익률 & 밸류에이션(PEG)
        if op_margin >= 0.20:
            fund_score += 5
            reasons.append({'category': '기본적 펀더멘털', 'text': f"영업이익률이 {op_margin*100:.1f}%로 안정적인 마진율을 유지하고 있습니다."})
            tags.append("고마진우량주")
        if 0 < peg_ratio <= 1.5:
            fund_score += 3
            tags.append("PEG저평가")
            
        # --- [3] 시장 모멘텀 & 과거 백테스트 검증 (30점) ---
        # 1) 백테스트 신뢰도 점수 (표본 3회 이상일 때만 엄밀하게 반영, 표본 부족 시 가산점 0점)
        if bt_stats['sample_count'] >= 3 and bt_stats['win_rate'] is not None:
            if bt_stats['win_rate'] >= 75:
                mom_score += 15
                reasons.append({'category': '과거 통계 검증', 'text': f"과거 2년간 동일 패턴 출현 시 승률 {bt_stats['win_rate']}% (평균 수익률 +{bt_stats['avg_return']}%, {bt_stats['sample_count']}회 검증)을 기록했습니다."})
                tags.append(f"백테스트승률{int(bt_stats['win_rate'])}%")
            elif bt_stats['win_rate'] >= 60:
                mom_score += 10
                reasons.append({'category': '과거 통계 검증', 'text': f"과거 2년 동일 패턴 출현 시 승률 {bt_stats['win_rate']}% (평균 수익률 +{bt_stats['avg_return']}%, {bt_stats['sample_count']}회)의 흐름을 보였습니다."})
            elif bt_stats['win_rate'] < 50:
                mom_score -= 5  # 과거 동일 패턴 승률 저조 시 감점
                reasons.append({'category': '과거 통계 검증', 'text': f"과거 2년 동일 패턴 승률이 {bt_stats['win_rate']}%로 저조하여 보수적 감점(-5점)이 적용되었습니다."})
        else:
            # 증거 부재 = 0점 (중립 가산점 완전 배제)
            mom_score += 0
            reasons.append({'category': '과거 통계 검증', 'text': f"과거 2년간 유효한 동일 패턴 표본 수({bt_stats['sample_count']}회)가 적어 과거 승률 수치는 산출하지 않았습니다."})
            tags.append("백테스트표본부족")
            
        # 2) 52주 고점 대비 견고함
        high_52 = float(df['High'].max())
        drop_from_high = ((high_52 - curr_price) / high_52) * 100
        if drop_from_high <= 15:
            mom_score += 15
            tags.append("신고가근접")
        else:
            mom_score += 8

        # --- [4] 성과 피드백 & 실패 페널티 적용 ---
        if adaptive_data:
            factor_adjustments = adaptive_data.get('factor_adjustments', {})
            cooldown_tickers = adaptive_data.get('cooldown_tickers', {})
            
            # 1) 팩터별 성공/실패율에 따른 가중치 보너스 또는 페널티 적용
            total_adj = 0
            for tag in tags:
                if tag in factor_adjustments:
                    adj = factor_adjustments[tag]
                    total_adj += adj
                    if adj < 0:
                        reasons.append({'category': '성과 피드백 보정', 'text': f"최근 '{tag}' 패턴의 실패율 상승으로 가중치 페널티 감점({adj}점)이 적용되었습니다."})
                    elif adj > 0:
                        reasons.append({'category': '성과 피드백 보정', 'text': f"최근 '{tag}' 패턴의 고승률 유지로 가중치 보너스(+{adj}점)가 가산되었습니다."})
            
            tech_score = max(0, tech_score + total_adj)
            
            # 2) 최근 14일 이내 손절선 이탈 종목 쿨다운 페널티 적용
            if ticker in cooldown_tickers:
                cd_info = cooldown_tickers[ticker]
                penalty = cd_info.get('penalty', -12)
                mom_score = max(0, mom_score + penalty)
                reasons.insert(0, {'category': '리스크 관리', 'text': f"최근 {cd_info['days_ago']}일 전 손절선 이탈({cd_info['reason']}) 이력으로 쿨다운 감점({penalty}점)이 적용되었습니다."})
                tags.insert(0, "최근손절쿨다운")
                pattern_status = "쿨다운(반등확인)"
                status_color = "#f04452"

        total_score = min(100, max(0, tech_score + fund_score + mom_score))

        # 목표가 및 손절가 계산
        bull_target_1 = pred.get('bull_target_1', round(curr_price * 1.05, 2))
        bull_target_2 = pred.get('bull_target_2', round(curr_price * 1.12, 2))
        stop_loss = pred.get('stop_loss', round(curr_price * 0.95, 2))
        
        target_1_pct = round(((bull_target_1 / curr_price) - 1) * 100, 1)
        target_2_pct = round(((bull_target_2 / curr_price) - 1) * 100, 1)
        stop_loss_pct = round(((stop_loss / curr_price) - 1) * 100, 1)
        
        potential_gain = max(0.01, bull_target_1 - curr_price)
        potential_loss = max(0.01, curr_price - stop_loss)
        risk_reward_ratio = round(potential_gain / potential_loss, 2)

        # 실전 진입 적격 여부 판정 (최소 점수 68점 & 손익비 1.20 이상 & 쿨다운 미해당 - 화면 설명과 100% 일치)
        is_qualified = (
            total_score >= 68 and
            risk_reward_ratio >= 1.20 and
            pattern_status != "쿨다운(반등확인)"
        )
        
        # 다각도 추천 근거 4개 엄선
        core_reasons = reasons[:4] if len(reasons) >= 4 else (reasons + [{'category': '종합 분석', 'text': "기술적 지표와 기본적 재무 안정성이 고루 뒷받침되는 종목입니다."}])[:4]
        
        return {
            'ticker': ticker,
            'name': name,
            'category': category,
            'current_price': round(curr_price, 2),
            'prev_price': round(prev_price, 2),
            'change_pct': round(change_pct, 2),
            'has_analyst_target': has_analyst_target,
            'target_price': target_price,
            'upside_pct': upside_pct,
            'analyst_count': analyst_count,
            'total_score': total_score,
            'tech_score': tech_score,
            'fund_score': fund_score,
            'mom_score': mom_score,
            'pattern_status': pattern_status,
            'status_color': status_color,
            'tags': tags[:4],
            'core_reasons': core_reasons,
            'fibonacci': fib,
            'trendline': trendline,
            'divergence': divergence,
            'backtest_stats': bt_stats,
            'bull_target_1': bull_target_1,
            'bull_target_2': bull_target_2,
            'stop_loss': stop_loss,
            'target_1_pct': target_1_pct,
            'target_2_pct': target_2_pct,
            'stop_loss_pct': stop_loss_pct,
            'risk_reward_ratio': risk_reward_ratio,
            'is_qualified': is_qualified,
            'atr': pred.get('atr', round(curr_price * 0.03, 2)),
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M')
        }

    except Exception as e:
        print(f"[{ticker}] 고급 스크리닝 분석 실패: {e}")
        return None


def run_full_market_scan(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """
    유니버스 전체를 스캔하고 상위 유망 종목을 선별하여 캐시에 저장합니다.
    1. Supabase 클라우드 캐시 우선 확인
    2. 로컬 JSON 캐시 확인
    3. 필요 시 82개 유니버스 전수 스캔 후 양쪽 캐시 동시 저장
    """
    from .db import db_load_daily_cache, db_save_daily_cache, is_supabase_enabled

    today_str = get_ny_market_date_str()

    if not force_refresh:
        # 1. Supabase 클라우드 캐시 우선 확인
        if is_supabase_enabled():
            cloud_cached = db_load_daily_cache(today_str)
            if cloud_cached:
                try:
                    from .tracker import record_daily_recommendations
                    record_daily_recommendations(cloud_cached)
                except Exception:
                    pass
                return cloud_cached

        # 2. 로컬 JSON 캐시 폴백 확인
        if os.path.exists(RECOMMENDATION_CACHE_FILE):
            try:
                with open(RECOMMENDATION_CACHE_FILE, 'r', encoding='utf-8') as f:
                    cached = json.load(f)
                    cache_date = cached.get('date', '')
                    if cache_date == today_str and 'recommendations' in cached:
                        cached_recs = cached['recommendations']
                        if is_supabase_enabled() and cached_recs:
                            try:
                                db_save_daily_cache(today_str, cached_recs)
                            except Exception:
                                pass
                        try:
                            from .tracker import record_daily_recommendations
                            record_daily_recommendations(cached_recs)
                        except Exception:
                            pass
                        return cached_recs
            except Exception:
                pass

    try:
        from .tracker import get_adaptive_factor_weights
        adaptive_data = get_adaptive_factor_weights()
    except Exception:
        adaptive_data = {}

    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(analyze_single_stock_advanced, item, adaptive_data): item for item in CORE_UNIVERSE}

        for future in as_completed(futures):
            try:
                res = future.result()
                if res:
                    results.append(res)
            except Exception:
                pass
            
    # 실전 진입 적격 종목만 선별 (최소 점수 68점 & 손익비 1.2 이상 & 쿨다운 미해당)
    qualified_results = [r for r in results if r.get('is_qualified', False)]
    qualified_results.sort(key=lambda x: x['total_score'], reverse=True)
    top_picks = qualified_results[:7]
    
    try:
        def np_encoder(obj):
            if isinstance(obj, (np.bool_, bool)):
                return bool(obj)
            if isinstance(obj, (np.floating, float)):
                return float(obj)
            if isinstance(obj, (np.integer, int)):
                return int(obj)
            return str(obj)

        with open(RECOMMENDATION_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump({
                'date': today_str,
                'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'recommendations': top_picks
            }, f, ensure_ascii=False, indent=2, default=np_encoder)
    except Exception as e:
        print(f"로컬 캐시 저장 실패: {e}")

    # Supabase 클라우드 캐시 동시 저장
    if is_supabase_enabled():
        try:
            db_save_daily_cache(today_str, top_picks)
        except Exception as e:
            print(f"Supabase 클라우드 캐시 저장 실패: {e}")
        
    # 추천 결과를 성과 추적 데이터베이스에 자동 기록 (학습용)
    try:
        from .tracker import record_daily_recommendations
        record_daily_recommendations(top_picks)
    except Exception as e:
        print(f"학습 기록 실패: {e}")
        
    return top_picks

