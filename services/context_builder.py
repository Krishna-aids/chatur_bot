from __future__ import annotations


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
        "memory": {},
    }

