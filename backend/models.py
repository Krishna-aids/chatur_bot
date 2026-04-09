from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    name: str
    email: str


class RegisterResponse(BaseModel):
    name: str
    email: str
    api_key: str
    is_active: bool = True


class AuthKeyResponse(BaseModel):
    name: str
    email: str
    api_key: str
    is_active: bool = True


class NewSessionRequest(BaseModel):
    user_id: str | None = None


class NewSessionResponse(BaseModel):
    session_id: str
    user_id: str
    created_at: str


class ChatRequest(BaseModel):
    query: str
    session_id: str
    input_type: str = "text"
    output_voice: bool = False
    language: str | None = None


class ChatResponse(BaseModel):
    response: str
    route: str
    session_id: str
    reason_trace: dict[str, Any] = Field(default_factory=dict)
    audio_bytes: str | None = None
    chunk_batches: list[list[str]] = Field(default_factory=list)
    message: str
    action: str
    status: str


class UploadResponse(BaseModel):
    message: str
    chunks_added: int = 0
    total_chunks: int = 0


class MessageRecord(BaseModel):
    role: str
    content: str
    route: str | None = None
    created_at: str


class HistoryResponse(BaseModel):
    session_id: str
    messages: list[MessageRecord]
    count: int


class VoiceChunkRequest(BaseModel):
    texts: list[str]
    language: str = ""


@dataclass(slots=True)
class InputPayload:
    user_id: str
    session_id: str
    text: str
    raw_attachments: list[dict[str, Any]] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass(slots=True)
class IntentResult:
    intent: str
    sub_intent: str
    emotion: str
    confidence: float
    mode: str


@dataclass(slots=True)
class DecisionResult:
    allowed_actions: list[str]
    priority_action: str
    reason: str


@dataclass(slots=True)
class ActionResult:
    success: bool
    status: str
    note: str = ""

