from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import logging

logger = logging.getLogger("chatur.learning")

# MVP in-memory storage
LEARNING_LOGS: list[dict] = []


def log_event(user_id: str, intent: str, action: str, status: str) -> None:
    logger.info("user_id=%s intent=%s action=%s status=%s", user_id, intent, action, status)


def store_interaction(*, user_id: str, intent: str, action_taken: str, outcome: str) -> dict:
    record = {
        "user_id": user_id,
        "intent": intent,
        "action_taken": action_taken,
        "outcome": outcome,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    LEARNING_LOGS.append(record)
    return record


def compute_behavior_signals(*, user_id: str) -> dict:
    rows = [row for row in LEARNING_LOGS if row["user_id"] == user_id]
    if not rows:
        return {"preferred_action": "general_support"}
    counts = Counter(row["action_taken"] for row in rows)
    preferred_action = counts.most_common(1)[0][0]
    return {"preferred_action": preferred_action}

