from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from .action_executor import execute_action
from .context_builder import build_context
from .decision_engine import run_decision_engine
from .intent_router import route_intent
from .llm_service import generate_response
from .models import ActionResult, DecisionResult, InputPayload
from .store import store

logger = logging.getLogger("chatur.pipeline")


def _route_label(mode: str) -> str:
    if mode == "deterministic":
        return "TOOL"
    if mode == "escalation_check":
        return "AGENT"
    return "CHAT"


def _sentiment_from_emotion(emotion: str) -> str:
    if emotion == "positive":
        return "positive"
    if emotion == "negative":
        return "negative"
    return "neutral"


async def run_chat_pipeline(
    *, user_id: str, session_id: str, query: str, raw_attachments: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    trace_id = uuid.uuid4().hex[:12]
    payload = InputPayload(user_id=user_id, session_id=session_id, text=query, raw_attachments=raw_attachments or [])
    context = await build_context(payload)
    intent = route_intent(payload.text)
    if intent.intent == "POLICY_QUERY":
        decision = DecisionResult(
            allowed_actions=["provide_information"],
            priority_action="provide_information",
            reason="Policy query handled via RAG explanation",
        )
    else:
        decision = run_decision_engine(intent, context)

    context["session_id"] = session_id
    context["decision"] = {
        "intent": intent.intent,
        "sub_intent": intent.sub_intent,
        "allowed_actions": decision.allowed_actions,
        "priority_action": decision.priority_action,
        "reason": decision.reason,
        "deterministic": intent.mode == "deterministic",
    }

    llm_output = await generate_response(context)
    if intent.intent == "POLICY_QUERY":
        action_result = ActionResult(True, "success", "No state-changing action for policy query")
    else:
        action_result = await execute_action(llm_output["action"], context)

    await asyncio.gather(
        store.save_message(session_id, "user", query),
        store.save_message(session_id, "assistant", llm_output["message"], route=_route_label(intent.mode)),
        store.log_event(user_id, intent.intent, llm_output["action"], action_result.status, trace_id),
        store.log_learning(
            user_id=user_id,
            intent=intent.intent,
            sub_intent=intent.sub_intent,
            action_taken=llm_output["action"],
            outcome=action_result.status if action_result.success else "failure",
            sentiment=_sentiment_from_emotion(intent.emotion),
        ),
    )

    logger.info(
        "trace_id=%s intent=%s action=%s status=%s", trace_id, intent.intent, llm_output["action"], action_result.status
    )
    return {
        "message": llm_output["message"],
        "action": llm_output["action"],
        "status": "success" if action_result.success else "failure",
        "response": llm_output["message"],
        "route": _route_label(intent.mode),
        "session_id": session_id,
        "reason_trace": {
            "trace_id": trace_id,
            "intent": intent.intent,
            "sub_intent": intent.sub_intent,
            "emotion": intent.emotion,
            "confidence": intent.confidence,
            "allowed_actions": decision.allowed_actions,
            "priority_action": decision.priority_action,
            "reason": decision.reason,
            "executor_note": action_result.note,
        },
        "audio_bytes": None,
        "chunk_batches": [],
    }

