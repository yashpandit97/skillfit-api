"""
Environment-based configuration. No secrets in code.
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# skillfit-api/skillfit-api/.env — stable regardless of process cwd (e.g. backend/run.py).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "AI Resume Intelligence"
    debug: bool = False

    # Gemini — pluggable; do not hardcode model in routes
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_timeout_seconds: int = 120
    gemini_max_retries: int = 3

    # JWT
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 days

    # Firebase Auth (ID token verification)
    firebase_project_id: str = "skillfit-e06fe"

    # Database — default SQLite for local dev (no PostgreSQL required); set DATABASE_URL for PostgreSQL
    database_url: str = "sqlite:///./resume_intel.db"

    # Redis — scaffold for caching
    redis_url: str = "redis://localhost:6379/0"

    # Rate limiting
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60

    # Resume
    resume_max_pages: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()
