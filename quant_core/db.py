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
_checked_env = False


def get_supabase_client():
    """
    Supabase 클라이언트를 초기화하여 반환합니다.
    Streamlit secrets 또는 환경 변수에서 URL과 KEY를 읽습니다.
    """
    global _supabase_client, _checked_env

    if _supabase_client is not None:
        return _supabase_client

    if _checked_env and _supabase_client is None:
        return None

    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "") or os.environ.get("SUPABASE_ANON_KEY", "")

    # Streamlit secrets 확인
    try:
        import streamlit as st
        if not url and "SUPABASE_URL" in st.secrets:
            url = str(st.secrets["SUPABASE_URL"]).strip()
        if not key and "SUPABASE_KEY" in st.secrets:
            key = str(st.secrets["SUPABASE_KEY"]).strip()
        if not key and "SUPABASE_ANON_KEY" in st.secrets:
            key = str(st.secrets["SUPABASE_ANON_KEY"]).strip()
    except Exception:
        pass

    _checked_env = True

    if url and key:
        try:
            from supabase import create_client
            _supabase_client = create_client(url, key)
            return _supabase_client
        except Exception as e:
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
                "hit_success": bool(it.get("hit_success", False)),
                "is_completed": bool(it.get("is_completed", False)),
                "failure_reason": it.get("failure_reason"),
                "exit_price": float(it.get("exit_price", 0.0)) if it.get("exit_price") else None,
                "exit_date": str(it.get("exit_date", "")) if it.get("exit_date") else None
            }
            records.append(rec)

        client.table("recommendation_history").upsert(
            records,
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
