# ⚡ Make Own Money: Toss-style Quant WTS & Screener

토스증권의 직관적인 UI/UX를 담아낸 **나스닥 성장주 전용 퀀트 WTS & 실전 매매 스크리너**입니다.  
나스닥 100, 반도체, 테크, 우주항공 등 **82개 유니버스**를 병렬 스캔하여 기술적 눌림목 지지 타점과 손익비(1.2:1 이상)를 검증합니다.

---

## 📌 핵심 기능

1. **토스증권 스타일 WTS UI**:
   - 차분하고 세련된 다크 테마 및 Pretendard 타이포그래피.
   - 상태 태그, 실시간 시세, 추천 근거가 일체화된 단일 클릭 카드 레이아웃.

2. **병렬 퀀트 스크리닝 엔진**:
   - 멀티스레드(`ThreadPoolExecutor`) 기반 82개 종목 고속 스캔.
   - 피보나치 되돌림 지지, 하락 추세선 돌파, RSI 상승 다이버전스 복합 기술 평가.
   - 월가 애널리스트 컨센서스 목표주가 및 괴리율 실시간 연동 (표본 부족 시 명확히 분리).

3. **실전 매매 손익 및 리스크 계산기 (100만원 기준)**:
   - 야후 파이낸스 실시간 달러/원 환율(USD/KRW) 자동 반영.
   - 기준 투자금 100만원 진입 시 예상 목표 수익과 최대 허용 손실을 대등하게 비교 산출.
   - 진입가, 1차 목표가, 2차 목표가, 손절가 및 손익비(Reward/Risk Ratio) 4단계 카드 제공.

4. **통계 기반 성과 추적 & 가중치 피드백 루프**:
   - 일일 추천 종목의 실제 주가 궤적을 일자별로 순차 추적하여 적중률 및 실패 원인 감사 로그 기록.
   - 최근 손절 종목 쿨다운(-12점) 및 팩터별 승률 기반 동적 가중치 보정.

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
├── daily_recommendations.json   # 스크리너 당일 분석 캐시 (로컬 폴백용)
├── recommendation_history.json # 성과 추적 데이터베이스 (로컬 폴백용)
├── quant_core/                 # 퀀트 분석 코어 패키지
│   ├── db.py                   # Supabase PostgreSQL 연동 및 로컬 JSON 하이브리드 어댑터
│   ├── screener.py             # 82개 종목 초고속 병렬 스캐너
│   ├── indicators.py           # 피보나치, RSI, EMA, 빗각 등 기술적 지표
│   ├── prediction.py           # 가격 시나리오 및 목표가 산출
│   ├── tracker.py              # 추천 성과 추적 엔진
│   ├── data_loader.py          # 실시간 시세 및 환율 수집기
│   └── backtester.py           # 백테스팅 시뮬레이터
├── supabase_schema.sql         # Supabase 테이블 및 RLS 행 보안 정책 SQL
└── .gitignore                  # Git 관리 제외 파일 목록
```

---

## 🗄️ 데이터베이스 & 보안 (Supabase RLS 연동 가이드)

Streamlit Community Cloud는 재시작 시 로컬 파일이 초기화되는 휘발성 컨테이너 환경입니다.
추천 이력과 일일 캐시를 영구 보존하기 위해 **Supabase (PostgreSQL)** 연동을 지원하며, **RLS (Row Level Security)** 정책을 통해 데이터 무단 변조를 원천 차단합니다.

> **하이브리드 무중단 폴백 구조**:
> Supabase 시크릿이 설정되지 않은 상태에서도 로컬 JSON 파일로 100% 정상 작동하며 오류가 발생하지 않습니다.

### 1. Supabase 스키마 생성 및 RLS 보안 활성화
Supabase 대시보드의 **SQL Editor**에서 [`supabase_schema.sql`](supabase_schema.sql) 파일의 내용을 실행합니다.

주요 보안 정책:
- **`recommendation_history` 및 `daily_recommendation_cache` RLS 활성화**
- **읽기(SELECT)**: `anon`, `authenticated` 모두 허용 (대시보드 차트/데이터 렌더링용)
- **쓰기/수정/삭제(ALL/WRITE)**: 오직 백엔드 서버의 `service_role` (Secret key)만 허용하여, 외부에서 공개 키(`anon`)로 데이터를 조작/삭제하는 것을 원천 차단

### 2. Streamlit Cloud 시크릿 설정
Streamlit Cloud 대시보드 (`Settings` > `Secrets`)에 다음을 추가합니다:

```toml
# 관리자 인증 (Google OAuth 또는 시크릿 비밀번호)
ADMIN_PASSWORD = "여기에_나만의_안전한_비밀번호_입력"

# Supabase 연결 설정
SUPABASE_URL = "https://your-project-id.supabase.co"
# 쓰기 권한이 필요한 경우 Supabase Settings > API의 'Secret keys' (service_role) 사용 권장
SUPABASE_KEY = "your-supabase-key"
```
