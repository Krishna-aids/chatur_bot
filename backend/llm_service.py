from __future__ import annotations

import asyncio
import json

import httpx

from .settings import settings

SYSTEM_PROMPT = """You are a customer support assistant for an e-commerce platform.
You receive structured JSON context and a deterministic decision.
Your only job is to generate a customer-facing message and choose an action from allowed_actions.

Rules:
- Choose action from allowed_actions only.
- Do not make or override business decisions.
- Prefer priority_action unless explicit evidence supports another allowed action.
- Return only JSON: {"action":"...","message":"..."}"""


def _filter_context(context: dict) -> dict:
    return {
        "task": context["task"][:500],
        "facts": context["facts"],
        "rules": context["rules"],
        "memory": context["memory"],
        "evidence": context.get("evidence", [])[:3],
        "decision": context["decision"],
    }


def _validate_output(parsed: dict, allowed_actions: list[str], priority_action: str) -> dict:
    if "action" not in parsed or "message" not in parsed:
        raise ValueError("Invalid LLM output structure")
    action = parsed["action"]
    if action not in allowed_actions:
        action = priority_action
    message = str(parsed["message"]).strip()
    if len(message) < 5:
        raise ValueError("LLM message too short")
    return {"action": action, "message": message}


async def _groq_call(filtered_context: dict) -> dict:
    payload = {
        "model": settings.groq_model,
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(filtered_context, ensure_ascii=False)},
        ],
    }
    headers = {"Authorization": f"Bearer {settings.groq_api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        response = await client.post(f"{settings.groq_base_url}/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        return json.loads(raw)


async def generate_response(context: dict) -> dict:
    allowed = context["decision"]["allowed_actions"]
    priority = context["decision"]["priority_action"]
    filtered = _filter_context(context)
    if not settings.groq_api_key:
        return {
            "action": priority,
            "message": f"I can help with this. Next step: {priority.replace('_', ' ')}.",
        }

    attempts = 0
    last_error = None
    while attempts < 2:
        attempts += 1
        try:
            parsed = await asyncio.wait_for(_groq_call(filtered), timeout=settings.llm_timeout_seconds + 1)
            return _validate_output(parsed, allowed, priority)
        except Exception as exc:
            last_error = exc
            continue

    return {
        "action": priority,
        "message": "I am having trouble generating a detailed response right now, but I will proceed with the policy-safe action.",
        "error": str(last_error) if last_error else "unknown",
    }

