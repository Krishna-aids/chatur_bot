from __future__ import annotations

import asyncio

from .learning import fetch_memory_signals
from .models import InputPayload
from .page_index import retrieve_policy_chunks
from .store import store


def _build_knowledge(chunks: list[dict[str, str | int]]) -> dict:
    policy_text_parts: list[str] = []
    faq_parts: list[str] = []
    for chunk in chunks:
        content = str(chunk.get("content", "")).strip()
        if not content:
            continue
        if chunk.get("kind") == "faq":
            faq_parts.append(content)
        else:
            policy_text_parts.append(content)
    return {
        "policy_text": " ".join(policy_text_parts).strip(),
        "faq": " ".join(faq_parts).strip(),
        "chunks": chunks,
    }


async def build_context(payload: InputPayload) -> dict:
    user_data, latest_order, memory = await asyncio.gather(
        store.get_user(payload.user_id),
        store.get_order(payload.user_id),
        fetch_memory_signals(payload.user_id),
    )

    product_id = str(latest_order.get("product_id", "")).strip()
    category = str(latest_order.get("category", "")).strip().lower()
    seller_type = str(latest_order.get("seller_type", "")).strip().lower()
    product_policy, refund_policy, marketplace_policy, privacy_policy = await asyncio.gather(
        store.get_product_policy(product_id),
        store.get_refund_policy(category),
        store.get_marketplace_policy(seller_type),
        store.get_privacy_policy(),
    )

    rag_chunks = retrieve_policy_chunks(payload.text, top_k=2)
    return {
        "task": payload.text,
        "facts": {
            "user_id": payload.user_id,
            "user_name": user_data.get("name", "Guest"),
            "email": user_data.get("email", payload.user_id),
            "order_id": latest_order.get("order_id", "N/A"),
            "order_status": latest_order.get("order_status", "unknown"),
            "order_date": latest_order.get("order_date", ""),
            "delivery_date": latest_order.get("delivery_date", ""),
            "items": latest_order.get("items", []),
            "product_id": product_id,
            "category": category,
            "seller_type": seller_type,
            "payment_status": latest_order.get("payment_status", "unknown"),
            "refund_status": latest_order.get("refund_status", "none"),
            "tracking_number": latest_order.get("tracking_number", ""),
        },
        "rules": {
            "product_policy": product_policy,
            "refund_policy": refund_policy,
            "marketplace_policy": marketplace_policy,
            "privacy_policy": privacy_policy,
            "max_returns_per_month": 3,
            "escalation_triggers": ["abusive", "legal_threat", "repeat_complaint"],
        },
        "knowledge": _build_knowledge(rag_chunks),
        "evidence": payload.raw_attachments,
        "memory": memory,
        "decision": None,
    }

