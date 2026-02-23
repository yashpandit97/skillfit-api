"""
Robust JSON extraction from LLM output. Handles markdown, trailing commas, extra text.
"""
import json
import re
from typing import Any


def _strip_markdown_code_block(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def _find_first_json_object(text: str) -> str | None:
    """Extract the first complete {...} from text by brace matching."""
    return _find_json_object_from(text.strip(), 0)


def _find_json_object_from(text: str, start_pos: int) -> str | None:
    """Extract the first complete {...} from text starting at start_pos (index into text)."""
    idx = text.find("{", start_pos)
    if idx < 0:
        return None
    depth = 0
    i = idx
    in_string = None
    escape = False
    while i < len(text):
        c = text[i]
        if escape:
            escape = False
            i += 1
            continue
        if in_string:
            if c == "\\":
                escape = True
            elif c == in_string:
                in_string = None
            i += 1
            continue
        if c in '"\'':
            in_string = c
            i += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[idx : i + 1]
        i += 1
    return None


def _looks_like_json_schema(obj: dict) -> bool:
    """True if the dict looks like a JSON Schema definition (LLM echoed the schema)."""
    if not isinstance(obj, dict):
        return False
    return "$defs" in obj or "$schema" in obj or (obj.get("type") == "object" and "questions" not in obj and ("properties" in obj or "definitions" in obj))


def _fix_trailing_commas(json_str: str) -> str:
    """Remove trailing commas before ] or } to fix common LLM JSON errors."""
    return re.sub(r",\s*([}\]])", r"\1", json_str)


def extract_json_from_llm_response(raw: str, skip_schema_like: bool = False) -> dict[str, Any]:
    """
    Extract a single JSON object from LLM output.
    Strips markdown, takes first complete {...}, fixes trailing commas.
    If skip_schema_like is True, skips objects that look like JSON Schema ($defs, $schema, etc.)
    so we don't treat an echoed schema as the response.
    """
    text = _strip_markdown_code_block(raw)
    start = 0
    while True:
        chunk = _find_json_object_from(text, start)
        if not chunk:
            if start == 0:
                raise json.JSONDecodeError("No JSON object found", raw, 0)
            raise json.JSONDecodeError("Only JSON schema found, no data object", raw, 0)
        fixed = _fix_trailing_commas(chunk)
        try:
            out = json.loads(fixed)
        except json.JSONDecodeError:
            start = text.find(chunk, start) + len(chunk)
            if start >= len(text):
                raise json.JSONDecodeError("No valid JSON object found", raw, 0)
            continue
        if not isinstance(out, dict):
            start = text.find(chunk, start) + len(chunk)
            if start >= len(text):
                raise json.JSONDecodeError("Expected JSON object", raw, 0)
            continue
        if skip_schema_like and _looks_like_json_schema(out):
            start = text.find(chunk, start) + len(chunk)
            if start >= len(text):
                raise json.JSONDecodeError("Only JSON schema found, no data object", raw, 0)
            continue
        return out
    raise json.JSONDecodeError("No JSON object found", raw, 0)


def extract_ndjson_questions_from_llm_response(raw: str) -> list[dict[str, Any]]:
    """
    Extract all question-like JSON objects from LLM output.
    Tries NDJSON first (one object per line), then a single {"questions": [...]} object.
    Returns a list of dicts with at least "concept" (and ideally id, category, description).
    """
    text = _strip_markdown_code_block(raw)
    questions: list[dict[str, Any]] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("```"):
            continue
        try:
            obj = json.loads(_fix_trailing_commas(line))
            if isinstance(obj, dict) and obj.get("concept"):
                questions.append(obj)
        except (json.JSONDecodeError, TypeError):
            continue
    if questions:
        return questions
    try:
        chunk = _find_first_json_object(text)
        if chunk:
            data = json.loads(_fix_trailing_commas(chunk))
            if isinstance(data, dict) and "questions" in data and isinstance(data["questions"], list):
                return [q for q in data["questions"] if isinstance(q, dict) and q.get("concept")]
            if isinstance(data, dict) and data.get("concept"):
                return [data]
            if isinstance(data, list):
                return [q for q in data if isinstance(q, dict) and q.get("concept")]
    except (json.JSONDecodeError, TypeError):
        pass
    return []
