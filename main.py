from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
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
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    rag_path = Path(settings.rag_data_path)
    return {
        "status": "ok",
        "version": settings.app_version,
        "llm": "configured" if settings.groq_api_key else "mock-mode",
        "model_70b": settings.groq_model,
        "rag_docs": len(list(rag_path.glob("**/*"))) if rag_path.exists() else 0,
    }


@app.post("/session/new", response_model=NewSessionResponse)
async def new_session(request: NewSessionRequest, auth: dict = Depends(verify_api_key)):
    user_id = auth.get("user_id") or request.user_id or "anonymous@example.com"
    session = await store.create_session(user_id=user_id)
    return NewSessionResponse(**session)


async def _parse_chat_input(request: Request, auth: dict) -> dict:
    content_type = request.headers.get("content-type", "").lower()
    payload: dict = {"message": "", "user_id": auth.get("user_id", "anonymous@example.com"), "session_id": "", "input_type": "text"}
    file_obj: UploadFile | None = None

    if "multipart/form-data" in content_type:
        form = await request.form()
        payload["message"] = str(form.get("message") or form.get("query") or "").strip()
        payload["user_id"] = str(form.get("user_id") or payload["user_id"]).strip() or payload["user_id"]
        payload["session_id"] = str(form.get("session_id") or "").strip()
        payload["input_type"] = str(form.get("input_type") or "text").strip().lower()
        incoming_file = form.get("file")
        if isinstance(incoming_file, UploadFile):
            file_obj = incoming_file
    else:
        body = await request.json()
        payload["message"] = str(body.get("message") or body.get("query") or "").strip()
        payload["user_id"] = str(body.get("user_id") or payload["user_id"]).strip() or payload["user_id"]
        payload["session_id"] = str(body.get("session_id") or "").strip()
        payload["input_type"] = str(body.get("input_type") or "text").strip().lower()

    payload["file"] = file_obj
    return payload


async def _mock_file_signal(file_obj: UploadFile | None, input_type: str) -> str:
    if not file_obj:
        return ""

    file_name = (file_obj.filename or "uploaded_file").lower()
    is_image = input_type == "image" or file_name.endswith((".png", ".jpg", ".jpeg", ".webp"))
    is_pdf = input_type == "pdf" or file_name.endswith(".pdf")

    if is_image:
        return "Image received. Mock damage detection suggests possible visible damage."

    if is_pdf:
        raw_bytes = await file_obj.read()
        decoded = raw_bytes.decode("utf-8", errors="ignore")
        match = re.search(r"(ORD[-\s]?\d+)", decoded, flags=re.IGNORECASE) or re.search(
            r"(ORD[-\s]?\d+)", file_name, flags=re.IGNORECASE
        )
        order_id = match.group(1).replace(" ", "") if match else "not found"
        return f"PDF received. Mock extraction result: order_id={order_id}."

    return f"File '{file_name}' received."


@app.post("/chat", response_model=ChatResponse)
async def chat(request: Request, auth: dict = Depends(verify_api_key)):
    try:
        parsed = await _parse_chat_input(request, auth)
        user_id = parsed["user_id"]

        session_id = parsed["session_id"]
        if not session_id:
            session = await store.create_session(user_id=user_id)
            session_id = session["session_id"]

        message = parsed["message"] or "Hi"
        file_signal = await _mock_file_signal(parsed["file"], parsed["input_type"])
        if file_signal:
            message = f"{message}\n{file_signal}" if message else file_signal

        result = await asyncio.wait_for(
            run_chat_pipeline(user_id=user_id, session_id=session_id, query=message),
            timeout=AGENT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        result = {
            "message": "I'm taking too long to respond. Please try again.",
            "action": "provide_information",
            "status": "failure",
            "response": "I'm taking too long to respond. Please try again.",
            "route": "TIMEOUT",
            "session_id": "",
            "reason_trace": {"error": "timeout"},
            "audio_bytes": None,
            "chunk_batches": [],
        }
    except Exception as exc:
        logger.exception("chat request failed: %s", exc)
        result = {
            "message": "I hit a temporary issue, but I can still help. Please try again.",
            "action": "provide_information",
            "status": "failure",
            "response": "I hit a temporary issue, but I can still help. Please try again.",
            "route": "FALLBACK",
            "session_id": "",
            "reason_trace": {"error": str(exc)},
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=settings.port, reload=False)

