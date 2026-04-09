from __future__ import annotations

from statistics import mean

from .store import store


def compute_sentiment_trend(rows: list[dict]) -> str:
    if not rows:
        return "neutral"
    score_map = {"positive": 1, "neutral": 0, "negative": -1}
    scores = [score_map.get(r.get("sentiment", "neutral"), 0) for r in rows]
    avg = mean(scores)
    if avg >= 0.25:
        return "positive"
    if avg <= -0.25:
        return "negative"
    return "neutral"


async def fetch_memory_signals(user_id: str) -> dict:
    rows = await store.get_learning_rows(user_id=user_id, limit=20)
    return {
        "recent_intents": [r["intent"] for r in rows[:5]],
        "past_actions": [r["action_taken"] for r in rows],
        "complaint_count": sum(1 for r in rows if r["intent"] == "COMPLAINT"),
        "refund_count": sum(1 for r in rows if r["action_taken"] == "issue_refund"),
        "sentiment_trend": compute_sentiment_trend(rows),
        "vip_flag": False,
    }

