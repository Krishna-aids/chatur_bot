from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(slots=True)
class SDKConfig:
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    model_8b: str = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
    model_70b: str = os.getenv("GROQ_MODEL_70B", "llama-3.3-70b-versatile")
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.4"))
    llm_max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "512"))
    max_agent_steps: int = int(os.getenv("MAX_AGENT_STEPS", "3"))
    max_text_length: int = int(os.getenv("MAX_TEXT_LENGTH", "2000"))
    groq_stt_url: str = "https://api.groq.com/openai/v1/audio/transcriptions"
    groq_stt_model: str = "whisper-large-v3"
    groq_stt_language: str = os.getenv("GROQ_STT_LANGUAGE", "")
    tts_english_voice: str = os.getenv("TTS_ENGLISH_VOICE", "en-US-AriaNeural")
    tts_hindi_voice: str = os.getenv("TTS_HINDI_VOICE", "hi-IN-SwaraNeural")
    tts_max_chars: int = int(os.getenv("TTS_MAX_CHARS", "400"))
