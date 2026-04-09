"""
voice/tts.py — Async TTS returning bytes directly
Primary  : Edge TTS (async, Microsoft Neural, free)
Fallback : gTTS (sync via executor)
Returns  : bytes (mp3) — no temp files, no file paths, no serving needed
"""

import asyncio
import re
import io
from core.config import Config
from core.logger import get_logger

logger = get_logger("novamind.tts")

# Voice response cap — keep it shorter for voice UX
VOICE_MAX_CHARS = 400


def _detect_language(text: str) -> str:
    devanagari  = re.compile(r'[\u0900-\u097F]')
    hindi_chars = len(devanagari.findall(text))
    return "hi" if len(text) > 0 and hindi_chars / len(text) > 0.1 else "en"


def _clean_for_voice(text: str) -> str:
    """
    Strip markdown and trim for voice output.
    Voice responses must be short and clean — no code, no markdown.
    """
    text = re.sub(r"#{1,6}\s+",             "",   text)
    text = re.sub(r"\*{1,3}(.*?)\*{1,3}",  r"\1", text)
    text = re.sub(r"^\s*[-*•]\s+",          "",   text, flags=re.MULTILINE)
    text = re.sub(r"```.*?```",             "",   text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`",             r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\s+",                   " ",   text).strip()

    # trim to voice-friendly length at sentence boundary
    if len(text) > VOICE_MAX_CHARS:
        trimmed = text[:VOICE_MAX_CHARS]
        last    = max(trimmed.rfind("."), trimmed.rfind("!"), trimmed.rfind("?"))
        text    = trimmed[:last + 1] if last > VOICE_MAX_CHARS * 0.5 else trimmed
    return text


class TTSProcessor:
    """
    Returns audio as bytes — no file paths, no file serving.
    Caller encodes to base64 and sends in JSON response.
    """

    async def synthesize_bytes(
        self,
        text:     str,
        language: str = None,
    ) -> dict:
        """
        Convert text to MP3 bytes.

        Returns:
            {
                "audio_bytes": bytes | None,
                "language":    "en" | "hi",
                "method":      "edge" | "gtts" | "none",
                "content":     cleaned text,
            }

        Edge cases:
        - Empty text        → None
        - Edge TTS fails    → gTTS fallback
        - Both fail         → None (caller shows text only)
        - Hindi text        → hi-IN-SwaraNeural
        """
        if not text or not text.strip():
            return {"audio_bytes": None, "language": "en", "method": "none", "content": ""}

        clean = _clean_for_voice(text)
        lang  = language or _detect_language(clean)
        voice = Config.TTS_HINDI_VOICE if lang == "hi" else Config.TTS_ENGLISH_VOICE

        # Step 1 — Edge TTS (async, best quality)
        audio_bytes = await self._edge_tts_bytes(clean, voice)
        if audio_bytes:
            logger.info(f"Edge TTS: {len(audio_bytes)} bytes (voice={voice})")
            return {"audio_bytes": audio_bytes, "language": lang, "method": "edge", "content": clean}

        logger.warning("Edge TTS failed — trying gTTS fallback.")

        # Step 2 — gTTS (sync via executor)
        gtts_lang   = "hi" if lang == "hi" else "en"
        audio_bytes = await asyncio.get_running_loop().run_in_executor(
            None, self._gtts_bytes_sync, clean, gtts_lang
        )
        if audio_bytes:
            logger.info(f"gTTS: {len(audio_bytes)} bytes (lang={gtts_lang})")
            return {"audio_bytes": audio_bytes, "language": lang, "method": "gtts", "content": clean}

        logger.warning("All TTS failed — returning text only.")
        return {"audio_bytes": None, "language": lang, "method": "none", "content": clean}

    # ── keep old synthesize() for backward compat ─────────────
    async def synthesize(self, text: str, language: str = None, save_path: str = None) -> dict:
        """
        Legacy interface — still works, saves to temp file.
        New code should use synthesize_bytes().
        """
        result = await self.synthesize_bytes(text, language)
        if result["audio_bytes"] and save_path:
            with open(save_path, "wb") as f:
                f.write(result["audio_bytes"])
            return {"type": "audio", "content": text, "audio_path": save_path, "language": result["language"]}

        if result["audio_bytes"]:
            import tempfile, os
            tmp  = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
            path = tmp.name
            tmp.close()
            with open(path, "wb") as f:
                f.write(result["audio_bytes"])
            return {"type": "audio", "content": text, "audio_path": path, "language": result["language"]}

        return {"type": "text", "content": text, "audio_path": None, "language": result["language"]}

    # ── private ───────────────────────────────────────────────

    async def _edge_tts_bytes(self, text: str, voice: str) -> bytes:
        """Edge TTS → bytes in memory. No temp file."""
        try:
            import edge_tts

            chunks = []
            communicate = edge_tts.Communicate(
                text  = text,
                voice = voice,
                rate  = "-5%",    # slightly slower = more natural
                pitch = "+0Hz",   # neutral pitch
            )
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])

            if not chunks:
                raise ValueError("Edge TTS returned no audio chunks")

            return b"".join(chunks)

        except Exception as e:
            logger.error(f"Edge TTS bytes error: {e}")
            return b""

    def _gtts_bytes_sync(self, text: str, lang: str) -> bytes:
        """gTTS → bytes in memory. Sync — called via executor."""
        try:
            from gtts import gTTS
            buf = io.BytesIO()
            gTTS(text=text, lang=lang, slow=False).write_to_fp(buf)
            buf.seek(0)
            return buf.read()
        except Exception as e:
            logger.error(f"gTTS bytes error: {e}")
            return b""