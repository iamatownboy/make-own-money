# 🚀 퀀트 시스템 엄밀화 & 5대 핵심 결함 해결 완결 보고서

> **작업 브랜치**: `feat/strategy-versioning-quant-rigor`  
> **상태**: 5대 핵심 결함(P1 4건, P2 1건) 전원 해결, 단위 테스트 17종 100% 통과, `main` 직접 커밋 금지 원칙 준수

---

## 📌 해결된 5대 핵심 결함 및 개선 내역

### 1. 🏷️ [P1] 전략 버전별 통계 및 팩터 가중치 완전 분리
- **문제점**: 추천 시 `strategy_version`이 저장되더라도, 성과 계산 및 팩터 가중치 보정 시 전체 과거 이력이 한꺼번에 집계되어 구버전(`legacy`)과 신규 동결 전략(`v1.0.0`)의 통계가 뒤섞이는 현상.
- **해결 조치**:
  - `load_history()`: `strategy_version`이 없는 과거 기록을 명시적으로 `'legacy'`로 자동 마이그레이션.
  - `get_adaptive_factor_weights(strategy_version)`: 지정된 버전의 데이터만 독립 필터링하여 승률 및 가중치 보정값을 산출. (단, 최근 14일 손절 쿨다운은 포트폴리오 리스크 방어를 위해 전체 이력 대상 적용)
  - `evaluate_and_learn_from_history(strategy_version)`: 시세 실시간 추적 업데이트와 성과 통계 집계를 명확히 분리하고, 지정된 버전만 선별 집계.
  - `app.py`: 성과 추적 대시보드에 **전략 버전 선택 셀렉트박스**(`v1.0.0 (동결 전략)`, `전체 통합 (All)`, `legacy (구버전)`)를 도입하여 각 전략의 순수 성과를 투명하게 조회 가능하도록 구현.

---

### 2. 🗄️ [P1] 다중 전략 버전 추천 중복 저장 및 DB 제약조건 전환
- **문제점**: DB 제약조건이 `UNIQUE (date, ticker)`로 묶여 있어, 동일 날짜·종목에 대해 `v1.0.0`과 향후 `v1.1.0`을 동시에 추천 이력으로 남길 경우 충돌 또는 덮어쓰기 발생.
- **해결 조치**:
  - 고유 키를 `date + ticker + strategy_version` 복합 키 및 `recommendation_id`로 전면 확장.
  - `supabase_schema.sql`: `CONSTRAINT unique_date_ticker_version UNIQUE (date, ticker, strategy_version)` 제약조건 및 안전 마이그레이션 SQL 적용.
  - `quant_core/db.py`: `db_upsert_history_items`에서 신규 복합 키 기준 upsert를 1차 시도하고, 구버전 DB 스키마일 경우 `date, ticker`로 부드럽게 폴백(Fallback)하도록 3단계 방어 로직 구현.

---

### 3. 🏷️ [P1] 1종목 내 다중 정규화 동의어 태그 중복 카운팅 원천 차단
- **문제점**: 스크리너에서 1개 종목에 `월가목표+32%`와 `월가괴리_25이상`을 동시 부여할 경우, 정규화 후 둘 다 `월가괴리_25이상`으로 매핑되어 1개 추천 종목에 대해 표본 수가 2회 중복 카운트되는 오류 발생.
- **해결 조치**:
  - `get_adaptive_factor_weights()` 및 `evaluate_and_learn_from_history()` 모두에서 태그 정규화 후 **`set` 집합 연산**을 적용.
  - 1개 종목에서는 아무리 많은 동의어 태그가 있더라도 정규화 버킷당 **정확히 1건**으로만 집계되도록 보장.

---

### 4. 🔬 [P2] 실시간 스크리닝과 백테스팅 판정 함수 100% 공통화 (Single Source of Truth)
- **문제점**: 실시간 스크리너는 피보나치(60일 윈도우), 빗각 추세선(50일 윈도우)을 사용하는 반면, 과거 백테스터는 25일 스윙 약식 수식을 사용하여 신호 생성 로직이 불일치.
- **해결 조치**:
  - `evaluate_pattern_match(df_slice, pattern_type)` 단일 공통 함수 구현 (`quant_core/screener.py`).
  - 피보나치 되돌림, 하락 추세선 돌파, RSI 상승 다이버전스, 슈퍼트렌드/EMA 지지 판정을 1개 함수로 일원화.
  - `analyze_single_stock_advanced`와 `backtest_pattern_reliability`가 100% 동일한 `evaluate_pattern_match`를 호출하도록 리팩토링하여 로직 불일치 및 룩어헤드 편향을 완전 제거.

---

### 5. 🔒 [P1] Supabase RLS 비공개 보안 정책 강화 (Default Deny)
- **문제점**: `supabase_schema.sql`에서 `anon`에게 SELECT 권한을 허용하여, 외부 클라이언트가 익명 키로 개인 추천 이력을 조회할 수 있었음.
- **해결 조치**:
  - `anon` 역할의 SELECT 정책을 완전 폐기.
  - `authenticated` 및 `service_role`만 SELECT 가능하도록 격상 (`Private Read History`, `Private Read Cache`).
  - 외부 미인증 클라이언트의 접근을 원천 차단(Default Deny)하여 개인 전용 트레이딩 WTS의 데이터 기밀성 확보.

---

### 6. ✨ 추가 퀀트 엄밀성 개선 내역
1. **실제 나스닥(QQQ) 거시 시장 레짐 연동**:
   - `get_market_context_regime()`을 구현하여 QQQ의 20MA/50MA 및 20일 수익률 기반 레짐(`강세 상승장`, `약세 조정장`, `박스권 횡보장`)을 실시간 산출하고 추천 종목 메타데이터에 기록.
2. **무손실(Loss 0건) 시 Profit Factor 무한대 안전 처리**:
   - 손실이 0건일 때 `profit_factor = 99.9`, `profit_factor_display = "손실 없음 (∞)"`으로 깔끔하게 렌더링.
3. **레코드별 동적 거래비용 차감**:
   - 레코드마다 `fee_slippage_pct`를 개별 적용하여 기대값(EV) 및 실현 순손익률 계산.
4. **Git 트레일링 공백(Trailing Whitespace) 정리**:
   - `git diff --check`로 감지된 5건의 후행 공백 및 EOF 빈 줄을 완벽 정리 (오류 0건).

---

## 🧪 자동화 검증 결과 (100% 통과)

### 1. Pytest 단위 테스트 (17개 전원 통과)
```bash
python3 -m pytest tests/ -v
```
```text
tests/test_quant_engine.py::test_backtest_pattern_insufficient_samples PASSED      [  5%]
tests/test_quant_engine.py::test_dynamic_risk_reward_calculation PASSED           [ 11%]
tests/test_quant_engine.py::test_normalize_factor_tag_buckets PASSED              [ 17%]
tests/test_quant_engine.py::test_outcome_target_hit_first PASSED                  [ 23%]
tests/test_quant_engine.py::test_outcome_stop_hit_first PASSED                    [ 29%]
tests/test_quant_engine.py::test_outcome_simultaneous_bar_conservative_loss PASSED  [ 35%]
tests/test_quant_engine.py::test_outcome_20_day_expiration PASSED                 [ 41%]
tests/test_quant_engine.py::test_supabase_fallback_and_deduplication PASSED      [ 47%]
tests/test_quant_engine.py::test_cooldown_and_adaptive_weights PASSED             [ 52%]
tests/test_quant_engine.py::test_strategy_version_and_metadata PASSED             [ 58%]
tests/test_quant_engine.py::test_expectancy_and_profit_factor_calculation PASSED  [ 64%]
tests/test_quant_engine.py::test_strategy_version_isolation PASSED                [ 70%]
tests/test_quant_engine.py::test_tag_deduplication_in_single_recommendation PASSED [ 76%]
tests/test_quant_engine.py::test_multi_strategy_same_date_ticker_storage PASSED   [ 82%]
tests/test_quant_engine.py::test_evaluate_pattern_match_unified_logic PASSED      [ 88%]
tests/test_quant_engine.py::test_zero_loss_profit_factor_infinity_display PASSED  [ 94%]
tests/test_quant_engine.py::test_dynamic_fee_slippage_calculation PASSED          [100%]

============================== 17 passed in 0.42s ==============================
```

### 2. 구문 컴파일 검증
```bash
python3 -m py_compile app.py quant_core/*.py tests/*.py
# 결과: 에러 0건 정상 통과
```

### 3. Git Diff 공백 검증
```bash
git diff --check
# 결과: 트레일링 공백 0건 정상 통과
```
