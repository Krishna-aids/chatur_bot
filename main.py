"""
server/main.py — NovaMind v2 FastAPI Server (Supabase edition)
Storage: Supabase only. No MySQL. No Chroma.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.logger import get_logger, setup_logging
from core.config import Config
from server.auth import verify_api_key, router as auth_router
from server.schemas import (
    NewSessionRequest, NewSessionResponse,
    ChatRequest, ChatResponse,
    OrchestratedChatRequest, OrchestratedChatResponse,
    UploadResponse,
    HistoryResponse, MessageRecord,
)
from chatbot import Chatbot
from core.orchestrator import ChatOrchestrator

logger        = get_logger("novamind.server")
AGENT_TIMEOUT = 30
MAX_FILE_SIZE = 10 * 1024 * 1024

_bot: Chatbot = None
_orchestrator: ChatOrchestrator = None


# ── Lifespan ──────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bot, _orchestrator
    setup_logging()
    logger.info("Starting NovaMind v2 (Supabase)...")

    # Startup env validation — fail fast with clear message
    missing = []
    if not os.getenv("GROQ_API_KEY"):      missing.append("GROQ_API_KEY")
    if not os.getenv("SUPABASE_URL"):      missing.append("SUPABASE_URL")
    if not os.getenv("SUPABASE_SERVICE_KEY"): missing.append("SUPABASE_SERVICE_KEY")
    if missing:
        logger.error(
            f"STARTUP FAILED — missing required env vars: {missing}\n"
            f"Set these in .env or Hugging Face Space Secrets before starting."
        )
        # Don't crash — let health endpoint report degraded state
        # Server starts but all requests will fail gracefully

    _bot = Chatbot()
    _orchestrator = ChatOrchestrator(chatbot_instance=_bot)

    # Pre-warm MiniLM embedder in background — first request won't pay cold start
    async def _prewarm():
        try:
            logger.info("Pre-warming MiniLM embedder...")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _bot.retriever._get_embedder)
            logger.info("MiniLM pre-warm complete — embedder ready.")
        except Exception as e:
            logger.warning(f"MiniLM pre-warm failed (non-fatal): {e}")

    asyncio.create_task(_prewarm())
    yield
    logger.info("NovaMind v2 shutting down.")


app = FastAPI(
    title       = "NovaMind v2 API",
    description = "Memory-first AI workspace",
    version     = "2.0.0",
    lifespan    = lifespan,
)
app.include_router(auth_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)


def _guard():
    if not _bot:
        raise HTTPException(status_code=503, detail="Chatbot not initialized.")


def _guard_orchestrator():
    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized.")


# ── /health ───────────────────────────────────────────────────

@app.get("/health")
async def health():
    supabase_ok = False
    try:
        from storage.supabase_store import _get_client
        _get_client().table("sessions").select("session_id").limit(1).execute()
        supabase_ok = True
    except Exception as e:
        logger.warning(f"Supabase health check failed: {e}")

    return {
        "status":    "ok" if supabase_ok and _bot else "degraded",
        "version":   "2.0.0",
        "supabase":  supabase_ok,
        "bot_ready": bool(_bot),
        "model_8b":  Config.GROQ_MODEL,
        "model_70b": Config.GROQ_MODEL_70B,
        "llm":       "configured" if Config.GROQ_API_KEY else "missing GROQ_API_KEY",
    }


# ── /session/new ──────────────────────────────────────────────

@app.post("/session/new", response_model=NewSessionResponse)
async def new_session(
    request: NewSessionRequest,
    auth:    dict = Depends(verify_api_key),
):
    _guard()
    import uuid
    session_id = str(uuid.uuid4())
    user_id    = auth.get("user_id") or request.user_id or "anonymous"
    try:
        from storage.supabase_store import create_session
        create_session(user_id, session_id)
        logger.info(f"Session created: {session_id} for user={user_id}")
    except Exception as e:
        logger.warning(f"Session create failed: {e}")
    from datetime import datetime

    return NewSessionResponse(
            session_id=session_id,
            user_id=user_id,
            created_at=datetime.utcnow().isoformat()
        )


# ── /chat ─────────────────────────────────────────────────────

@app.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    auth:    dict = Depends(verify_api_key),
):
    _guard()
    user_id = auth.get("user_id") or "anonymous"
    try:
        result = await asyncio.wait_for(
            _bot.chat(
                query        = request.query,
                session_id   = request.session_id,
                user_id      = user_id,
                input_type   = request.input_type,
                output_voice = request.output_voice,
                language     = request.language,
            ),
            timeout = AGENT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(f"[{request.session_id}] Chat timed out")
        result = {
            "response":      "I'm taking too long to respond. Please try again.",
            "route":         "TIMEOUT",
            "session_id":    request.session_id,
            "audio_bytes":   None,
            "chunk_batches": [],
            "reason_trace":  {"error": "timeout"},
        }
    return ChatResponse(**result)


@app.post("/orchestrator/chat", response_model=OrchestratedChatResponse)
async def orchestrated_chat(
    request: OrchestratedChatRequest,
    auth: dict = Depends(verify_api_key),
):
    """
    Example unified entrypoint for text, voice, and file inputs.

    Voice/file bytes should be sent inside request.input.data as base64 strings.
    """
    _guard()
    _guard_orchestrator()
    user_id = auth.get("user_id") or "anonymous"

    try:
        result = await asyncio.wait_for(
            _orchestrator.handle(
                payload=request.input.model_dump(),
                session_id=request.session_id,
                user_id=user_id,
                output_voice=request.output_voice,
                language=request.language,
            ),
            timeout=AGENT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(f"[{request.session_id}] Orchestrated chat timed out")
        result = {
            "text": "I'm taking too long to respond. Please try again.",
            "route": "TIMEOUT",
            "session_id": request.session_id,
            "mode": request.input.type,
            "audio_bytes": None,
            "chunk_batches": [],
            "reason_trace": {"error": "timeout"},
            "document_result": None,
            "document_id": None,
            "tools_loaded": [],
            "tts_meta": None,
        }
    return OrchestratedChatResponse(**result)


# ── /chat/stream ──────────────────────────────────────────────

@app.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    auth:    dict = Depends(verify_api_key),
):
    """
    Streaming chat — Server-Sent Events.

    FIX: Runs the FULL Chatbot.chat() pipeline (InputGuard → IntentClassifier
    → MemoryRetriever → Tool execution → LLM) then streams the final response
    word-by-word. The OLD implementation called _bot.llm.stream() directly,
    bypassing all tool routing and leaking raw TOOL_CALL text to the UI.

    Protocol:
      Route event: "data: [ROUTE]<CHAT|RAG|TOOL|AGENT|MEMORY>\\n\\n"
      Each token:  "data: <word> \\n\\n"
      Done:        "data: [DONE]\\n\\n"
      Error:       "data: [ERROR] <message>\\n\\n"
    """
    _guard()
    user_id = auth.get("user_id") or "anonymous"

    async def generate():
        try:
            # Run the FULL pipeline — tools execute here, no leaking
            result = await asyncio.wait_for(
                _bot.chat(
                    query        = request.query,
                    session_id   = request.session_id,
                    user_id      = user_id,
                    input_type   = request.input_type,
                    output_voice = False,
                    language     = request.language,
                ),
                timeout = AGENT_TIMEOUT,
            )

            response = result.get("response", "")
            route    = result.get("route", "CHAT")

            if not response:
                yield "data: [ERROR] Empty response from pipeline.\n\n"
                return

            # Emit route badge first so the frontend can update it immediately
            yield f"data: [ROUTE]{route}\n\n"

            # Simulate streaming word-by-word from the final response
            words = response.split(" ")
            for i, word in enumerate(words):
                chunk = word if i == len(words) - 1 else word + " "
                yield f"data: {chunk}\n\n"
                await asyncio.sleep(0.01 if len(words) < 50 else 0.007)

            yield "data: [DONE]\n\n"

        except asyncio.TimeoutError:
            logger.warning(f"[{request.session_id}] Stream timed out")
            yield "data: I'm taking too long to respond. Please try again.\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            logger.error(f"Stream error: {e}")
            yield f"data: [ERROR] {e}\n\n"

    return StreamingResponse(
        generate(),
        media_type = "text/event-stream",
        headers    = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── /upload ───────────────────────────────────────────────────

@app.post("/upload", response_model=UploadResponse)
async def upload_document(
    file:    UploadFile = File(...),
    user_id: str        = Form(default="anonymous"),
    source:  str        = Form(default=""),
    auth:    dict       = Depends(verify_api_key),
):
    _guard()
    filename  = file.filename.lower() if file.filename else "unknown"
    raw_bytes = await file.read()

    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(raw_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large. Max 10MB.")

    text = ""
    try:
        if filename.endswith(".txt"):
            text = raw_bytes.decode("utf-8", errors="ignore")
        elif filename.endswith(".pdf"):
            import fitz
            text = "\n".join(
                page.get_text()
                for page in fitz.open(stream=raw_bytes, filetype="pdf")
            )
        elif filename.endswith((".doc", ".docx")):
            import docx, io
            text = "\n".join(
                p.text for p in docx.Document(io.BytesIO(raw_bytes)).paragraphs
            )
        else:
            raise HTTPException(
                status_code=400,
                detail="Unsupported format. Use TXT, PDF, or DOCX."
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not extract text: {e}")

    if not text.strip():
        raise HTTPException(status_code=400, detail="No text could be extracted.")

    doc_user_id = auth.get("user_id") or user_id
    doc_name    = source or filename

    # Generate doc_id immediately so frontend can poll status
    import hashlib
    doc_id = hashlib.md5(f"{doc_user_id}:{doc_name}:{text[:200]}".encode()).hexdigest()

    # Mark as processing in Supabase immediately
    try:
        from storage.supabase_store import _get_client
        _get_client().table("document_memory").upsert({
            "user_id":    doc_user_id,
            "doc_id":     doc_id,
            "filename":   doc_name,
            "summary":    "__processing__",
            "key_points": [],
            "qa_pairs":   [],
            "tags":       [],
        }, on_conflict="user_id,doc_id").execute()
    except Exception as e:
        logger.warning(f"Could not mark doc as processing: {e}")

    # Invalidate cache immediately so next query fetches fresh data from Supabase
    try:
        from core.cache_store import cache_invalidate
        cache_invalidate(doc_user_id)
    except Exception as e:
        logger.warning(f"cache_invalidate failed (non-fatal): {e}")

    # Process in background — user gets instant response
    async def _process_in_background():
        try:
            logger.info(f"Background processing: '{doc_name}' for '{doc_user_id}'")
            result   = await _bot.process_document(text, doc_name, doc_user_id)
            qa_count = len(result.get("qa_pairs", []))
            logger.info(f"Background done: '{doc_name}' → {qa_count} Q&A pairs")
        except Exception as e:
            logger.error(f"Background doc processing failed: '{doc_name}': {e}")
            # Mark as failed in Supabase
            try:
                from storage.supabase_store import _get_client
                _get_client().table("document_memory").update({
                    "summary": "__failed__"
                }).eq("user_id", doc_user_id).eq("doc_id", doc_id).execute()
            except Exception:
                pass

    asyncio.create_task(_process_in_background())

    word_count = len(text.split())
    logger.info(f"Upload received: '{doc_name}' ({word_count} words) — processing in background")

    return UploadResponse(
        message      = f"'{filename}' received ({word_count:,} words). Processing in background — ready in ~15s.",
        chunks_added = 0,
        total_chunks = 0,
    )


# ── /upload/status/{doc_id} ──────────────────────────────────

@app.get("/upload/status/{doc_id}")
async def upload_status(
    doc_id: str,
    auth:   dict = Depends(verify_api_key),
):
    """
    Poll document processing status.
    Returns: processing | ready | failed | not_found
    Frontend polls this every 3s after upload until status == ready.
    """
    user_id = auth.get("user_id") or "anonymous"
    try:
        from storage.supabase_store import _get_client
        res = (
            _get_client()
            .table("document_memory")
            .select("summary, qa_pairs, filename")
            .eq("user_id", user_id)
            .eq("doc_id",  doc_id)
            .single()
            .execute()
        )
        if not res.data:
            return {"status": "not_found", "doc_id": doc_id}

        d       = res.data
        summary = d.get("summary", "")
        qa      = d.get("qa_pairs") or []

        if summary == "__processing__":
            return {"status": "processing", "doc_id": doc_id, "filename": d.get("filename","")}
        if summary == "__failed__":
            return {"status": "failed",     "doc_id": doc_id, "filename": d.get("filename","")}

        return {
            "status":   "ready",
            "doc_id":   doc_id,
            "filename": d.get("filename", ""),
            "qa_count": len(qa),
            "summary":  summary[:200] + "..." if len(summary) > 200 else summary,
        }
    except Exception as e:
        logger.error(f"upload/status failed: {e}")
        return {"status": "error", "detail": str(e)}


# ── /history/{session_id} ─────────────────────────────────────

@app.get("/history/{session_id}", response_model=HistoryResponse)
async def get_history_endpoint(
    session_id: str,
    auth:       dict = Depends(verify_api_key),
):
    user_id = auth.get("user_id") or "anonymous"
    from storage.supabase_store import get_history
    rows = get_history(user_id, session_id, limit=100)
    return HistoryResponse(
        session_id = session_id,
        messages   = [MessageRecord(**r) for r in rows],
        count      = len(rows),
    )


# ── /voice/chat ───────────────────────────────────────────────

@app.post("/voice/chat")
async def voice_chat(
    audio:      UploadFile = File(...),
    session_id: str        = Form(...),
    language:   str        = Form(default=""),
    auth:       dict       = Depends(verify_api_key),
):
    import base64
    _guard()
    user_id   = auth.get("user_id") or "anonymous"
    raw_bytes = await audio.read()

    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file.")
    if len(raw_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="Audio too large. Max 10MB.")

    audio_format = (
        audio.filename.split(".")[-1].lower()
        if audio.filename and "." in audio.filename else "webm"
    )

    # STT
    try:
        transcribed = await asyncio.wait_for(
            _bot.transcribe(raw_bytes, audio_format=audio_format),
            timeout=15.0,
        )
    except asyncio.TimeoutError:
        return {"transcription": "", "response": "Transcription timed out.",
                "route": "ERROR", "session_id": session_id,
                "audio_b64_first": None, "chunk_batches": []}

    if not transcribed:
        return {"transcription": "", "response": "Couldn't understand audio. Please try again.",
                "route": "ERROR", "session_id": session_id,
                "audio_b64_first": None, "chunk_batches": []}

    logger.info(f"[{session_id}] Transcribed: '{transcribed[:80]}'")

    # LLM + TTS
    try:
        result = await asyncio.wait_for(
            _bot.chat(
                query        = transcribed,
                session_id   = session_id,
                user_id      = user_id,
                input_type   = "voice",
                output_voice = True,
                language     = language or None,
            ),
            timeout = AGENT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        result = {"response": "Taking too long. Please try again.", "route": "TIMEOUT",
                  "audio_bytes": None, "chunk_batches": [], "reason_trace": {"error": "timeout"}}

    raw_first = result.get("audio_bytes") or b""
    return {
        "transcription":   transcribed,
        "response":        result.get("response", ""),
        "route":           result.get("route", "CHAT"),
        "session_id":      session_id,
        "audio_b64_first": base64.b64encode(raw_first).decode() if raw_first else None,
        "chunk_batches":   result.get("chunk_batches", []),
        "reason_trace":    result.get("reason_trace"),
    }


# ── /voice/chunk ──────────────────────────────────────────────

class VoiceChunkRequest(BaseModel):
    texts:    list[str]
    language: str = ""

@app.post("/voice/chunk")
async def voice_chunk(
    request: VoiceChunkRequest,
    auth:    dict = Depends(verify_api_key),
):
    import base64
    from voice.speech_formatter import merge_audio_bytes
    _guard()

    texts = [t for t in request.texts if t and t.strip()]
    if not texts:
        raise HTTPException(status_code=400, detail="No texts provided.")

    try:
        tasks   = [_bot.tts.synthesize_bytes(t, language=request.language or None)
                   for t in texts]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        parts   = [r.get("audio_bytes") or b""
                   for r in results if not isinstance(r, Exception)]
        merged  = merge_audio_bytes([p for p in parts if p])
        return {"audio_b64": base64.b64encode(merged).decode() if merged else None}
    except Exception as e:
        logger.error(f"voice/chunk failed: {e}")
        raise HTTPException(status_code=500, detail="TTS generation failed.")


# ── WhatsApp webhook ──────────────────────────────────────────

from server.webhook import router as whatsapp_router
app.include_router(whatsapp_router)
