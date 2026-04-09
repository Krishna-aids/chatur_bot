from __future__ import annotations

from datetime import date

from .models import DecisionResult, IntentResult


def _days_since(iso_date: str) -> int | None:
    if not iso_date:
        return None
    try:
        d = date.fromisoformat(iso_date)
        return (date.today() - d).days
    except ValueError:
        return None


def run_decision_engine(intent: IntentResult, context: dict) -> DecisionResult:
    facts = context["facts"]
    rules = context["rules"]
    memory = context["memory"]
    task = context["task"].lower()

    if intent.intent == "RETURN_REQUEST":
        days_since_delivery = _days_since(facts.get("delivery_date", ""))
        return_window = int(rules.get("return_window_days", 30))
        if days_since_delivery is not None and days_since_delivery > return_window:
            return DecisionResult(["deny_return"], "deny_return", "Outside return window")
        if memory.get("refund_count", 0) > int(rules.get("max_returns_per_month", 3)):
            return DecisionResult(["escalate"], "escalate", "Refund abuse risk based on behavior")
        has_evidence = bool(context.get("evidence"))
        if has_evidence or memory.get("vip_flag"):
            return DecisionResult(["approve_return", "escalate"], "approve_return", "Within policy and eligible")
        return DecisionResult(["request_evidence", "approve_return"], "request_evidence", "Need evidence before approval")

    if intent.intent == "ORDER_STATUS":
        return DecisionResult(["provide_tracking"], "provide_tracking", "Informational order status request")

    if intent.intent == "REFUND_STATUS":
        return DecisionResult(["provide_information", "escalate"], "provide_information", "Refund status request")

    if intent.intent == "CANCELLATION":
        if facts.get("order_status") in {"pending", "processing"}:
            return DecisionResult(["cancel_order"], "cancel_order", "Order can be cancelled before shipment")
        return DecisionResult(["escalate"], "escalate", "Cannot auto-cancel shipped/delivered order")

    if intent.intent == "PAYMENT_ISSUE":
        return DecisionResult(["issue_refund", "escalate"], "issue_refund", "Payment issue under policy thresholds")

    if intent.intent == "COMPLAINT":
        triggers = rules.get("escalation_triggers", [])
        if any(t in task for t in triggers) or memory.get("complaint_count", 0) >= 3:
            return DecisionResult(["escalate"], "escalate", "Escalation trigger hit")
        return DecisionResult(["log_complaint", "escalate"], "log_complaint", "Standard complaint flow")

    return DecisionResult(["provide_information"], "provide_information", "General conversational fallback")

