# ⚡ Make Own Money: Toss-style AI Quant WTS & Screener

토스증권의 직관적이고 감성적인 UI/UX를 담아낸 **나스닥 핵심 테크 주도주 전용 AI 퀀트 WTS & 실전 매매 스크리너**입니다.  
나스닥 100, 차세대 AI, 글로벌 반도체, 우주항공, 양자컴퓨팅 등 **82개 핵심 성장주 유니버스**를 13초 만에 병렬 스캔하여 최적의 눌림목 반등 타점을 저격합니다.

---

## 📌 핵심 기능

1. **토스증권 감성 WTS UI & 원클릭 전환**:
   - 차분하고 세련된 다크 테마 및 Pretendard 타이포그래피.
   - 뱃지, 실시간 시세, 추천 근거가 일체화된 단일 클릭 카드 레이아웃.

2. **초고속 병렬 퀀트 스크리닝 엔진**:
   - 멀티스레드(`ThreadPoolExecutor`) 기반 82개 종목을 약 13초 만에 분석.
   - 피보나치 0.618 지지, 하락 추세 빗각 돌파, RSI 상승 다이버전스, 반등 캔들 복합 평가.
   - 월가 애널리스트 컨센서스 목표주가 및 상승 여력(Upside Potential) 실시간 연동.

3. **실전 매매 손익 계산 가이드**:
   - 야후 파이낸스 실시간 달러/원 환율(USD/KRW) 100% 자동 반영.
   - 투자금(100만 / 300만 / 500만 / 1,000만 원) 토글 선택 시 매수 가능 주수 및 예상 손익 원화 즉시 계산.
   - 진입가, 1차 익절(+5~8%), 2차 대박(+15~20%), 손절가(-3~5%) 4단계 액션 카드 제공.

4. **성과 추적 & 자가 피드백 루프**:
   - 매일 추천된 종목의 진입 이후 최고 수익률 및 손절 여부 자동 추적.

---

## 🚀 빠른 시작

### 1. 필수 패키지 설치
```bash
pip install -r requirements.txt
```

### 2. 웹 대시보드 실행
```bash
streamlit run app.py
```
브라우저에서 `http://localhost:8501`로 접속하여 실시간 추천 종목 및 손익 가이드를 확인할 수 있습니다.

---

## 📂 프로젝트 구조
```text
make_own_money/
├── app.py                      # 토스 감성 Streamlit WTS 메인 애플리케이션
├── requirements.txt            # 클라우드 배포용 의존성 목록
├── daily_recommendations.json   # 스크리너 당일 분석 캐시
├── recommendation_history.json # 성과 추적 데이터베이스
├── quant_core/                 # 퀀트 분석 코어 패키지
│   ├── screener.py             # 82개 종목 초고속 병렬 스캐너
│   ├── indicators.py           # 피보나치, RSI, EMA, 빗각 등 기술적 지표
│   ├── prediction.py           # 가격 시나리오 및 목표가 산출
│   ├── tracker.py              # 추천 성과 추적 엔진
│   ├── data_loader.py          # 실시간 시세 및 환율 수집기
│   └── backtester.py           # 백테스팅 시뮬레이터
└── .gitignore                  # Git 관리 제외 파일 목록
```
