from __future__ import annotations

import asyncio
import secrets
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any


class InMemoryStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.users_by_email: dict[str, dict[str, Any]] = {}
        self.users_by_key: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, dict[str, Any]] = {}
        self.history: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.learning_logs: list[dict[str, Any]] = []
        self.event_logs: list[dict[str, Any]] = []
        self.action_receipts: set[tuple[str, str, str]] = set()
        self.orders_by_user: dict[str, dict[str, Any]] = {}
        self.product_policy: dict[str, Any] = {
            "return_window_days": 30,
            "max_returns_per_month": 3,
            "auto_refund_threshold_inr": 500,
            "refund_methods": ["original_payment", "store_credit"],
            "escalation_triggers": ["abusive", "legal_threat", "repeat_complaint"],
        }
        self._seed_defaults()

    def _seed_defaults(self) -> None:
        self.register_user(name="Demo User", email="demo@example.com", api_key="nm_demo_key")
        now = datetime.utcnow().date()
        self.orders_by_user["demo@example.com"] = {
            "order_id": "ORD-1001",
            "order_status": "delivered",
            "order_date": (now - timedelta(days=5)).isoformat(),
            "delivery_date": (now - timedelta(days=2)).isoformat(),
            "items": ["wireless mouse"],
            "payment_status": "paid",
            "refund_status": "none",
            "tracking_number": "TRK-92711",
            "return_window_days": 30,
        }

    def register_user(self, name: str, email: str, api_key: str | None = None) -> dict[str, Any]:
        key = api_key or f"nm_{secrets.token_hex(16)}"
        user = {"name": name, "email": email.lower(), "api_key": key, "is_active": True}
        self.users_by_email[user["email"]] = user
        self.users_by_key[key] = user
        if user["email"] not in self.orders_by_user:
            self.orders_by_user[user["email"]] = {
                "order_id": f"ORD-{uuid.uuid4().hex[:8].upper()}",
                "order_status": "shipped",
                "order_date": datetime.utcnow().date().isoformat(),
                "delivery_date": (datetime.utcnow().date() + timedelta(days=2)).isoformat(),
                "items": ["starter kit"],
                "payment_status": "paid",
                "refund_status": "none",
                "tracking_number": f"TRK-{uuid.uuid4().hex[:6].upper()}",
                "return_window_days": 30,
            }
        return user

    async def create_session(self, user_id: str) -> dict[str, str]:
        session_id = str(uuid.uuid4())
        session = {"session_id": session_id, "user_id": user_id, "created_at": datetime.utcnow().isoformat()}
        async with self._lock:
            self.sessions[session_id] = session
        return session

    async def save_message(self, session_id: str, role: str, content: str, route: str | None = None) -> None:
        async with self._lock:
            self.history[session_id].append(
                {"role": role, "content": content, "route": route, "created_at": datetime.utcnow().isoformat()}
            )

    async def get_history(self, session_id: str) -> list[dict[str, Any]]:
        return list(self.history.get(session_id, []))

    async def get_user(self, user_id: str) -> dict[str, Any]:
        user = self.users_by_email.get(user_id.lower())
        if user:
            return user
        return {"name": "Guest", "email": user_id, "api_key": "", "is_active": True}

    async def get_order(self, user_id: str) -> dict[str, Any]:
        return dict(self.orders_by_user.get(user_id.lower(), {}))

    async def get_policy(self) -> dict[str, Any]:
        return dict(self.product_policy)

    async def log_event(self, user_id: str, intent: str, action: str, status: str, trace_id: str) -> None:
        async with self._lock:
            self.event_logs.append(
                {
                    "user_id": user_id,
                    "intent": intent,
                    "action": action,
                    "status": status,
                    "trace_id": trace_id,
                    "created_at": datetime.utcnow().isoformat(),
                }
            )

    async def log_learning(
        self, user_id: str, intent: str, sub_intent: str, action_taken: str, outcome: str, sentiment: str
    ) -> None:
        async with self._lock:
            self.learning_logs.append(
                {
                    "user_id": user_id,
                    "intent": intent,
                    "sub_intent": sub_intent,
                    "action_taken": action_taken,
                    "outcome": outcome,
                    "sentiment": sentiment,
                    "created_at": datetime.utcnow().isoformat(),
                }
            )

    async def get_learning_rows(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = [r for r in self.learning_logs if r["user_id"].lower() == user_id.lower()]
        rows.sort(key=lambda x: x["created_at"], reverse=True)
        return rows[:limit]

    async def apply_action_idempotent(self, session_id: str, order_id: str, action: str) -> tuple[bool, str]:
        key = (session_id, order_id, action)
        async with self._lock:
            if key in self.action_receipts:
                return True, "Already applied — idempotent"
            self.action_receipts.add(key)
            return False, "Applied"

    async def update_order_status(self, user_id: str, **fields: Any) -> None:
        async with self._lock:
            current = self.orders_by_user.get(user_id.lower(), {})
            current.update(fields)
            self.orders_by_user[user_id.lower()] = current


store = InMemoryStore()

