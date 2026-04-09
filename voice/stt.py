"""
voice/stt.py — Async Speech-to-Text
Primary  : Groq Whisper (async httpx — no event loop blocking)
Fallback : Google SpeechRecognition (WAV only, sync run in executor)
"""

import io
import asyncio
from core.logger import get_logger
from core.config import Config

logger = get_logger("novamind.stt")

_MIME_MAP = {
    ".mp3":  "audio/mpeg",
    ".mp4":  "audio/mp4",
    ".mpeg": "audio/mpeg",
    ".mpga": "audio/mpeg",
    ".m4a":  "audio/mp4",
    ".wav":  "audio/wav",
    ".webm": "audio/webm",
    ".ogg":  "audio/ogg",
}


class STTProcessor:
    """
    Fully async STT — never blocks the event loop.

    Chain:
        Groq Whisper (httpx async) → Google STT (executor) → ""
    """

    async def transcribe(
        self,
        audio_data:   bytes = None,
        audio_path:   str   = None,
        audio_format: str   = "webm",
    ) -> str:
        """
        Convert audio bytes to text. Fully async.

        Edge cases:
        - No audio provided    → return ""
        - Groq fails/empty     → Google fallback
        - Google fails         → return ""
        - webm for Google      → converted to wav bytes in executor
        - API key missing      → skip Groq, go straight to Google
        """
        if not audio_data and not audio_path:
            logger.warning("STT: no audio provided.")
            return ""

        # load from file if path given
        if audio_path and not audio_data:
            import os
            audio_format = os.path.splitext(audio_path)[1].lstrip(".") or audio_format
            with open(audio_path, "rb") as f:
                audio_data = f.read()

        if not audio_data:
            return ""

        # Step 1 — Groq Whisper (async)
        if Config.GROQ_API_KEY:
            text = await self._groq_whisper_async(audio_data, audio_format)
            if text:
                logger.info(f"Groq Whisper: '{text[:80]}'")
                return text
            logger.warning("Groq Whisper returned empty — trying Google STT.")

        # Step 2 — Google STT (blocking, run in executor so async is safe)
        text = await asyncio.get_running_loop().run_in_executor(
            None, self._google_stt_sync, audio_data, audio_format
        )
        if text:
            logger.info(f"Google STT: '{text[:80]}'")
            return text

        logger.error("All STT methods failed.")
        return ""

    async def _groq_whisper_async(self, audio_data: bytes, audio_format: str) -> str:
        """
        Async Groq Whisper call using httpx.
        No blocking — safe inside FastAPI async handlers.
        """
        try:
            import httpx

            ext      = f".{audio_format.lstrip('.')}"
            mime     = _MIME_MAP.get(ext, "audio/webm")
            filename = f"audio{ext}"

            payload = {"model": Config.GROQ_STT_MODEL}
            if Config.GROQ_STT_LANGUAGE:
                payload["language"] = Config.GROQ_STT_LANGUAGE

            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    Config.GROQ_STT_URL,
                    headers = {"Authorization": f"Bearer {Config.GROQ_API_KEY}"},
                    files   = {"file": (filename, audio_data, mime)},
                    data    = payload,
                )
                response.raise_for_status()
                return response.json().get("text", "").strip()

        except Exception as e:
            logger.error(f"Groq Whisper async error: {e}")
            return ""

    def _google_stt_sync(self, audio_data: bytes, audio_format: str) -> str:
        """
        Synchronous Google STT — run via executor, never called directly in async.

        Edge case: Google STT does NOT support webm.
        We attempt to convert webm→wav using pydub if installed.
        If pydub not available, we skip Google and return "".
        """
        try:
            import speech_recognition as sr

            # Google STT only handles wav/aiff/flac — convert if needed
            if audio_format.lower() in ("webm", "ogg", "mp4", "mpeg", "mpga"):
                audio_data = _convert_to_wav(audio_data, audio_format)
                if not audio_data:
                    logger.warning("Google STT: could not convert audio format — skipping.")
                    return ""

            recognizer = sr.Recognizer()
            with sr.AudioFile(io.BytesIO(audio_data)) as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.2)
                audio = recognizer.record(source)

            for lang in ["en-IN", "hi-IN"]:
                try:
                    text = recognizer.recognize_google(audio, language=lang)
                    if text:
                        return text.strip()
                except sr.UnknownValueError:
                    continue
                except Exception as e:
                    logger.error(f"Google STT ({lang}): {e}")
                    continue

            return ""

        except Exception as e:
            logger.error(f"Google STT sync failed: {e}")
            return ""


def _convert_to_wav(audio_data: bytes, src_format: str) -> bytes:
    """
    Convert audio bytes to WAV using pydub (requires ffmpeg).
    Returns None if conversion fails.
    """
    try:
        from pydub import AudioSegment
        import io as _io

        fmt_map = {"webm": "webm", "ogg": "ogg", "mp4": "mp4",
                   "mpeg": "mp3", "mpga": "mp3"}
        fmt = fmt_map.get(src_format.lower(), src_format)

        seg = AudioSegment.from_file(_io.BytesIO(audio_data), format=fmt)
        out = _io.BytesIO()
        seg.export(out, format="wav")
        return out.getvalue()

    except Exception as e:
        logger.warning(f"Audio conversion failed: {e}")
        return None