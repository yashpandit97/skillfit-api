"""
Centralized LLM service. All LLM calls must go through this layer.
- Retry logic, timeout, structured JSON enforcement.
- Logging of prompt/response and token estimation.
- No model names in route files — use config.
"""
import json
import logging
import re
import time
from typing import Any

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from backend.config import get_settings
from backend.utils.logging_config import log_llm_call
from backend.utils.json_extract import extract_json_from_llm_response

logger = logging.getLogger(__name__)


def _schema_to_format_description(schema: type[BaseModel]) -> str:
    """Build a short key-and-type description so the model knows the shape without echoing full JSON schema."""
    js = schema.model_json_schema()
    parts = []
    for key, prop in (js.get("properties") or {}).items():
        if key.startswith("_"):
            continue
        t = prop.get("type", "string")
        if t == "array":
            parts.append(f'"{key}": array of strings')
        elif t == "string":
            parts.append(f'"{key}": string')
        elif t == "integer":
            parts.append(f'"{key}": number')
        else:
            parts.append(f'"{key}"')
    return ", ".join(parts) if parts else "same keys as described above"


def _approx_tokens(text: str) -> int:
    """Rough token estimate (~4 chars per token for English)."""
    return max(1, len(text) // 4)


def _is_rate_limit_error(exc: Exception) -> bool:
    msg = str(exc)
    return "429" in msg or "RESOURCE_EXHAUSTED" in msg


def _rate_limit_wait_seconds(exc: Exception, attempt: int) -> float:
    """Parse RetryInfo from Gemini error, else exponential backoff."""
    msg = str(exc)
    match = re.search(r"retry in ([\d.]+)s", msg, re.IGNORECASE)
    if match:
        return float(match.group(1)) + 0.5
    return min(60.0, 2.0 ** attempt)


class GeminiLLMService:
    """
    Pluggable Gemini client. Model and API key from config.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: int | None = None,
        max_retries: int | None = None,
    ):
        s = get_settings()
        self.api_key = api_key or s.gemini_api_key
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is required")
        self.model = model or s.gemini_model
        self.timeout = timeout_seconds or s.gemini_timeout_seconds
        self.max_retries = max_retries or s.gemini_max_retries
        self._client = ChatGoogleGenerativeAI(
            model=self.model,
            google_api_key=self.api_key,
            timeout=self.timeout,
        )

    def invoke(
        self,
        system_prompt: str,
        user_prompt: str,
        stage: str = "llm",
    ) -> str:
        """
        Raw text completion with retries and logging.
        """
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
        prompt_preview = f"{system_prompt[:200]} ... | {user_prompt[:200]} ..."
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                start = time.perf_counter()
                response = self._client.invoke(messages)
                duration = time.perf_counter() - start
                content = response.content if hasattr(response, "content") else str(response)
                log_llm_call(
                    logger,
                    stage=stage,
                    prompt_preview=prompt_preview,
                    response_preview=content,
                    token_estimate=_approx_tokens(user_prompt) + _approx_tokens(content),
                    duration_seconds=duration,
                )
                return content
            except Exception as e:
                last_error = e
                if _is_rate_limit_error(e) and attempt < self.max_retries - 1:
                    wait = _rate_limit_wait_seconds(e, attempt)
                    logger.warning(
                        "LLM rate limited (attempt %s/%s), retrying in %.1fs: %s",
                        attempt + 1,
                        self.max_retries,
                        wait,
                        e,
                    )
                    time.sleep(wait)
                    continue
                logger.warning("LLM attempt %s/%s failed: %s", attempt + 1, self.max_retries, e)
        raise last_error or RuntimeError("LLM invocation failed")

    def invoke_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
        stage: str = "llm_structured",
    ) -> BaseModel:
        """
        Enforce JSON schema. Retries on parse/validation failure.
        Uses a short format description instead of full JSON schema to avoid models echoing the schema.
        """
        format_desc = _schema_to_format_description(schema)
        instruction = (
            "Respond with a single JSON object only. No markdown, no code fences. "
            f"Use exactly these keys: {format_desc}"
        )
        full_system = f"{system_prompt}\n\n{instruction}"
        for attempt in range(self.max_retries):
            try:
                raw = self.invoke(full_system, user_prompt, stage=stage)
                data = extract_json_from_llm_response(raw, skip_schema_like=True)
                return schema.model_validate(data)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning("Structured parse attempt %s failed: %s", attempt + 1, e)
                if attempt == self.max_retries - 1:
                    raise
        raise RuntimeError("Structured LLM response could not be validated")

    def invoke_json_dict(
        self,
        system_prompt: str,
        user_prompt: str,
        stage: str = "llm_json",
    ) -> dict[str, Any]:
        """Return parsed JSON dict. Uses robust extraction (first object, strip markdown, fix trailing commas)."""
        raw = self.invoke(
            system_prompt + "\nRespond with a single JSON object only. No markdown.",
            user_prompt,
            stage=stage,
        )
        return extract_json_from_llm_response(raw)

    def stream(
        self,
        system_prompt: str,
        user_prompt: str,
        stage: str = "llm_stream",
    ):
        """Stream LLM response token-by-token. Yields content chunks (strings)."""
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                for chunk in self._client.stream(messages):
                    content = chunk.content if hasattr(chunk, "content") else str(chunk)
                    if content:
                        yield content
                return
            except Exception as e:
                last_error = e
                if _is_rate_limit_error(e) and attempt < self.max_retries - 1:
                    wait = _rate_limit_wait_seconds(e, attempt)
                    logger.warning(
                        "LLM stream rate limited (attempt %s/%s), retrying in %.1fs",
                        attempt + 1,
                        self.max_retries,
                        wait,
                    )
                    time.sleep(wait)
                    continue
                raise
        raise last_error or RuntimeError("LLM stream failed")


def get_llm_service(
    api_key: str | None = None,
    model: str | None = None,
) -> GeminiLLMService:
    """Factory for dependency injection. Use config defaults."""
    return GeminiLLMService(api_key=api_key, model=model)
