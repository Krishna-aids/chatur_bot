from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from backend.auth import router as auth_router
from backend.auth import verify_api_key
from backend.models import (
    ChatRequest,
    ChatResponse,
    HistoryResponse,
    MessageRecord,
    NewSessionRequest,
    NewSessionResponse,
    UploadResponse,
    VoiceChunkRequest,
)
from backend.pipeline import run_chat_pipeline
from backend.settings import settings
from backend.store import store
from services.action_executor import execute_action
from services.context_builder import build_context
from services.decision_engine import make_decision
from services.intent_router import route_intent
from services.llm_service import generate_response

AGENT_TIMEOUT = 25
MAX_FILE_SIZE = 10 * 1024 * 1024

logger = logging.getLogger("chatur.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
    logger.info("Starting Chatur MVP backend.")
    yield
    logger.info("Shutting down Chatur MVP backend.")


app = FastAPI(title="Chatur MVP API", version=settings.app_version, lifespan=lifespan)
app.include_router(auth_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": settings.app_version,
        "llm": "configured" if settings.groq_api_key else "mock-mode",
        "model_70b": settings.groq_model,
    }


@app.post("/session/new", response_model=NewSessionResponse)
async def new_session(request: NewSessionRequest, auth: dict = Depends(verify_api_key)):
    user_id = auth.get("user_id") or request.user_id or "anonymous@example.com"
    session = await store.create_session(user_id=user_id)
    return NewSessionResponse(**session)


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, auth: dict = Depends(verify_api_key)):
    user_id = auth.get("user_id", "anonymous@example.com")
    try:
        context = await build_context(user_id=user_id, session_id=request.session_id, text=request.query)
        intent_result = await route_intent(context=context)
        decision_result = await make_decision(context=context, intent_result=intent_result)
        llm_result = await generate_response(
            context=context,
            intent_result=intent_result,
            decision_result=decision_result,
        )
        action_result = await execute_action(
            context=context,
            decision_result=decision_result,
            llm_result=llm_result,
        )
        result = {
            "message": llm_result["message"],
            "action": llm_result["action"],
            "status": action_result["status"],
            "response": llm_result["message"],
            "route": "TOOL",
            "session_id": request.session_id,
            "reason_trace": {
                "pipeline": [
                    "context_builder",
                    "intent_router",
                    "decision_engine",
                    "llm_service",
                    "action_executor",
                ],
                "intent_result": intent_result,
                "decision_result": decision_result,
                "action_result": action_result,
            },
            "audio_bytes": None,
            "chunk_batches": [],
        }
    except asyncio.TimeoutError:
        result = {
            "message": "I'm taking too long to respond. Please try again.",
            "action": "provide_information",
            "status": "failure",
            "response": "I'm taking too long to respond. Please try again.",
            "route": "TIMEOUT",
            "session_id": request.session_id,
            "reason_trace": {"error": "timeout"},
            "audio_bytes": None,
            "chunk_batches": [],
        }
    return ChatResponse(**result)


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest, auth: dict = Depends(verify_api_key)):
    user_id = auth.get("user_id", "anonymous@example.com")

    async def generate():
        try:
            result = await asyncio.wait_for(
                run_chat_pipeline(user_id=user_id, session_id=request.session_id, query=request.query),
                timeout=AGENT_TIMEOUT,
            )
            route = result.get("route", "CHAT")
            response = result.get("response", "")
            yield f"data: [ROUTE]{route}\n\n"
            for idx, token in enumerate(response.split(" ")):
                chunk = token if idx == len(response.split(" ")) - 1 else token + " "
                yield f"data: {chunk}\n\n"
                await asyncio.sleep(0.01)
            yield "data: [DONE]\n\n"
        except asyncio.TimeoutError:
            yield "data: [ERROR] timeout\n\n"
            yield "data: [DONE]\n\n"
        except Exception as exc:
            yield f"data: [ERROR] {exc}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/upload", response_model=UploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    user_id: str = Form(default="anonymous@example.com"),
    source: str = Form(default=""),
    auth: dict = Depends(verify_api_key),
):
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(raw_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large. Max 10MB.")

    file_name = source or file.filename or "uploaded_file"
    owner = auth.get("user_id", user_id)
    digest = hashlib.md5(f"{owner}:{file_name}:{len(raw_bytes)}".encode()).hexdigest()
    await store.save_message(session_id=digest, role="system", content=f"Uploaded {file_name}", route="UPLOAD")
    return UploadResponse(message=f"'{file_name}' received and queued.", chunks_added=0, total_chunks=0)


@app.get("/upload/status/{doc_id}")
async def upload_status(doc_id: str, auth: dict = Depends(verify_api_key)):
    _ = auth
    return {"status": "ready", "doc_id": doc_id}


@app.get("/history/{session_id}", response_model=HistoryResponse)
async def history(session_id: str, auth: dict = Depends(verify_api_key)):
    _ = auth
    rows = await store.get_history(session_id)
    messages = [MessageRecord(**row) for row in rows]
    return HistoryResponse(session_id=session_id, messages=messages, count=len(messages))


async def _transcribe_with_groq(audio_data: bytes, audio_format: str) -> str:
    if not settings.groq_api_key:
        return ""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{settings.groq_base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                files={"file": (f"audio.{audio_format}", audio_data, f"audio/{audio_format}")},
                data={"model": "whisper-large-v3"},
            )
            response.raise_for_status()
            return response.json().get("text", "").strip()
    except Exception:
        return ""


@app.post("/voice/chat")
async def voice_chat(
    audio: UploadFile = File(...),
    session_id: str = Form(...),
    language: str = Form(default=""),
    auth: dict = Depends(verify_api_key),
):
    raw_bytes = await audio.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file.")
    if len(raw_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="Audio too large. Max 10MB.")

    ext = "webm"
    if audio.filename and "." in audio.filename:
        ext = audio.filename.split(".")[-1].lower()

    transcription = await _transcribe_with_groq(raw_bytes, ext)
    if not transcription:
        transcription = "I sent a voice message."

    result = await run_chat_pipeline(user_id=auth.get("user_id"), session_id=session_id, query=transcription)
    _ = language
    return {
        "transcription": transcription,
        "response": result["response"],
        "route": result["route"],
        "session_id": session_id,
        "audio_b64_first": None,
        "chunk_batches": [],
        "reason_trace": result["reason_trace"],
    }


@app.post("/voice/chunk")
async def voice_chunk(request: VoiceChunkRequest, auth: dict = Depends(verify_api_key)):
    _ = auth
    _ = request
    return {"audio_b64": None}

