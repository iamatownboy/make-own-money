-- ==============================================================================
-- 퀀트 대시보드 Supabase PostgreSQL 영구 데이터베이스 스키마 & RLS 비공개 보안 정책
-- ==============================================================================

-- 1. 과거 추천 종목 이력 및 실현 성과 추적 테이블
CREATE TABLE IF NOT EXISTS recommendation_history (
    id BIGSERIAL PRIMARY KEY,
    recommendation_id VARCHAR(64),
    date DATE NOT NULL,
    ticker VARCHAR(16) NOT NULL,
    name VARCHAR(128),
    category VARCHAR(64),
    rec_price NUMERIC,
    target_price NUMERIC,
    target_price_2 NUMERIC,
    stop_loss NUMERIC,
    expected_upside NUMERIC,
    quant_score INTEGER,
    tags JSONB,
    pattern_status VARCHAR(64),
    status VARCHAR(64),
    max_price NUMERIC,
    current_price NUMERIC,
    current_pnl_pct NUMERIC,
    realized_pnl_pct NUMERIC,
    realized_pnl_net_pct NUMERIC,
    hit_success BOOLEAN DEFAULT FALSE,
    is_completed BOOLEAN DEFAULT FALSE,
    failure_reason TEXT,
    exit_price NUMERIC,
    exit_date DATE,
    strategy_version VARCHAR(32) DEFAULT 'v1.0.0',
    rules JSONB,
    market_context JSONB,
    fee_slippage_pct NUMERIC DEFAULT 0.25,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT unique_date_ticker_version UNIQUE (date, ticker, strategy_version)
);

-- 기존 테이블이 있을 경우 신규 컬럼 안전 추가 및 기존 행 legacy 백필 (마이그레이션)
-- 1단계: 기본값 없이 컬럼 추가 (기존 행이 v1.0.0으로 자동 오분류되는 현상 원천 차단)
ALTER TABLE recommendation_history ADD COLUMN IF NOT EXISTS recommendation_id VARCHAR(64);
ALTER TABLE recommendation_history ADD COLUMN IF NOT EXISTS strategy_version VARCHAR(32);
ALTER TABLE recommendation_history ADD COLUMN IF NOT EXISTS rules JSONB;
ALTER TABLE recommendation_history ADD COLUMN IF NOT EXISTS market_context JSONB;
ALTER TABLE recommendation_history ADD COLUMN IF NOT EXISTS fee_slippage_pct NUMERIC DEFAULT 0.25;
ALTER TABLE recommendation_history ADD COLUMN IF NOT EXISTS realized_pnl_net_pct NUMERIC;

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

-- 4단계: 앞으로 들어오는 신규 추천에만 기본값 'v1.0.0' 및 NOT NULL 설정
ALTER TABLE recommendation_history ALTER COLUMN strategy_version SET DEFAULT 'v1.0.0';
ALTER TABLE recommendation_history ALTER COLUMN strategy_version SET NOT NULL;

-- 5단계: 기존 (date, ticker) 제약조건 제거 후 (date, ticker, strategy_version) 복합 UNIQUE 적용
ALTER TABLE recommendation_history DROP CONSTRAINT IF EXISTS unique_date_ticker;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'unique_date_ticker_version'
    ) THEN
        ALTER TABLE recommendation_history ADD CONSTRAINT unique_date_ticker_version UNIQUE (date, ticker, strategy_version);
    END IF;
END $$;

-- 2. 당일 추천 종목 스캔 캐시 테이블
CREATE TABLE IF NOT EXISTS daily_recommendation_cache (
    date DATE PRIMARY KEY,
    recommendations JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ==============================================================================
-- 🔒 Row Level Security (RLS) 비공개 행 단위 보안 활성화
-- ==============================================================================

ALTER TABLE recommendation_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_recommendation_cache ENABLE ROW LEVEL SECURITY;

-- 기존 정책 정리
DROP POLICY IF EXISTS "Public Read History" ON recommendation_history;
DROP POLICY IF EXISTS "Private Read History" ON recommendation_history;
DROP POLICY IF EXISTS "Service Role Write History" ON recommendation_history;
DROP POLICY IF EXISTS "Public Read Cache" ON daily_recommendation_cache;
DROP POLICY IF EXISTS "Private Read Cache" ON daily_recommendation_cache;
DROP POLICY IF EXISTS "Service Role Write Cache" ON daily_recommendation_cache;

-- [보안 정책 1] 조회(SELECT)는 인증된 사용자(authenticated) 및 백엔드(service_role)만 허용
-- 익명(anon) 사용자의 외부 접근을 원천 차단하여 비공개 개인 대시보드의 데이터를 안전하게 보호
CREATE POLICY "Private Read History"
ON recommendation_history
FOR SELECT
TO authenticated, service_role
USING (true);

CREATE POLICY "Private Read Cache"
ON daily_recommendation_cache
FOR SELECT
TO authenticated, service_role
USING (true);

-- [보안 정책 2] 수정/삽입/삭제(ALL/WRITE)는 오직 백엔드 서버(service_role)만 허용
-- 외부에서 데이터를 임의 조작하거나 변조/삭제하는 것을 원천 차단
CREATE POLICY "Service Role Write History"
ON recommendation_history
FOR ALL
TO service_role
USING (true)
WITH CHECK (true);

CREATE POLICY "Service Role Write Cache"
ON daily_recommendation_cache
FOR ALL
TO service_role
USING (true)
WITH CHECK (true);
