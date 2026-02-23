"""
Environment-based configuration. No secrets in code.
"""
import os
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    app_name: str = "AI Resume Intelligence"
    debug: bool = False

    # Ollama — pluggable; do not hardcode model in routes
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = os.getenv("OLLAMA_MODEL", "llama3.2")
    ollama_timeout_seconds: int = 120
    ollama_max_retries: int = 3

    # JWT
    jwt_secret: str = os.getenv("JWT_SECRET", "change-me-in-production")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 days

    # Database — default SQLite for local dev (no PostgreSQL required); set DATABASE_URL for PostgreSQL
    database_url: str = os.getenv(
        "DATABASE_URL",
        "sqlite:///./resume_intel.db"
    )

    # Redis — scaffold for caching
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # Rate limiting
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60

    # Resume
    resume_max_pages: int = 2

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
