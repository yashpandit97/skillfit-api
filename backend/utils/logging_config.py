"""
Centralized logging. All LLM prompt/response logging goes through here.
"""
import logging
import sys
from typing import Any

# Structured log format for production
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )
    # Reduce noise from third-party libs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def log_llm_call(
    logger: logging.Logger,
    stage: str,
    prompt_preview: str,
    response_preview: str,
    token_estimate: int | None = None,
    duration_seconds: float | None = None,
) -> None:
    """Log LLM invocation for audit and debugging. No PII in production logs."""
    extra: dict[str, Any] = {
        "stage": stage,
        "prompt_preview": prompt_preview[:500] + "..." if len(prompt_preview) > 500 else prompt_preview,
        "response_preview": response_preview[:500] + "..." if len(response_preview) > 500 else response_preview,
    }
    if token_estimate is not None:
        extra["token_estimate"] = token_estimate
    if duration_seconds is not None:
        extra["duration_seconds"] = round(duration_seconds, 2)
    logger.info("LLM call: %s", stage, extra=extra)
