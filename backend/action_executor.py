from __future__ import annotations

from .models import ActionResult
from .store import store


async def execute_action(action: str, context: dict) -> ActionResult:
    allowed_actions = context["decision"]["allowed_actions"]
    if action not in allowed_actions:
        return ActionResult(False, "failure", "Action not allowed by deterministic decision")

    user_id = context["facts"]["user_id"]
    order_id = context["facts"].get("order_id", "N/A")
    session_id = context["session_id"]
    already_applied, note = await store.apply_action_idempotent(session_id=session_id, order_id=order_id, action=action)
    if already_applied:
        return ActionResult(True, "success", note)

    if action == "approve_return":
        await store.update_order_status(user_id, order_status="return_approved")
    elif action == "deny_return":
        await store.update_order_status(user_id, order_status="return_denied")
    elif action == "cancel_order":
        await store.update_order_status(user_id, order_status="cancelled")
    elif action == "issue_refund":
        await store.update_order_status(user_id, refund_status="issued")
    elif action == "log_complaint":
        await store.update_order_status(user_id, account_note="complaint_logged")
    elif action in {"request_evidence", "provide_tracking", "provide_information", "escalate"}:
        pass
    else:
        return ActionResult(False, "failure", "Unknown action")

    return ActionResult(True, "success", "Applied")

