"""
quant_core/db.py
Supabase(PostgreSQL) 클라우드 데이터베이스 연동 및 로컬 JSON 자동 폴백 어댑터
- Supabase 시크릿(SUPABASE_URL, SUPABASE_KEY) 존재 시: 클라우드 DB 연동
- 시크릿 미설정 시: 기존 로컬 JSON 파일로 안전하게 자동 폴백(Fallback)
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Any, Optional

# Supabase 클라이언트 싱글톤 캐시
_supabase_client = None
_last_error = None


def get_supabase_diagnostics() -> Dict[str, Any]:
    """Supabase 연결 상태 상세 진단 정보 반환"""
    global _supabase_client, _last_error
    url = os.environ.get("SUPABASE_URL", "") or os.environ.get("supabase_url", "")
    key = os.environ.get("SUPABASE_KEY", "") or os.environ.get("SUPABASE_ANON_KEY", "") or os.environ.get("supabase_key", "")

    try:
        import streamlit as st
        for k in st.secrets.keys():
            k_lower = k.lower().replace("-", "_")
            if k_lower in ("supabase_url", "supabase_project_url"):
                if not url:
                    url = str(st.secrets[k]).strip()
            elif k_lower in ("supabase_key", "supabase_anon_key", "supabase_publishable_key", "supabase_anon"):
                if not key:
                    key = str(st.secrets[k]).strip()
    except Exception as e:
        pass

    package_installed = False
    try:
        import supabase
        package_installed = True
    except Exception:
        package_installed = False

    return {
        "has_url": bool(url),
        "has_key": bool(key),
        "url_preview": f"{url[:15]}..." if url else "없음",
        "package_installed": package_installed,
        "is_connected": _supabase_client is not None,
        "last_error": str(_last_error) if _last_error else None
    }


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

    # Streamlit secrets 확인 (대소문자 및 변수명 유연 매핑)
    try:
        import streamlit as st
        for k in st.secrets.keys():
            k_lower = k.lower().replace("-", "_")
            if k_lower in ("supabase_url", "supabase_project_url"):
                if not url:
                    url = str(st.secrets[k]).strip()
            elif k_lower in ("supabase_key", "supabase_anon_key", "supabase_publishable_key", "supabase_anon"):
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
    """Supabase DB 연결 가능 여부 확인"""
    return get_supabase_client() is not None


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
    """Supabase에 추천 이력 리스트를 업서트(Insert or Update)합니다."""
    client = get_supabase_client()
    if not client or not items:
        return False

    try:
        records = []
        for it in items:
            rec = {
                "date": str(it.get("date", "")),
                "ticker": it.get("ticker", ""),
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
                "strategy_version": it.get("strategy_version", "v1.0.0"),
                "rules": it.get("rules"),
                "market_context": it.get("market_context"),
                "fee_slippage_pct": float(it.get("fee_slippage_pct", 0.25))
            }
            records.append(rec)

        try:
            client.table("recommendation_history").upsert(
                records,
                on_conflict="date,ticker"
            ).execute()
            return True
        except Exception as full_err:
            # 신규 컬럼이 아직 마이그레이션되지 않은 기존 테이블의 경우 구버전 필드만으로 2차 시도
            fallback_records = []
            for r in records:
                fb = dict(r)
                fb.pop("strategy_version", None)
                fb.pop("rules", None)
                fb.pop("market_context", None)
                fb.pop("fee_slippage_pct", None)
                fb.pop("realized_pnl_net_pct", None)
                fallback_records.append(fb)
            client.table("recommendation_history").upsert(
                fallback_records,
                on_conflict="date,ticker"
            ).execute()
            return True
    except Exception as e:
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
