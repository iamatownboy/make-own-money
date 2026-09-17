"""
quant_core/db.py
Supabase(PostgreSQL) 클라우드 데이터베이스 연동 및 로컬 JSON 자동 폴백 어댑터
- Supabase 시크릿(SUPABASE_URL, SUPABASE_KEY) 존재 시: 클라우드 DB 연동
- 시크릿 미설정 시: 기존 로컬 JSON 파일로 안전하게 자동 폴백(Fallback)
"""

import base64
import os
import json
import uuid
from datetime import datetime
from typing import Dict, List, Any, Optional

# Supabase 클라이언트 싱글톤 캐시 및 Health Check 캐시
_supabase_client = None
_last_error = None
_health_cache = None
_health_cache_time = None


def inspect_jwt_role(key: str) -> str:
    """
    Supabase API 키의 JWT 페이로드를 안전하게 파싱하여 role('service_role', 'anon' 등)을 감지합니다.
    - 보안 원칙: 키 본문 평문은 절대로 출력하거나 노출하지 않으며, 오직 역할 이름 문자열만 반환합니다.
    """
    if not key or not isinstance(key, str):
        return "unknown"
    parts = key.strip().split(".")
    if len(parts) != 3:
        return "unknown"
    try:
        payload_b64 = parts[1]
        padding = len(payload_b64) % 4
        if padding:
            payload_b64 += "=" * (4 - padding)
        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        payload = json.loads(payload_bytes.decode("utf-8"))
        return str(payload.get("role", "unknown"))
    except Exception:
        return "unknown"


def get_supabase_diagnostics(force_refresh: bool = False) -> Dict[str, Any]:
    """
    Supabase 연결 상태 및 실제 RLS 권한(Health Check) 상세 진단 정보 반환.
    - 단순히 클라이언트 객체 생성 여부가 아니라, 실제 SELECT 및 service_role 권한 여부를 검증
    - 30초 캐시 적용으로 불필요한 네트워크 오버헤드 방지
    """
    global _supabase_client, _last_error, _health_cache, _health_cache_time
    now = datetime.now()

    if not force_refresh and _health_cache and _health_cache_time:
        if (now - _health_cache_time).total_seconds() < 30:
            return _health_cache

    url = os.environ.get("SUPABASE_URL", "") or os.environ.get("supabase_url", "")
    key = os.environ.get("SUPABASE_KEY", "") or os.environ.get("SUPABASE_ANON_KEY", "") or os.environ.get("supabase_key", "")

    try:
        import streamlit as st
        for k in st.secrets.keys():
            k_lower = k.lower().replace("-", "_")
            if k_lower in ("supabase_url", "supabase_project_url"):
                if not url:
                    url = str(st.secrets[k]).strip()
            elif k_lower in ("supabase_key", "supabase_service_role_key", "supabase_service_key", "supabase_secret_key", "supabase_anon_key", "supabase_publishable_key", "supabase_anon"):
                if not key:
                    key = str(st.secrets[k]).strip()
    except Exception as e:
        pass

    key_role = inspect_jwt_role(key)

    package_installed = False
    try:
        import supabase
        package_installed = True
    except Exception:
        package_installed = False

    diag = {
        "has_url": bool(url),
        "has_key": bool(key),
        "key_role": key_role,
        "is_service_role": (key_role == "service_role"),
        "package_installed": package_installed,
        "client_created": False,
        "can_read": False,
        "can_write": False,
        "is_healthy": False,
        "is_connected": False,
        "status_message": "⚪ 로컬 스토리지 모드 (Supabase 미설정)",
        "last_error": str(_last_error) if _last_error else None
    }

    if not url or not key or not package_installed:
        _health_cache = diag
        _health_cache_time = now
        return diag

    client = get_supabase_client()
    if not client:
        diag["status_message"] = "🔴 Supabase 클라이언트 초기화 실패"
        _health_cache = diag
        _health_cache_time = now
        return diag

    diag["client_created"] = True

    # 1. 실제 읽기 권한 점검 (경량 SELECT 1건)
    can_read = False
    try:
        resp = client.table("recommendation_history").select("id").limit(1).execute()
        can_read = True
    except Exception as re:
        _last_error = f"SELECT 권한 오류: {re}"

    # 2. 실제 쓰기 권한 실증 점검 (전용 system_health_check 테이블 전용, 비즈니스 테이블 침범 완전 배제)
    can_write = False
    is_schema_missing = False
    if key_role == "anon":
        # anon 키는 비공개 RLS 정책상 쓰기가 원천 차단되므로 시도 없이 거부
        can_write = False
        if not _last_error:
            _last_error = "RLS 정책 제한: anon 키는 쓰기 권한이 없습니다. service_role 키가 필요합니다."
    else:
        probe_id = f"probe_{uuid.uuid4().hex}"
        try:
            # [전용 헬스체크 테이블 단독 사용] 비즈니스 데이터(추천/캐시) 테이블은 절대로 건드리지 않음
            test_row = {"id": probe_id, "pinged_at": datetime.now().isoformat()}
            client.table("system_health_check").upsert(test_row).execute()
            can_write = True
            try:
                client.table("system_health_check").delete().eq("id", probe_id).execute()
            except Exception:
                pass  # 삭제 실패 시에도 전용 테이블의 핑 행일 뿐 비즈니스 데이터 영향 0%
        except Exception as te:
            can_write = False
            err_msg = str(te)
            # system_health_check 테이블이 아직 생성되지 않은 경우:
            # 비즈니스 캐시 행을 덮어쓰지 않고 명확하게 '스키마 마이그레이션 필요' 상태로 방어
            if "relation" in err_msg.lower() or "does not exist" in err_msg.lower() or "42p01" in err_msg.lower():
                is_schema_missing = True
                _last_error = "스키마 마이그레이션 필요: system_health_check 테이블이 없습니다. supabase_schema.sql을 먼저 실행해 주세요."
            else:
                _last_error = f"실제 쓰기 권한 거부 (service_role 키 필요): {te}"

    diag["can_read"] = can_read
    diag["can_write"] = can_write

    # 종합 가동 가능 상태(is_healthy) 판정: 읽기 및 쓰기 권한이 실제 테스트로 모두 입증된 경우만 True
    if can_read and can_write:
        diag["is_healthy"] = True
        diag["is_connected"] = True
        diag["status_message"] = "🟢 Supabase 연동됨 (읽기·쓰기 실증 완료)"
    elif is_schema_missing or not can_read:
        diag["is_healthy"] = False
        diag["is_connected"] = False
        diag["status_message"] = "🔴 Supabase 스키마 미적용 (supabase_schema.sql 마이그레이션 필요)"
    elif can_read and not can_write:
        diag["is_healthy"] = False
        diag["is_connected"] = False
        diag["status_message"] = "🟠 Supabase 쓰기 권한 부족 (service_role 키 필요, 읽기만 가능)"
    else:
        diag["is_healthy"] = False
        diag["is_connected"] = False
        diag["status_message"] = "🔴 Supabase 접근 불가 (RLS 차단)"

    _health_cache = diag
    _health_cache_time = now
    return diag


def get_supabase_client():
    """
    Supabase 클라이언트를 초기화하여 반환합니다.
    Streamlit secrets 또는 환경 변수에서 URL과 KEY를 유연하게 읽습니다.
    """
    global _supabase_client, _last_error

    if _supabase_client is not None:
        return _supabase_client

    url = os.environ.get("SUPABASE_URL", "") or os.environ.get("supabase_url", "")
    key = os.environ.get("SUPABASE_KEY", "") or os.environ.get("SUPABASE_ANON_KEY", "") or os.environ.get("supabase_key", "")

    try:
        import streamlit as st
        for k in st.secrets.keys():
            k_lower = k.lower().replace("-", "_")
            if k_lower in ("supabase_url", "supabase_project_url"):
                if not url:
                    url = str(st.secrets[k]).strip()
            elif k_lower in ("supabase_key", "supabase_service_role_key", "supabase_service_key", "supabase_secret_key", "supabase_anon_key", "supabase_publishable_key", "supabase_anon"):
                if not key:
                    key = str(st.secrets[k]).strip()
    except Exception as e:
        _last_error = f"Secrets 읽기 예외: {e}"

    if url and key:
        try:
            from supabase import create_client
            _supabase_client = create_client(url, key)
            _last_error = None
            return _supabase_client
        except Exception as e:
            _last_error = f"Client 생성 실패: {e}"
            print(f"[DB] Supabase 클라이언트 초기화 실패: {e}")
            return None

    return None


def is_supabase_enabled() -> bool:
    """
    Supabase DB 실질 가동 가능 여부 확인.
    - 단순히 클라이언트 객체 생성 여부가 아니라, 실제 읽기/쓰기 권한(is_healthy)을 만족해야 True.
    - 권한이 불완전할 경우 가짜 연결로 간주하지 않고 로컬 JSON 폴백으로 안전하게 동작.
    """
    client = get_supabase_client()
    if not client:
        return False
    diag = get_supabase_diagnostics()
    return diag.get("is_healthy", False)


# -----------------------------------------------------------------------------
# 1. 과거 추천 이력 (recommendation_history) DB 작업
# -----------------------------------------------------------------------------

def db_load_history() -> Optional[List[Dict[str, Any]]]:
    """Supabase에서 전체 추천 이력을 최신순으로 불러옵니다."""
    client = get_supabase_client()
    if not client:
        return None

    try:
        response = client.table("recommendation_history") \
            .select("*") \
            .order("date", desc=True) \
            .execute()

        if response and hasattr(response, "data") and response.data is not None:
            items = []
            for row in response.data:
                item = dict(row)
                item["date"] = str(item.get("date", ""))
                if "quant_score" in item and "ai_score" not in item:
                    item["ai_score"] = item["quant_score"]
                items.append(item)
            return items
    except Exception as e:
        print(f"[DB] Supabase 추천 이력 조회 실패: {e}")
        return None

    return None


def db_upsert_history_items(items: List[Dict[str, Any]]) -> bool:
    """
    Supabase에 추천 이력 리스트를 업서트(Insert or Update)합니다.
    - 고유 키: (date, ticker, strategy_version) 복합 키
    - [엄격한 무손실 원칙]:
      구형 DB 제약조건(date, ticker)으로 조용히 재시도하여 서로 다른 전략 버전의 데이터를 덮어쓰는
      침묵적 폴백을 엄격히 배제합니다.
      복합 제약조건 미적용으로 실패할 경우 명확한 에러를 반환하여 마이그레이션을 요구합니다.
    """
    client = get_supabase_client()
    if not client or not items:
        return False

    global _last_error
    try:
        records = []
        for it in items:
            strat_ver = it.get("strategy_version", "v1.0.0")
            dt_str = str(it.get("date", ""))
            tk_str = it.get("ticker", "")
            rec_id = it.get("recommendation_id") or f"{dt_str}_{tk_str}_{strat_ver}"

            rec = {
                "recommendation_id": rec_id,
                "date": dt_str,
                "ticker": tk_str,
                "name": it.get("name", ""),
                "category": it.get("category", ""),
                "rec_price": float(it.get("rec_price", 0.0)),
                "target_price": float(it.get("target_price", 0.0)) if it.get("target_price") else None,
                "target_price_2": float(it.get("target_price_2", 0.0)) if it.get("target_price_2") else None,
                "stop_loss": float(it.get("stop_loss", 0.0)) if it.get("stop_loss") else None,
                "expected_upside": float(it.get("expected_upside", 0.0)) if it.get("expected_upside") else None,
                "quant_score": int(it.get("total_score", it.get("ai_score", 0))),
                "tags": it.get("tags", []),
                "pattern_status": it.get("pattern_status", ""),
                "status": it.get("status", "HOLD"),
                "max_price": float(it.get("max_price", 0.0)) if it.get("max_price") else None,
                "current_price": float(it.get("current_price", 0.0)) if it.get("current_price") else None,
                "current_pnl_pct": float(it.get("current_pnl_pct", 0.0)),
                "realized_pnl_pct": float(it.get("realized_pnl_pct", 0.0)) if it.get("realized_pnl_pct") is not None else None,
                "realized_pnl_net_pct": float(it.get("realized_pnl_net_pct", 0.0)) if it.get("realized_pnl_net_pct") is not None else None,
                "hit_success": bool(it.get("hit_success", False)),
                "is_completed": bool(it.get("is_completed", False)),
                "failure_reason": it.get("failure_reason"),
                "exit_price": float(it.get("exit_price", 0.0)) if it.get("exit_price") else None,
                "exit_date": str(it.get("exit_date", "")) if it.get("exit_date") else None,
                "strategy_version": strat_ver,
                "rules": it.get("rules"),
                "market_context": it.get("market_context"),
                "fee_slippage_pct": float(it.get("fee_slippage_pct", 0.25))
            }
            records.append(rec)

        # 전략 버전 분리 복합 키 (date, ticker, strategy_version) 기준 단일 엄격 upsert
        try:
            client.table("recommendation_history").upsert(
                records,
                on_conflict="date,ticker,strategy_version"
            ).execute()
            return True
        except Exception as v_err:
            err_msg = (
                f"[DB 오류] DB 스키마가 전략 버전 복합키(date, ticker, strategy_version) 저장을 지원하지 않습니다. "
                f"기존 (date, ticker) 단일키로 덮어쓰지 않고 저장을 중단합니다. "
                f"supabase_schema.sql 마이그레이션을 먼저 실행해 주세요. (상세: {v_err})"
            )
            print(err_msg)
            _last_error = err_msg
            return False

    except Exception as e:
        _last_error = f"Supabase 추천 이력 업서트 실패: {e}"
        print(f"[DB] Supabase 추천 이력 업서트 실패: {e}")
        return False


# -----------------------------------------------------------------------------
# 2. 당일 추천 스캔 캐시 (daily_recommendation_cache) DB 작업
# -----------------------------------------------------------------------------

def db_load_daily_cache(today_str: str) -> Optional[List[Dict[str, Any]]]:
    """Supabase에서 당일자 스캔 캐시를 불러옵니다."""
    client = get_supabase_client()
    if not client:
        return None

    try:
        response = client.table("daily_recommendation_cache") \
            .select("recommendations") \
            .eq("date", today_str) \
            .limit(1) \
            .execute()

        if response and hasattr(response, "data") and response.data:
            return response.data[0].get("recommendations", [])
    except Exception as e:
        print(f"[DB] Supabase 당일 캐시 조회 실패: {e}")
        return None

    return None


def db_save_daily_cache(today_str: str, recs: List[Dict[str, Any]]) -> bool:
    """Supabase에 당일자 스캔 캐시를 저장합니다."""
    client = get_supabase_client()
    if not client:
        return False

    try:
        client.table("daily_recommendation_cache").upsert({
            "date": today_str,
            "recommendations": recs,
            "updated_at": datetime.now().isoformat()
        }, on_conflict="date").execute()
        return True
    except Exception as e:
        print(f"[DB] Supabase 당일 캐시 저장 실패: {e}")
        return False
