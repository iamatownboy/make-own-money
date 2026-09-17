-- ==============================================================================
-- 퀀트 대시보드 Supabase PostgreSQL 영구 데이터베이스 스키마 & RLS 보안 정책
-- ==============================================================================

-- 1. 과거 추천 종목 이력 및 실현 성과 추적 테이블
CREATE TABLE IF NOT EXISTS recommendation_history (
    id BIGSERIAL PRIMARY KEY,
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
    hit_success BOOLEAN DEFAULT FALSE,
    is_completed BOOLEAN DEFAULT FALSE,
    failure_reason TEXT,
    exit_price NUMERIC,
    exit_date DATE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT unique_date_ticker UNIQUE (date, ticker)
);

-- 2. 당일 추천 종목 스캔 캐시 테이블
CREATE TABLE IF NOT EXISTS daily_recommendation_cache (
    date DATE PRIMARY KEY,
    recommendations JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ==============================================================================
-- 🔒 Row Level Security (RLS) 행 단위 보안 활성화
-- ==============================================================================

ALTER TABLE recommendation_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_recommendation_cache ENABLE ROW LEVEL SECURITY;

-- 기존 정책 초기화
DROP POLICY IF EXISTS "Public Read History" ON recommendation_history;
DROP POLICY IF EXISTS "Service Role Write History" ON recommendation_history;
DROP POLICY IF EXISTS "Public Read Cache" ON daily_recommendation_cache;
DROP POLICY IF EXISTS "Service Role Write Cache" ON daily_recommendation_cache;

-- [보안 정책 1] 조회(SELECT)는 모든 클라이언트(anon, authenticated, service_role)에게 허용
CREATE POLICY "Public Read History" 
ON recommendation_history 
FOR SELECT 
TO anon, authenticated, service_role
USING (true);

CREATE POLICY "Public Read Cache" 
ON daily_recommendation_cache 
FOR SELECT 
TO anon, authenticated, service_role
USING (true);

-- [보안 정책 2] 수정/삽입/삭제(ALL/WRITE)는 오직 백엔드 서버(service_role)만 허용
-- 외부에서 anon 키를 탈취하더라도 데이터 조작/변조/삭제 원천 차단
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
