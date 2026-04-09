from __future__ import annotations


async def make_decision(*, context: dict, intent_result: dict) -> dict:
    _ = context
    _ = intent_result
    return {
        "allowed_actions": ["provide_tracking", "escalate"],
        "priority_action": "provide_tracking",
        "reason": "Mock deterministic decision for Step 1",
    }

