from __future__ import annotations

import json
import os
import re

import httpx

SYSTEM_PROMPT = """You are a polite and human customer support assistant.
You receive structured JSON context and must return exactly one concise reply in 2-3 short sentences max.
Always choose action from allowed_actions only, prefer priority_action, provide a clear answer plus a suggested next action, use apologetic tone when emotion is angry and normal tone when emotion is neutral, and return only valid JSON: {"action":"...","message":"..."}.
Use one of these styles naturally:
- Problem + Solution: "Your order is delayed. I can help you request a refund or wait a bit longer."
- Apology + Action: "Sorry about this 😔 I can arrange a replacement immediately."
"""

FALLBACK = {"action": "general_support", "message": "Let me help you with that."}


def _build_filtered_context(context: dict, intent_result: dict, decision_result: dict) -> dict:
    order = context.get("facts", {}).get("order", {})
    return {
        "task": {
            "query": context.get("task", {}).get("query", ""),
            "intent": intent_result.get("intent", ""),
            "emotion": intent_result.get("emotion", "neutral"),
        },
        "facts": {
            "order_status": order.get("status", ""),
            "product": order.get("product", ""),
        },
        "decision": {
            "allowed_actions": decision_result.get("allowed_actions", []),
            "priority_action": decision_result.get("priority_action", ""),
            "reason": decision_result.get("reason", ""),
        },
    }


def _validate_output(candidate: dict, allowed_actions: list[str], priority_action: str) -> dict:
    action = str(candidate.get("action", "")).strip()
    message = str(candidate.get("message", "")).strip()

    if not message:
        message = FALLBACK["message"]
    if action not in allowed_actions:
        action = priority_action or FALLBACK["action"]

    return {"action": action, "message": message}


def _action_to_text(action: str) -> str:
    return action.replace("_", " ").strip() if action else "next support step"


def _split_sentences(message: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", message.strip()) if part.strip()]


def format_response(message: str, action: str) -> str:
    cleaned = re.sub(r"\s+", " ", (message or "").strip())
    if not cleaned:
        cleaned = "I can help with this."

    action_phrase = _action_to_text(action)
    if action_phrase.lower() not in cleaned.lower():
        if cleaned and cleaned[-1] not in ".!?":
            cleaned += "."
        cleaned = f"{cleaned} I can help you with {action_phrase}."

    sentences = _split_sentences(cleaned)
    if not sentences:
        return f"I can help with this. I can help you with {action_phrase}."
    if len(sentences) > 3:
        sentences = sentences[:3]
    return " ".join(sentences)


def _apply_tone(message: str, emotion: str) -> str:
    if emotion == "angry" and "sorry" not in message.lower():
        return f"I'm really sorry about this 😔 {message}"
    return message


async def _call_groq(filtered_context: dict, api_key: str) -> dict:
    payload = {
        "model": "llama3-70b-8192",
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(filtered_context, ensure_ascii=False)},
        ],
    }
    async with httpx.AsyncClient(timeout=12.0) as client:
        response = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content)


async def generate_response(*, context: dict, intent_result: dict, decision_result: dict) -> dict:
    filtered_context = _build_filtered_context(context, intent_result, decision_result)
    allowed_actions = list(decision_result.get("allowed_actions", []))
    priority_action = str(decision_result.get("priority_action", "")).strip()

    # Retry once on failure.
    api_key = os.getenv("GROQ_API_KEY", "")
    attempts = 0
    last_candidate: dict | None = None
    while attempts < 2:
        attempts += 1
        try:
            if not api_key:
                break
            last_candidate = await _call_groq(filtered_context, api_key)
            break
        except Exception:
            continue

    # 1) JSON validation + 4) fallback response.
    if not isinstance(last_candidate, dict):
        last_candidate = dict(FALLBACK)

    # 2) Action validation (and fallback adjustment to allowed action when needed).
    validated = _validate_output(last_candidate, allowed_actions, priority_action)
    toned = _apply_tone(validated["message"], str(intent_result.get("emotion", "neutral")).strip().lower())
    return {"action": validated["action"], "message": format_response(toned, validated["action"])}

