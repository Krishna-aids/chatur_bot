from __future__ import annotations


def _emotion_from_query(query: str) -> str:
    lowered = query.lower()
    if any(word in lowered for word in ("frustrated", "again", "angry")):
        return "angry"
    return "neutral"


def _detect_intent(query: str) -> tuple[str, str, float]:
    lowered = query.lower()

    if any(phrase in lowered for phrase in ("where is my order", "track my order", "order status", "where's my order")):
        return "order_tracking", "tracking_status", 0.92

    if any(word in lowered for word in ("broken", "damaged", "defective", "not working")):
        return "complaint", "product_issue", 0.88

    if any(word in lowered for word in ("refund", "return", "money back")):
        return "return_refund", "return_or_refund_request", 0.9

    if any(word in lowered for word in ("product", "price", "spec", "feature", "available")):
        return "product_query", "product_information", 0.82

    if any(word in lowered for word in ("hi", "hello", "hey", "good morning", "good evening")):
        return "greeting", "salutation", 0.8

    # Weak match fallback.
    if len(lowered.split()) <= 2:
        return "greeting", "short_message", 0.45
    return "product_query", "generic_query", 0.4


async def route_intent(*, context: dict) -> dict:
    query = context.get("task", {}).get("query", "")
    intent, sub_intent, confidence = _detect_intent(query)
    emotion = _emotion_from_query(query)

    if confidence < 0.5:
        intent = "ask_clarification"
        sub_intent = "needs_more_information"

    mode = "deterministic" if intent in {"order_tracking", "complaint", "return_refund"} else "conversational"

    return {
        "intent": intent,
        "sub_intent": sub_intent,
        "emotion": emotion,
        "confidence": round(confidence, 2),
        "mode": mode,
    }

