from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _split_csv(value: str, fallback: list[str]) -> list[str]:
    raw = [item.strip() for item in value.split(",") if item.strip()]
    return raw or fallback


@dataclass(slots=True)
class Settings:
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "llama3-70b-8192")
    groq_base_url: str = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.2"))
    llm_max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "400"))
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "12"))
    app_version: str = "0.1.0-mvp"
    default_api_key: str = os.getenv("DEFAULT_API_KEY", "nm_demo_key")
    mysql_host: str = os.getenv("MYSQL_HOST", "localhost")
    mysql_user: str = os.getenv("MYSQL_USER", "root")
    mysql_password: str = os.getenv("MYSQL_PASSWORD", "")
    mysql_db: str = os.getenv("MYSQL_DB", "ecommerce_ai")
    rag_data_path: str = os.getenv("RAG_DATA_PATH", "./data/policies")
    port: int = int(os.getenv("PORT", "8000"))
    cors_origins: list[str] = field(
        default_factory=lambda: _split_csv(
            os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173"),
            ["http://localhost:3000"],
        )
    )


settings = Settings()

