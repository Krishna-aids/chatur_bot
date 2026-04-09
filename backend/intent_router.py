from __future__ import annotations

import re

from .models import IntentResult

INTENT_MAP: list[tuple[str, list[str], str, str]] = [
    ("ORDER_STATUS", [r"where.*order", r"track.*order", r"order.*status", r"deliver"], "tracking", "deterministic"),
    ("RETURN_REQUEST", [r"return", r"send.*back", r"exchange", r"replacement"], "damaged_item", "deterministic"),
    (
        "POLICY_QUERY",
        [
            r"refund policy",
            r"refund take",
            r"how long.*refund",
            r"privacy",
            r"data safe",
            r"data usage",
            r"marketplace policy",
            r"return policy",
            r"policy",
        ],
        "policy_lookup",
        "conversational",
    ),
    ("REFUND_STATUS", [r"refund status", r"where.*refund", r"refund update", r"money back status"], "refund_delay", "deterministic"),
    ("COMPLAINT", [r"complaint", r"unhappy", r"terrible", r"worst", r"legal"], "delivery_issue", "escalation_check"),
    ("CANCELLATION", [r"cancel", r"stop.*order"], "before_ship", "deterministic"),
    ("PAYMENT_ISSUE", [r"charged twice", r"payment fail", r"coupon", r"discount"], "payment_failed", "deterministic"),
    ("PRODUCT_QUERY", [r"product", r"item", r"available", r"spec", r"price"], "availability", "conversational"),
]


def _emotion(text: str) -> str:
    lowered = text.lower()
    if any(w in lowered for w in ["angry", "upset", "terrible", "worst", "hate"]):
        return "negative"
    if any(w in lowered for w in ["thanks", "great", "happy", "awesome"]):
        return "positive"
    return "neutral"


def route_intent(text: str) -> IntentResult:
    normalized = re.sub(r"[^\w\s]", " ", text.lower()).strip()
    for intent, patterns, sub_intent, mode in INTENT_MAP:
        for pattern in patterns:
            if re.search(pattern, normalized):
                confidence = 0.92 if pattern in normalized else 0.84
                return IntentResult(
                    intent=intent,
                    sub_intent=sub_intent,
                    emotion=_emotion(normalized),
                    confidence=confidence,
                    mode=mode,
                )
    return IntentResult(intent="UNKNOWN", sub_intent="general", emotion=_emotion(normalized), confidence=0.4, mode="conversational")

