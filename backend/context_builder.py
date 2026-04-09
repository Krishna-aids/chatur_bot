from __future__ import annotations

import asyncio

from .learning import fetch_memory_signals
from .models import InputPayload
from .store import store


async def build_context(payload: InputPayload) -> dict:
    user_data, latest_order, policy, memory = await asyncio.gather(
        store.get_user(payload.user_id),
        store.get_order(payload.user_id),
        store.get_policy(),
        fetch_memory_signals(payload.user_id),
    )
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
            "payment_status": latest_order.get("payment_status", "unknown"),
            "refund_status": latest_order.get("refund_status", "none"),
            "tracking_number": latest_order.get("tracking_number", ""),
        },
        "rules": policy,
        "knowledge": [{"source": "mock-policy", "content": "Use product policy as source of truth.", "relevance_score": 1}],
        "evidence": payload.raw_attachments,
        "memory": memory,
        "decision": None,
    }

