from __future__ import annotations

import re

_PAGEINDEX_DOCS: list[dict[str, str]] = [
    {
        "source": "refund-policy",
        "kind": "policy_text",
        "content": (
            "Refund policy: electronics refunds are processed in 7 days, fashion refunds in 5 days, "
            "and refund mode depends on payment method such as bank transfer or wallet credit."
        ),
    },
    {
        "source": "privacy-policy",
        "kind": "policy_text",
        "content": (
            "Privacy policy: we use customer data only for order support and service quality, "
            "retain records for 365 days, and users can request access, correction, and deletion."
        ),
    },
    {
        "source": "marketplace-policy",
        "kind": "policy_text",
        "content": (
            "Marketplace rules: first-party sellers allow direct return processing, while third-party "
            "returns may require additional evidence and dispute resolution through support."
        ),
    },
    {
        "source": "product-faq",
        "kind": "faq",
        "content": (
            "Product FAQ: return windows depend on product category, warranty varies by product, "
            "and replacement is available only when policy allows it."
        ),
    },
]


def _tokenize(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(token) > 1}


def retrieve_policy_chunks(query: str, top_k: int = 2) -> list[dict[str, str | int]]:
    query_tokens = _tokenize(query)
    scored: list[tuple[int, dict[str, str]]] = []
    for doc in _PAGEINDEX_DOCS:
        score = len(query_tokens.intersection(_tokenize(doc["content"])))
        scored.append((score, doc))
    scored.sort(key=lambda item: item[0], reverse=True)

    top = scored[: max(1, top_k)]
    return [
        {
            "source": doc["source"],
            "kind": doc["kind"],
            "content": doc["content"],
            "relevance_score": score,
        }
        for score, doc in top
    ]

