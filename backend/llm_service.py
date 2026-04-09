from __future__ import annotations

import asyncio
import json
import re

import httpx

from .settings import settings

SYSTEM_PROMPT = """You are a polite and human customer support assistant for an e-commerce platform.
You receive structured JSON context and a deterministic decision, and must write a concise reply in 2-3 short sentences max.
Use DB rules as truth, use knowledge text only for explanation, never invent or alter policy values, and return only JSON: {"action":"...","message":"..."}.
Always choose action from allowed_actions only, prefer priority_action unless evidence supports another allowed action, include a clear answer and a suggested next action, use apologetic tone when emotion is angry and normal tone when emotion is neutral.
Use one of these styles naturally:
- Problem + Solution: "Your order is delayed. I can help you request a refund or wait a bit longer."
- Apology + Action: "Sorry about this 😔 I can arrange a replacement immediately."
"""


def _filter_context(context: dict) -> dict:
    return {
        "task": context["task"][:500],
        "facts": context["facts"],
        "rules": context["rules"],
        "knowledge": context.get("knowledge", {}),
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


def _infer_emotion(task: str) -> str:
    lowered = (task or "").lower()
    if any(word in lowered for word in ("angry", "frustrated", "upset", "terrible", "worst", "hate")):
        return "angry"
    return "neutral"


def _apply_tone(message: str, emotion: str) -> str:
    if emotion == "angry" and "sorry" not in message.lower():
        return f"I'm really sorry about this 😔 {message}"
    return message


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
        emotion = _infer_emotion(str(context.get("task", "")))
        base = format_response("I can help with this.", priority)
        return {"action": priority, "message": _apply_tone(base, emotion)}

    attempts = 0
    last_error = None
    while attempts < 2:
        attempts += 1
        try:
            parsed = await asyncio.wait_for(_groq_call(filtered), timeout=settings.llm_timeout_seconds + 1)
            validated = _validate_output(parsed, allowed, priority)
            emotion = _infer_emotion(str(context.get("task", "")))
            toned = _apply_tone(validated["message"], emotion)
            return {"action": validated["action"], "message": format_response(toned, validated["action"])}
        except Exception as exc:
            last_error = exc
            continue

    _ = last_error
    return {"action": priority, "message": format_response("I can still help right away.", priority)}

