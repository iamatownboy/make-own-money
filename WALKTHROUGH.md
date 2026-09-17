# 🚀 퀀트 시스템 엄밀화 & Supabase 무손실 운영 데이터 보호 완결 보고서

> **작업 브랜치**: `feat/strategy-versioning-quant-rigor`
> **상태**: 사용자 추가 지적 사항 2건 완벽 해결, 단위 테스트 22종 100% 통과, `main` 직접 커밋 금지 원칙 엄격 준수

---

## 💡 사용자 핵심 질문 답변: "DB를 새로 만들어서 연결해야 하나요?"

> **결론: 절대로 새로 만드실 필요가 없습니다!**
>
> 기존 DB에 쌓여 있는 소중한 추천 기록과 성과 데이터를 삭제하거나 DB를 새로 파지 마세요.
> 아래 안내해 드리는 **무손실 복구 및 마이그레이션 SQL**을 Supabase SQL Editor에서 1번만 실행하시면:
> 1. 과거 데이터(`2026-09-17` 이전)는 모두 정직하게 `legacy` 전략으로 안전 복구 및 분류됩니다.
> 2. 기존의 수익률, 익절/손절 판정 결과 등 모든 데이터가 100% 그대로 보존됩니다.
> 3. 신규 추천부터만 `v1.0.0` 전략으로 깔끔하게 적재 및 통계 분리됩니다.

---

## 📌 추가 보완 사항 해결 내역

### 1. 🏷️ [P1] 과거 기록의 `legacy` 무손실 보존 & 일회성 복구 SQL 확립
- **문제점**:
  - 만약 직전 버전의 스키마(`DEFAULT 'v1.0.0'`)를 이미 운영 DB에 실행했었다면, 과거 행들이 이미 `v1.0.0`으로 오염되어 단순 `NULL` 체크만으로는 복구되지 않는 문제.
- **해결 조치 (`supabase_schema.sql`)**:
  - 전략 버전 도입 공식 날짜(`2026-09-17`)를 기준으로 삼아, 과거 날짜의 모든 행을 `legacy`로 일괄 복구하도록 SQL을 고도화했습니다:
  ```sql
  -- 2단계: 과거 추천 행을 명시적으로 'legacy' 버전으로 완벽 복구/백필 (통계 왜곡 원천 차단)
  -- (NULL/빈값뿐만 아니라, 직전 스키마 적용으로 인해 v1.0.0 공식 도입일(2026-09-17) 이전에 v1.0.0으로 잘못 채워진 과거 행도 일괄 복구)
  UPDATE recommendation_history
  SET strategy_version = 'legacy'
  WHERE strategy_version IS NULL
     OR strategy_version = ''
     OR (strategy_version = 'v1.0.0' AND date < '2026-09-17');

  -- 3단계: 기존 행의 recommendation_id 고유 식별자 백필 및 동기화
  UPDATE recommendation_history
  SET recommendation_id = date || '_' || ticker || '_' || strategy_version
  WHERE recommendation_id IS NULL
     OR recommendation_id = ''
     OR recommendation_id != (date || '_' || ticker || '_' || strategy_version);
  ```

---

### 2. 🧪 [P1] `can_write = can_read` 추측 배제 & 실제 Write Probe Test 실증
- **문제점**:
  - `anon`이 아닌 다른 키(예: `authenticated` 등)가 주어졌을 때, 읽기가 된다고 해서 쓰기 권한을 `can_write = can_read`로 추측하면, RLS 정책에 의해 쓰기가 거부될 때 런타임 저장 실패가 발생함.
- **해결 조치 (`quant_core/db.py`)**:
  - **추측 코드 완전 제거**: 단순 role 검사나 읽기 성공 여부에 의존하지 않고, **실제 쓰기 검증(Write Probe Test)**을 직접 실행.
  - `daily_recommendation_cache` 테이블에 격리된 더미 핑 행(`date: 1970-01-01`)을 `upsert`한 뒤 성공 시 즉시 `delete`하여 DB를 청결하게 유지.
  - RLS 차단(403) 등으로 쓰기가 실패하면 즉시 `can_write = False` 처리.
  - **정직하고 명확한 UI 배지 연동 (`app.py`)**:
    - `🟢 Supabase 연동됨 (읽기·쓰기 실증 완료)`
    - `🟠 Supabase 쓰기 권한 부족 (service_role 키 필요, 읽기만 가능)`
    - `🔴 Supabase 접근 불가 (RLS 차단)`

---

## 🧪 자동화 단위 테스트 결과 (22종 100% 통과)

```bash
python3 -m pytest tests/ -v
```
```text
tests/test_quant_engine.py::test_backtest_pattern_insufficient_samples PASSED      [  4%]
tests/test_quant_engine.py::test_dynamic_risk_reward_calculation PASSED           [  9%]
tests/test_quant_engine.py::test_normalize_factor_tag_buckets PASSED              [ 13%]
tests/test_quant_engine.py::test_outcome_target_hit_first PASSED                  [ 18%]
tests/test_quant_engine.py::test_outcome_stop_hit_first PASSED                    [ 22%]
tests/test_quant_engine.py::test_outcome_simultaneous_bar_conservative_loss PASSED [ 27%]
tests/test_quant_engine.py::test_outcome_20_day_expiration PASSED                 [ 31%]
tests/test_quant_engine.py::test_supabase_fallback_and_deduplication PASSED      [ 36%]
tests/test_quant_engine.py::test_cooldown_and_adaptive_weights PASSED             [ 40%]
tests/test_quant_engine.py::test_strategy_version_and_metadata PASSED             [ 45%]
tests/test_quant_engine.py::test_expectancy_and_profit_factor_calculation PASSED  [ 50%]
tests/test_quant_engine.py::test_strategy_version_isolation PASSED                [ 54%]
tests/test_quant_engine.py::test_tag_deduplication_in_single_recommendation PASSED [ 59%]
tests/test_quant_engine.py::test_multi_strategy_same_date_ticker_storage PASSED   [ 63%]
tests/test_quant_engine.py::test_evaluate_pattern_match_unified_logic PASSED      [ 68%]
tests/test_quant_engine.py::test_zero_loss_profit_factor_infinity_display PASSED  [ 72%]
tests/test_quant_engine.py::test_dynamic_fee_slippage_calculation PASSED          [ 77%]
tests/test_quant_engine.py::test_jwt_role_inspection PASSED                       [ 81%]
tests/test_quant_engine.py::test_supabase_diagnostics_anon_key_blocks_enabled PASSED [ 86%]
tests/test_quant_engine.py::test_db_upsert_strict_compound_key_no_silent_overwrite PASSED [ 90%]
tests/test_quant_engine.py::test_supabase_schema_migration_backfill_order PASSED  [ 95%]
tests/test_quant_engine.py::test_write_probe_authenticated_key_rejection PASSED   [100%]

============================== 22 passed in 1.33s ==============================
```

- **구문 컴파일 검증**: `python3 -m py_compile app.py quant_core/*.py tests/*.py` ➡️ **오류 0건**
- **Git diff 공백 검증**: `git diff --check` ➡️ **오류 0건**
- **운용 원칙 준수**: `main` 브랜치는 변경되지 않았으며, 작업 브랜치 `feat/strategy-versioning-quant-rigor`에서만 안전하게 작업 및 검증 완료.
