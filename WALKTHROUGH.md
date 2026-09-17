# 🚀 퀀트 시스템 엄밀화 & Supabase 무손실 운영 데이터 보호 완결 보고서

> **작업 브랜치**: `feat/strategy-versioning-quant-rigor`  
> **상태**: 사용자 리뷰 지적 사항(P1 3건) 완벽 해결, 단위 테스트 21종 100% 통과, `main` 직접 커밋 금지 원칙 엄격 준수

---

## 📌 Supabase 운영 데이터 3대 필수 수정 사항 해결 내역

### 1. 🏷️ [P1] 기존 기록의 `legacy` 보존 및 안전 마이그레이션 순서 확립
- **문제점**:
  - `ALTER TABLE recommendation_history ADD COLUMN IF NOT EXISTS strategy_version VARCHAR(32) DEFAULT 'v1.0.0';` 구문 실행 시, 기존에 저장되어 있던 과거 추천 행들도 일괄 `v1.0.0`으로 채워져 신구 전략 통계가 섞이는 치명적 오염 발생.
- **해결 조치 (`supabase_schema.sql`)**:
  1. **1단계 (기본값 없이 추가)**: `ALTER TABLE recommendation_history ADD COLUMN IF NOT EXISTS strategy_version VARCHAR(32);`
  2. **2단계 (legacy 명시적 백필)**: `UPDATE recommendation_history SET strategy_version = 'legacy' WHERE strategy_version IS NULL OR strategy_version = '';`
  3. **3단계 (recommendation_id 백필)**: `UPDATE recommendation_history SET recommendation_id = date || '_' || ticker || '_' || strategy_version WHERE recommendation_id IS NULL;`
  4. **4단계 (신규 행 기본값 설정)**: `ALTER TABLE recommendation_history ALTER COLUMN strategy_version SET DEFAULT 'v1.0.0';` 및 `NOT NULL` 설정.
  5. **5단계 (복합 UNIQUE 적용)**: `unique_date_ticker` 제거 후 `CONSTRAINT unique_date_ticker_version UNIQUE (date, ticker, strategy_version)` 승격.

---

### 2. 🗄️ [P1] 구형 DB 제약조건으로의 조용한 덮어쓰기 폴백(Fallback) 원천 차단
- **문제점**:
  - `quant_core/db.py`에서 복합키 `(date, ticker, strategy_version)` 업서트가 실패했을 때, 구버전 단일키 `(date, ticker)`로 재시도하고 `True`를 반환하여 마이그레이션이 안 된 DB에서 서로 다른 전략 버전의 기록이 조용히 덮어써지던 결함.
- **해결 조치 (`quant_core/db.py`)**:
  - 복합키 업서트 실패 시 `(date, ticker)`로의 침묵적 폴백을 **완전히 제거**.
  - 대신 명확한 에러 로그(`"[DB 오류] DB 스키마가 전략 버전 복합키(date, ticker, strategy_version) 저장을 지원하지 않습니다. supabase_schema.sql 마이그레이션을 먼저 실행해 주세요."`)를 기록하고 **`False`를 즉시 반환**.
  - 버전 데이터가 덮어써지는 피해를 방지하고, 로컬 JSON에만 안전하게 보존되도록 방어.

---

### 3. 🔒 [P1] JWT Role 감지 & 실제 RLS 읽기/쓰기 권한 Health Check 도입
- **문제점**:
  - 단순히 Supabase 클라이언트 객체가 생성되기만 하면 `anon` 키(공개 키)로 연결되어 실제로는 RLS에 의해 SELECT/INSERT가 403으로 막혀있음에도 초록색 "🟢 Supabase DB 연동됨" 배지가 떠서 영구 보존되고 있다고 사용자가 오인함.
- **해결 조치 (`quant_core/db.py`, `app.py`)**:
  - **JWT 역할 안전 파싱 (`inspect_jwt_role`)**:
    - 키 본문 평문은 절대로 출력하지 않으며, JWT 페이로드를 디코딩하여 `"service_role"`인지 `"anon"`인지 역할을 식별.
  - **실제 권한 Health Check (`get_supabase_diagnostics`)**:
    - 실제 가벼운 `SELECT id` 쿼리를 실행하여 `can_read` 판정.
    - 키가 `service_role`인지 확인하여 `can_write` 판정.
    - `can_read`와 `can_write`가 모두 `True`일 때만 `is_healthy = True`.
  - **`is_supabase_enabled()` 격상**:
    - 단순 클라이언트 존재 여부가 아니라, `is_healthy == True`일 때만 활성화로 판정.
  - **정직하고 투명한 UI 배지 (`app.py`)**:
    - 읽기/쓰기 모두 성공 시: `🟢 Supabase 연동됨 (읽기/쓰기 완료)`
    - `anon` 키 감지 시: `🟠 Supabase 권한 부족 (service_role 키 필요)` 경고 표시
    - 테이블 조회 실패 시: `🔴 Supabase 스키마 미적용`
    - 미설정 시: `⚪ 로컬 스토리지 모드`

---

## 🧪 자동화 단위 테스트 결과 (21종 100% 통과)

```bash
python3 -m pytest tests/ -v
```
```text
tests/test_quant_engine.py::test_backtest_pattern_insufficient_samples PASSED      [  4%]
tests/test_quant_engine.py::test_dynamic_risk_reward_calculation PASSED           [  9%]
tests/test_quant_engine.py::test_normalize_factor_tag_buckets PASSED              [ 14%]
tests/test_quant_engine.py::test_outcome_target_hit_first PASSED                  [ 19%]
tests/test_quant_engine.py::test_outcome_stop_hit_first PASSED                    [ 23%]
tests/test_quant_engine.py::test_outcome_simultaneous_bar_conservative_loss PASSED [ 28%]
tests/test_quant_engine.py::test_outcome_20_day_expiration PASSED                 [ 33%]
tests/test_quant_engine.py::test_supabase_fallback_and_deduplication PASSED      [ 38%]
tests/test_quant_engine.py::test_cooldown_and_adaptive_weights PASSED             [ 42%]
tests/test_quant_engine.py::test_strategy_version_and_metadata PASSED             [ 47%]
tests/test_quant_engine.py::test_expectancy_and_profit_factor_calculation PASSED  [ 52%]
tests/test_quant_engine.py::test_strategy_version_isolation PASSED                [ 57%]
tests/test_quant_engine.py::test_tag_deduplication_in_single_recommendation PASSED [ 61%]
tests/test_quant_engine.py::test_multi_strategy_same_date_ticker_storage PASSED   [ 66%]
tests/test_quant_engine.py::test_evaluate_pattern_match_unified_logic PASSED      [ 71%]
tests/test_quant_engine.py::test_zero_loss_profit_factor_infinity_display PASSED  [ 76%]
tests/test_quant_engine.py::test_dynamic_fee_slippage_calculation PASSED          [ 80%]
tests/test_quant_engine.py::test_jwt_role_inspection PASSED                       [ 85%]
tests/test_quant_engine.py::test_supabase_diagnostics_anon_key_blocks_enabled PASSED [ 90%]
tests/test_quant_engine.py::test_db_upsert_strict_compound_key_no_silent_overwrite PASSED [ 95%]
tests/test_quant_engine.py::test_supabase_schema_migration_backfill_order PASSED  [100%]

============================== 21 passed in 1.19s ==============================
```

- **구문 컴파일 검증**: `python3 -m py_compile app.py quant_core/*.py tests/*.py` ➡️ **오류 0건**
- **Git diff 공백 검증**: `git diff --check` ➡️ **오류 0건**
- **세션 상태 크래시 수정**: `app.py` 클라우드 배포 시 `is_authenticated` 초기화 완료
