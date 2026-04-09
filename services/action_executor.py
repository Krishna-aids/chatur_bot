from __future__ import annotations

from datetime import datetime, timezone

from services.learning_system import compute_behavior_signals, log_event, store_interaction

# MVP idempotency cache
_ACTION_CACHE: dict[tuple[str, str, str], dict] = {}


def _validate_action(action: str, allowed_actions: list[str], priority_action: str) -> str:
    if action in allowed_actions:
        return action
    return priority_action


def _handle_offer_replacement(order: dict) -> dict:
    if order.get("status") == "replacement_initiated":
        return {"result": "replacement_initiated", "idempotent": True}
    order["status"] = "replacement_initiated"
    return {"result": "replacement_initiated"}


def _handle_offer_refund(order: dict) -> dict:
    if order.get("status") == "refund_initiated":
        return {"result": "refund_initiated", "idempotent": True}
    order["status"] = "refund_initiated"
    return {"result": "refund_initiated"}


def _handle_show_status(order: dict) -> dict:
    return {"result": order.get("status", "unknown")}


def _execute_handler(action: str, order: dict) -> dict:
    if action == "offer_replacement":
        return _handle_offer_replacement(order)
    if action == "offer_refund":
        return _handle_offer_refund(order)
    if action == "show_status":
        return _handle_show_status(order)
    if action == "reject_return":
        return {"result": "not_eligible"}
    if action == "ask_clarification":
        return {"result": "clarification_needed"}
    if action == "general_support":
        return {"result": "support"}
    if action == "show_eta":
        return {"result": "eta_shared"}
    if action == "offer_wait":
        return {"result": "wait_offered"}
    return {"result": "support"}


async def execute_action(*, context: dict, decision_result: dict, llm_result: dict) -> dict:
    facts = context.get("facts", {})
    user = facts.get("user", {})
    order = facts.get("order", {})
    memory = context.setdefault("memory", {})
    intent = context.get("task", {}).get("intent") or "unknown"
    user_id = user.get("id") or "unknown_user"

    allowed_actions = list(decision_result.get("allowed_actions", []))
    priority_action = decision_result.get("priority_action", "general_support")
    requested_action = llm_result.get("action", "general_support")
    action = _validate_action(requested_action, allowed_actions, priority_action)
    llm_result["action"] = action

    # Idempotency key by user+intent+action
    cache_key = (user_id, intent, action)
    if cache_key in _ACTION_CACHE:
        cached = _ACTION_CACHE[cache_key]
        log_event(user_id, intent, action, "success")
        memory.update(compute_behavior_signals(user_id=user_id))
        return {
            "status": "success",
            "note": "Already processed",
            "action": action,
            "execution": cached,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    execution_result = _execute_handler(action, order)
    _ACTION_CACHE[cache_key] = execution_result

    store_interaction(
        user_id=user_id,
        intent=intent,
        action_taken=action,
        outcome="accepted",
    )
    memory.update(compute_behavior_signals(user_id=user_id))
    log_event(user_id, intent, action, "success")

    return {
        "status": "success",
        "note": "Action executed",
        "action": action,
        "execution": execution_result,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

