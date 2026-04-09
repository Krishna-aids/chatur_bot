from __future__ import annotations

import os
from pathlib import Path


async def _fetch_user(user_id: str) -> dict:
    return {"id": "U1", "name": "Tarun", "requested_user_id": user_id}


async def _fetch_latest_order(user_id: str) -> dict:
    _ = user_id
    return {
        "id": "O101",
        "status": "delivered",
        "product": "Headphones",
        "days_since_delivery": 2,
    }


async def _fetch_product_policy() -> dict:
    return {"return_window_days": 7, "replacement_allowed": True}


def _load_rag_knowledge() -> list[dict]:
    rag_path = Path(os.getenv("RAG_DATA_PATH", "./data/policies"))
    if not rag_path.exists():
        return [{"source": "mock-policy", "content": "Use product policy as source of truth.", "relevance_score": 1}]

    docs: list[dict] = []
    for file_path in rag_path.glob("**/*"):
        if not file_path.is_file() or file_path.suffix.lower() not in {".txt", ".md", ".json"}:
            continue
        content = file_path.read_text(encoding="utf-8", errors="ignore").strip()
        if not content:
            continue
        docs.append(
            {
                "source": str(file_path),
                "content": content[:600],
                "relevance_score": 1,
            }
        )
        if len(docs) >= 3:
            break

    return docs or [{"source": "mock-policy", "content": "Use product policy as source of truth.", "relevance_score": 1}]


def _db_config() -> dict:
    return {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "user": os.getenv("MYSQL_USER", "root"),
        "database": os.getenv("MYSQL_DB", "ecommerce_ai"),
    }


async def build_context(*, user_id: str, session_id: str, text: str) -> dict:
    user = await _fetch_user(user_id)
    order = await _fetch_latest_order(user_id)
    policy = await _fetch_product_policy()

    # Step 2 filter: only include required policy/order fields.
    filtered_order = {
        "status": order["status"],
        "product": order["product"],
    }
    filtered_policy = {
        "return_window_days": policy["return_window_days"],
        "replacement_allowed": policy["replacement_allowed"],
    }

    return {
        "task": {"query": text},
        "session_id": session_id,
        "facts": {
            "user": {"id": user["id"], "name": user["name"]},
            "order": filtered_order,
        },
        "rules": {"product_policy": filtered_policy},
        "knowledge": _load_rag_knowledge(),
        "memory": {},
        "db": _db_config(),
    }

