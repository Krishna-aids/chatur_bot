from __future__ import annotations


async def generate_response(*, context: dict, intent_result: dict, decision_result: dict) -> dict:
    _ = context
    _ = intent_result
    return {
        "action": decision_result["priority_action"],
        "message": "Your order is currently in transit. You can track it from your orders page.",
    }

