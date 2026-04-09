from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(slots=True)
class Settings:
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = "llama3-70b-8192"
    groq_base_url: str = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    llm_temperature: float = 0.2
    llm_max_tokens: int = 400
    llm_timeout_seconds: float = 12.0
    app_version: str = "0.1.0-mvp"
    default_api_key: str = os.getenv("DEFAULT_API_KEY", "nm_demo_key")


settings = Settings()

