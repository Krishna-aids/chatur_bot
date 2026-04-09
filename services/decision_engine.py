from __future__ import annotations


def _to_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except Exception:
        return default


def _pick_priority(allowed_actions: list[str]) -> str:
    if not allowed_actions:
        return "general_support"
    preference = {
        "offer_replacement": 1,
        "offer_refund": 2,
        "offer_wait": 3,
    }
    ranked = sorted(allowed_actions, key=lambda action: preference.get(action, 100))
    return ranked[0]


async def make_decision(*, context: dict, intent_result: dict) -> dict:
    intent = str(intent_result.get("intent", "")).lower()
    facts = context.get("facts", {})
    rules = context.get("rules", {}).get("product_policy", {})
    order = facts.get("order", {})

    order_status = str(order.get("status", "")).lower()
    days_since_delivery = _to_int(order.get("days_since_delivery", 0), 0)
    return_window_days = _to_int(rules.get("return_window_days", 0), 0)
    replacement_allowed = bool(rules.get("replacement_allowed", False))

    allowed_actions: list[str]
    reason: str

    # 1) ORDER TRACKING
    if intent == "order_tracking":
        if order_status == "delivered":
            allowed_actions = ["show_status"]
            reason = "Order has been delivered"
        elif order_status == "shipped":
            allowed_actions = ["show_eta"]
            reason = "Order is shipped and in transit"
        elif order_status == "delayed":
            allowed_actions = ["offer_refund", "offer_wait"]
            reason = "Order is delayed"
        else:
            allowed_actions = ["general_support"]
            reason = "Order status is unavailable"

    # 2) COMPLAINT (DAMAGED PRODUCT)
    elif intent == "complaint":
        if order_status == "delivered":
            if days_since_delivery <= return_window_days:
                allowed_actions = ["offer_replacement", "offer_refund"]
                reason = "Product delivered within return window"
            else:
                allowed_actions = ["reject_return"]
                reason = "Return window expired"
        else:
            allowed_actions = ["general_support"]
            reason = "Complaint rule requires delivered order"

    # 3) RETURN / REFUND
    elif intent == "return_refund":
        if days_since_delivery <= return_window_days:
            if replacement_allowed:
                allowed_actions = ["offer_replacement", "offer_refund"]
                reason = "Within return window and replacement is allowed"
            else:
                allowed_actions = ["offer_refund"]
                reason = "Within return window but replacement is not allowed"
        else:
            allowed_actions = ["reject_return"]
            reason = "Return window expired"

    # 4) LOW CONFIDENCE
    elif intent == "ask_clarification":
        allowed_actions = ["ask_clarification"]
        reason = "Low confidence intent detection"

    # 5) FALLBACK
    else:
        allowed_actions = ["general_support"]
        reason = "No deterministic rule matched"

    priority_action = _pick_priority(allowed_actions)
    return {
        "allowed_actions": allowed_actions,
        "priority_action": priority_action,
        "reason": reason,
    }

