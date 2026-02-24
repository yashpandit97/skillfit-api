"""
Questionnaire Generation Node: generate concept-based yes/no evaluation items.
Quality over quantity: focused concepts the user can answer Yes (aware) or No (needs to prepare).
For long JDs: split into sections, generate questions per section in parallel, then dedupe.
"""
import logging
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from backend.routers.graph.state import ResumeWorkflowState
from backend.services.ollama_llm_service import get_llm_service
from backend.routers.graph.schemas import QuestionnaireSchema, QuestionnaireItemSchema

logger = logging.getLogger(__name__)

# Use sectioned flow when JD is longer than this (chars) to avoid huge prompts and speed up stage 1
MIN_JD_LENGTH_FOR_SECTIONS = 1000
# Split JD into sections of this many characters each (break at sentence/paragraph when possible)
SECTION_CHAR_SIZE = 1000
MAX_QUESTIONS_PER_SECTION = 5


QUESTIONNAIRE_SYSTEM = """You are an expert technical recruiter and career coach. Your task is to produce a concise, high-value concept checklist for evaluating a candidate's fit for a role.

Rules:
- Output a JSON object with a single key "questions", which is a list of objects.
- Each object has: "id" (string, e.g. q1, q2, q3), "concept" (string, the skill or concept name, clear and specific), "category" (exactly one of: fundamentals, tools, advanced_concepts, real_world, metrics_impact), "description" (string or null; one short line clarifying what "aware" means for this concept, or null if the concept name is self-explanatory).
- Focus on quality over quantity: 10–18 concepts that matter most for the role. Prioritize discriminative, job-critical items.
- "fundamentals": core theory or basics (e.g. "REST API design", "SQL joins").
- "tools": specific tools/tech (e.g. "Docker", "Git", "PostgreSQL").
- "advanced_concepts": deeper or senior-level topics (e.g. "distributed systems consistency", "performance profiling").
- "real_world": applied scenarios (e.g. "debugging production incidents", "code review best practices").
- "metrics_impact": quantification and outcomes (e.g. "measuring feature impact", "owning KPIs").
- Be concrete and role-specific. Avoid vague or generic concepts.
- Output only valid JSON. No markdown, no code fences."""

# For streaming: one JSON object per line (NDJSON). Same fields per line.
QUESTIONNAIRE_NDJSON_SYSTEM = """You are an expert technical recruiter and career coach. Your task is to produce a concept checklist for evaluating a candidate's fit for a role.

Output exactly one JSON object per line (NDJSON). Each line is a single question with these keys only:
- "id": string, e.g. q1, q2, q3 (increment for each line).
- "concept": string, the skill or concept name (clear and specific).
- "category": exactly one of: fundamentals, tools, advanced_concepts, real_world, metrics_impact.
- "description": string or null; one short line clarifying what "aware" means, or null.

Categories: fundamentals (core theory), tools (Docker, Git, etc.), advanced_concepts (senior topics), real_world (applied scenarios), metrics_impact (KPIs, outcomes).
Generate 10–18 lines. One JSON object per line. No other text, no markdown, no code fences."""

# Stage 1: LLM decides how many questions (scenario-based); aim for 3–12 high-value concepts
STAGE_1_NDJSON_SYSTEM = """You are an expert technical recruiter and career coach. Produce the first batch of concepts for a candidate concept checklist based on the job description.

Read the job requirements: role level, must-have vs nice-to-have skills, key responsibilities, and domain. Decide how many concepts to ask in this first batch (between 3 and 12) based on role complexity and breadth—more for senior/broad roles, fewer for narrow roles. Generate concepts that are concrete and directly relevant.

Output exactly one JSON object per line (NDJSON). Each line has: "id", "concept", "category", "description".
- "id": use q1, q2, q3, ... (one per line; as many as you decided for this batch).
- "category": exactly one of: fundamentals, tools, advanced_concepts, real_world, metrics_impact.
Generate 3 to 12 lines. One JSON object per line. No other text."""

# Stage 1 sectioned: one chunk of the JD → 2–4 concepts (short prompt for speed)
STAGE_1_SECTION_SYSTEM = """You are a technical recruiter. From the following excerpt of a job description, extract 2 to 4 key concepts or skills for a candidate checklist. Be specific (e.g. "Docker", "REST API design", "unit testing").

Output NDJSON: one JSON object per line. Each line: {"concept": "short name", "category": "fundamentals"|"tools"|"advanced_concepts"|"real_world"|"metrics_impact", "description": null or one short line}.
Generate 2 to 4 lines. No other text."""

# Next stage: generate questions from concepts the user answered "yes" to; if none, generate complementary concepts from the job.
def _stage_next_from_yes_ndjson_system(next_id_start: int, yes_concepts: list[dict]) -> str:
    if yes_concepts:
        return f"""You are an expert technical recruiter and career coach. A candidate is answering a concept checklist. In the CURRENT stage they said YES (aware) to these concepts:

{json.dumps([{"concept": c.get("concept"), "category": c.get("category")} for c in yes_concepts], indent=2)}

Your task: Generate the NEXT batch of concepts to evaluate. Go deeper or more applied on the areas they said yes to. Decide how many questions to ask (between 2 and 10) based on how many areas they said yes to.

Rules:
- Do NOT repeat any concept already asked in previous stages (the list of concepts already asked is provided below).
- Output NDJSON: one JSON object per line with "id", "concept", "category", "description".
- "id": use q{next_id_start}, q{next_id_start + 1}, ... for as many questions as you generate.
- "category": exactly one of: fundamentals, tools, advanced_concepts, real_world, metrics_impact.
Output 2 to 10 lines. One JSON object per line. No other text."""
    # No "yes" answers: generate complementary/foundational concepts from the job so the checklist continues.
    return f"""You are an expert technical recruiter and career coach. A candidate is answering a concept checklist. They said YES to none of the concepts in the current stage.

Your task: Generate the NEXT batch of 3 to 6 concepts to evaluate. Pick complementary or foundational concepts that are still relevant to the role (from the job description below). Do NOT repeat any concept already asked (list provided below).

Rules:
- Output NDJSON: one JSON object per line with "id", "concept", "category", "description".
- "id": use q{next_id_start}, q{next_id_start + 1}, ... (generate at least 3, at most 6).
- "category": exactly one of: fundamentals, tools, advanced_concepts, real_world, metrics_impact.
Output 3 to 6 lines. One JSON object per line. No other text."""


def questionnaire_generation_node(state: ResumeWorkflowState) -> dict:
    """
    Generate concept-based checklist. If state already has questionnaire and user_answers (e.g. submit flow), skip LLM and return existing.
    """
    if state.get("questionnaire") and state.get("user_answers"):
        return {
            "questionnaire": state["questionnaire"],
            "questionnaire_validation_error": None,
            "current_node": "questionnaire_generation",
        }
    extracted = state.get("extracted_skills") or {}
    if not extracted:
        return {
            "questionnaire": None,
            "questionnaire_validation_error": "No extracted skills",
            "current_node": "questionnaire_generation",
        }

    llm = get_llm_service()
    user = f"Job requirements (use these to derive role-specific concepts):\n{json.dumps(extracted, indent=2)}"
    try:
        result = llm.invoke_structured(
            QUESTIONNAIRE_SYSTEM,
            user,
            schema=QuestionnaireSchema,
            stage="questionnaire_generation",
        )
        questions = [q.model_dump() for q in result.questions]
        return {
            "questionnaire": questions,
            "questionnaire_validation_error": None,
            "current_node": "questionnaire_generation",
        }
    except Exception as e:
        logger.warning("Questionnaire validation failed: %s", e)
        return {
            "questionnaire": None,
            "questionnaire_validation_error": str(e),
            "retry_count": (state.get("retry_count") or 0) + 1,
            "current_node": "questionnaire_generation",
        }


def stream_questionnaire_generation(extracted_skills: dict):
    """
    Stream questionnaire generation: yield one question dict at a time (NDJSON from LLM).
    Yields dicts with id, concept, category, description. Caller can send each to SSE.
    """
    llm = get_llm_service()
    user = f"Job requirements (use these to derive role-specific concepts):\n{json.dumps(extracted_skills, indent=2)}"
    buffer = ""
    for chunk in llm.stream(
        QUESTIONNAIRE_NDJSON_SYSTEM,
        user,
        stage="questionnaire_generation_stream",
    ):
        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if isinstance(data, dict) and "id" in data and "concept" in data and "category" in data:
                    item = QuestionnaireItemSchema.model_validate(data)
                    yield item.model_dump()
            except (json.JSONDecodeError, ValueError) as e:
                logger.debug("Skip non-question line or invalid JSON: %s", e)
    if buffer.strip():
        try:
            data = json.loads(buffer.strip())
            if isinstance(data, dict) and "id" in data and "concept" in data and "category" in data:
                item = QuestionnaireItemSchema.model_validate(data)
                yield item.model_dump()
        except (json.JSONDecodeError, ValueError):
            pass


# --- Dynamic stages: variable questions per stage; next stage driven by "yes" answers from current stage ---
MIN_QUESTIONS_STAGE_1 = 3
MAX_QUESTIONS_STAGE_1 = 12
MIN_QUESTIONS_NEXT_STAGE = 2
MAX_QUESTIONS_NEXT_STAGE = 10


def _total_stages_from_question_count(questionnaire: list) -> int:
    """Number of stages from questionnaire (max stage number among questions, or 1 if empty)."""
    if not questionnaire:
        return 1
    stages = [q.get("stage") for q in questionnaire if isinstance(q, dict) and q.get("stage") is not None]
    return max(stages, default=1)


def _normalize_concept(concept: str) -> str:
    """Normalize for duplicate detection: lowercase, strip, collapse spaces."""
    if not concept or not isinstance(concept, str):
        return ""
    return " ".join(str(concept).lower().strip().split())


def _is_duplicate_concept(new_concept: str, previous_concepts: list[str]) -> bool:
    """True if new_concept is a repeat of any previous concept (exact or substantial overlap)."""
    new_n = _normalize_concept(new_concept)
    if not new_n:
        return True
    for prev in previous_concepts:
        p = _normalize_concept(prev)
        if not p:
            continue
        if new_n == p:
            return True
        if new_n in p or p in new_n:
            return True
    return False


def _parse_question_line(line: str, default_id: str = "q0"):
    """Parse a single line into a question dict, or None. Lenient on missing keys."""
    line = line.strip()
    if not line or line.startswith("#") or line.startswith("```"):
        return None
    # Strip markdown code fence
    if line.startswith("```"):
        line = line.replace("```json", "").replace("```", "").strip()
    try:
        data = json.loads(line)
        if not isinstance(data, dict):
            return None
        concept = data.get("concept")
        if concept is None and "name" in data:
            concept = data.get("name")
        if not concept:
            return None
        qid = str(data.get("id", default_id)).strip() or default_id
        cat = data.get("category") or "fundamentals"
        if not isinstance(cat, str):
            cat = "fundamentals"
        item = QuestionnaireItemSchema.model_validate({
            "id": qid,
            "concept": str(concept),
            "category": cat,
            "description": data.get("description"),
        })
        return item.model_dump()
    except (json.JSONDecodeError, ValueError) as e:
        logger.debug("Skip line %r: %s", line[:80], e)
        return None


def _stream_ndjson_questions(
    system_prompt: str,
    user_prompt: str,
    stage_name: str,
    progress_callback: Callable[[str, int], None] | None = None,
):
    """Yield question dicts from LLM NDJSON stream. Tolerates markdown, single array, or NDJSON.
    If progress_callback(phase, progress_pct) is provided, calls it during streaming (phase='questionnaire', pct 30-100)."""
    llm = get_llm_service()
    buffer = ""
    chunk_count = 0
    PROGRESS_START = 30
    PROGRESS_END = 95
    CHUNKS_PER_PCT = 2  # every N chunks bump progress (smaller so stage 2+ sees updates sooner)
    if progress_callback:
        progress_callback("questionnaire", PROGRESS_START)
    for chunk in llm.stream(system_prompt, user_prompt, stage=stage_name):
        chunk_count += 1
        if progress_callback and (chunk_count <= 1 or chunk_count % CHUNKS_PER_PCT == 0):
            pct = min(PROGRESS_END, PROGRESS_START + chunk_count // CHUNKS_PER_PCT)
            progress_callback("questionnaire", pct)
        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            parsed = _parse_question_line(line)
            if parsed:
                yield parsed
    # Flush remainder
    if buffer.strip():
        # Maybe the model returned one big JSON array
        stripped = buffer.strip()
        for prefix in ("```json", "```"):
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix):].strip()
        if stripped.endswith("```"):
            stripped = stripped[:-3].strip()
        # Try each line as NDJSON first
        for line in stripped.split("\n"):
            parsed = _parse_question_line(line)
            if parsed:
                yield parsed
        # If no lines yielded, try single JSON array or {"questions": [...]}
        try:
            data = json.loads(stripped)
            if isinstance(data, list):
                for i, obj in enumerate(data):
                    if isinstance(obj, dict):
                        p = _parse_question_line(json.dumps(obj), default_id=f"q{i+1}")
                        if p:
                            yield p
            elif isinstance(data, dict) and "questions" in data:
                for i, obj in enumerate(data.get("questions") or []):
                    if isinstance(obj, dict):
                        p = _parse_question_line(json.dumps(obj), default_id=f"q{i+1}")
                        if p:
                            yield p
        except (json.JSONDecodeError, ValueError):
            # Try to extract a JSON array from the buffer (e.g. model wrapped in markdown)
            for pattern in (r"\[[\s\S]*\]", r'\{"questions"\s*:\s*\[[\s\S]*\]\s*}'):
                match = re.search(pattern, stripped)
                if match:
                    try:
                        data = json.loads(match.group(0))
                        if isinstance(data, list):
                            for i, obj in enumerate(data):
                                if isinstance(obj, dict):
                                    p = _parse_question_line(json.dumps(obj), default_id=f"q{i+1}")
                                    if p:
                                        yield p
                            break
                        if isinstance(data, dict) and "questions" in data:
                            for i, obj in enumerate(data.get("questions") or []):
                                if isinstance(obj, dict):
                                    p = _parse_question_line(json.dumps(obj), default_id=f"q{i+1}")
                                    if p:
                                        yield p
                            break
                    except (json.JSONDecodeError, ValueError):
                        continue
    if progress_callback:
        progress_callback("questionnaire", 100)


def _stage_1_sectioned(extracted_skills: dict, description_text: str) -> list[dict]:
    """Generate stage 1 questions by splitting JD into sections and processing in parallel. Dedupe; return list."""
    sections = _split_jd_into_sections(description_text)
    if not sections:
        return []
    role_context = extracted_skills.get("experience_level") or "mid"
    if extracted_skills.get("required_skills"):
        role_context += "; key skills: " + ", ".join(extracted_skills.get("required_skills", [])[:5])
    seen_concepts: list[str] = []
    collected: list[dict] = []
    max_workers = min(len(sections), 10)

    def task(idx: int):
        return idx, _generate_questions_for_one_section(sections[idx], role_context, idx)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(task, i) for i in range(len(sections))]
        for future in as_completed(futures):
            try:
                idx, section_questions = future.result()
                for q in section_questions:
                    concept = (q.get("concept") or "").strip()
                    if not concept or _is_duplicate_concept(concept, seen_concepts):
                        continue
                    seen_concepts.append(concept)
                    collected.append(q)
                    if len(collected) >= MAX_QUESTIONS_STAGE_1:
                        break
            except Exception as e:
                logger.warning("Section question generation failed: %s", e)
            if len(collected) >= MAX_QUESTIONS_STAGE_1:
                break

    return collected[:MAX_QUESTIONS_STAGE_1]


def stream_questionnaire_stage_1(
    extracted_skills: dict,
    description_text: str = "",
    progress_callback: Callable[[str, int], None] | None = None,
):
    """Generate first batch of questions (stage 1). For long JDs, split into sections and run in parallel; else single stream.
    Optional progress_callback(phase, progress_pct) for LLM generation progress (e.g. WebSocket)."""
    jd = (description_text or "").strip()
    if len(jd) >= MIN_JD_LENGTH_FOR_SECTIONS:
        logger.info("Using sectioned stage 1 (JD length=%s)", len(jd))
        if progress_callback:
            progress_callback("questionnaire", 30)
        sectioned = _stage_1_sectioned(extracted_skills, jd)
        if progress_callback:
            progress_callback("questionnaire", 100)
        for i, q in enumerate(sectioned, start=1):
            yield {**q, "id": f"q{i}", "stage": 1}
        if len(sectioned) >= MIN_QUESTIONS_STAGE_1:
            return
        logger.info("Sectioned flow returned %s questions, falling back to single stream", len(sectioned))
    user = _user_prompt_from_extracted(extracted_skills)
    start_id = 1
    for i, q_dict in enumerate(_stream_ndjson_questions(
        STAGE_1_NDJSON_SYSTEM,
        user,
        stage_name="questionnaire_stage_1",
        progress_callback=progress_callback,
    )):
        qid = start_id + i
        yield {**q_dict, "id": f"q{qid}", "stage": 1}


def _user_prompt_from_extracted(extracted_skills: dict) -> str:
    """Build a rich prompt from extracted skills so the LLM has full JD context."""
    parts = ["Job requirements (use these to derive role-specific concepts):"]
    parts.append(json.dumps(extracted_skills, indent=2))
    if extracted_skills.get("responsibilities"):
        parts.append("\nKey responsibilities to align concepts with:")
        parts.append(json.dumps(extracted_skills.get("responsibilities"), indent=2))
    if extracted_skills.get("experience_level"):
        parts.append(f"\nExperience level: {extracted_skills.get('experience_level')}. Tailor concept depth accordingly.")
    return "\n".join(parts)


def _split_jd_into_sections(text: str, section_size: int = SECTION_CHAR_SIZE) -> list[str]:
    """Split JD into sections of ~section_size characters each, breaking at sentence/paragraph when possible."""
    text = (text or "").strip()
    if not text:
        return []
    sections = []
    remaining = text
    while remaining:
        if len(remaining) <= section_size:
            sections.append(remaining.strip())
            break
        chunk = remaining[: section_size + 500]
        # Prefer to break at sentence or paragraph boundary near section_size
        break_at = section_size
        for sep in (". ", ".\n", "\n\n", "\n", " "):
            idx = chunk.rfind(sep, section_size // 2, section_size + 400)
            if idx >= section_size // 2:
                break_at = idx + len(sep)
                break
        section = remaining[:break_at].strip()
        remaining = remaining[break_at:].lstrip()
        if section:
            sections.append(section)
    return sections if sections else [text]


def _generate_questions_for_one_section(
    section_text: str,
    role_context: str,
    section_index: int,
) -> list[dict]:
    """Generate 2–4 question dicts from one JD section. Non-streaming, used for parallel sectioned flow."""
    if not (section_text or "").strip():
        return []
    user = f"Role context: {role_context}\n\nJob description excerpt:\n{section_text[:4000]}"
    llm = get_llm_service()
    raw = llm.invoke(
        STAGE_1_SECTION_SYSTEM,
        user,
        stage=f"questionnaire_stage_1_section_{section_index}",
    )
    from backend.utils.json_extract import extract_json_from_llm_response

    questions_data = []
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("```"):
            continue
        parsed = _parse_question_line(line)
        if parsed:
            questions_data.append(parsed)
    if not questions_data:
        data = extract_json_from_llm_response(raw)
        if isinstance(data, list):
            questions_data = [_parse_question_line(json.dumps(o)) for o in data if isinstance(o, dict)]
            questions_data = [q for q in questions_data if q]
        elif isinstance(data, dict) and data.get("concept"):
            p = _parse_question_line(json.dumps(data))
            if p:
                questions_data = [p]
    return questions_data[:MAX_QUESTIONS_PER_SECTION]


def generate_next_stage_structured(
    extracted_skills: dict,
    next_stage: int,
    previous_all_questions: list,
    current_stage_yes_questions: list,
) -> list:
    """Fallback: get next batch via single LLM call when stream returns 0. Uses yes-only from current stage."""
    next_id_start = len(previous_all_questions) + 1
    previous_concepts = [str(q.get("concept", "")).strip() for q in previous_all_questions if q.get("concept")]
    previous_concepts_line = "Concepts already asked (do NOT repeat any of these): " + ", ".join(previous_concepts) if previous_concepts else ""
    system_base = (
        _stage_next_from_yes_ndjson_system(next_id_start, current_stage_yes_questions)
        + f"\n\n{previous_concepts_line}\n\nOutput format: NDJSON (one JSON object per line) or a single JSON object with key \"questions\" (array). No markdown, no code fences."
    )
    user = _user_prompt_from_extracted(extracted_skills) + f"\n\n{previous_concepts_line}"
    llm = get_llm_service()
    from backend.utils.json_extract import extract_ndjson_questions_from_llm_response

    def do_invoke(extra_instruction: str = "") -> list:
        raw = llm.invoke(
            system_base + extra_instruction,
            user,
            stage=f"questionnaire_stage_{next_stage}_structured",
        )
        questions_data = extract_ndjson_questions_from_llm_response(raw)
        if not questions_data:
            from backend.utils.json_extract import extract_json_from_llm_response
            try:
                data = extract_json_from_llm_response(raw)
                if isinstance(data, list):
                    questions_data = [q for q in data if isinstance(q, dict) and q.get("concept")]
                elif isinstance(data, dict):
                    if "questions" in data and isinstance(data["questions"], list):
                        questions_data = data["questions"]
                    elif data.get("concept"):
                        questions_data = [data]
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning("Structured fallback: LLM response had no valid JSON: %s", e)
                questions_data = []
        validated = [QuestionnaireItemSchema.model_validate(q) for q in questions_data if isinstance(q, dict) and q.get("concept")]
        result = QuestionnaireSchema(questions=validated)
        out = []
        for i, q in enumerate(result.questions):
            qid = next_id_start + i
            out.append({**q.model_dump(), "id": f"q{qid}", "stage": next_stage})
        return out

    out = do_invoke("")
    if not out:
        logger.info("Fallback got 0 questions, retrying with explicit NDJSON request")
        out = do_invoke("\n\nYou MUST output at least 2 questions. One JSON object per line (NDJSON). Each line: {\"id\": \"qN\", \"concept\": \"...\", \"category\": \"...\", \"description\": \"...\"}. No other text.")
    return out


# Last resort when stream + structured fallback both return 0: minimal prompt for a few concepts.
_LAST_RESORT_NEXT_STAGE = """You are a technical recruiter. Output exactly 3 new concept questions for a candidate checklist. Use the job description and do NOT repeat the concepts already asked.

Output 3 lines (NDJSON). Each line one JSON object: {{"id": "qN", "concept": "short concept name", "category": "fundamentals" or "tools" or "advanced_concepts" or "real_world" or "metrics_impact", "description": null or one short line}}.
Use ids q{start}, q{start_plus_1}, q{start_plus_2}. No other text."""


def _generate_last_resort_next_stage(
    extracted_skills: dict,
    next_id_start: int,
    previous_concepts: list[str],
    next_stage: int,
) -> list[dict]:
    """Return 2-3 questions when normal generation returned 0."""
    previous_line = "Already asked (do not repeat): " + ", ".join(previous_concepts) if previous_concepts else "None yet."
    user = _user_prompt_from_extracted(extracted_skills) + "\n\n" + previous_line
    system = _LAST_RESORT_NEXT_STAGE.format(
        start=next_id_start,
        start_plus_1=next_id_start + 1,
        start_plus_2=next_id_start + 2,
    )
    llm = get_llm_service()
    raw = llm.invoke(system, user, stage=f"questionnaire_stage_{next_stage}_last_resort")
    from backend.utils.json_extract import extract_ndjson_questions_from_llm_response, extract_json_from_llm_response
    questions_data = extract_ndjson_questions_from_llm_response(raw)
    if not questions_data:
        try:
            data = extract_json_from_llm_response(raw)
            if isinstance(data, list):
                questions_data = [q for q in data if isinstance(q, dict) and q.get("concept")]
            elif isinstance(data, dict) and data.get("concept"):
                questions_data = [data]
            elif isinstance(data, dict) and isinstance(data.get("questions"), list):
                questions_data = data["questions"]
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Last-resort next stage: LLM response had no valid JSON: %s", e)
            questions_data = []
    out = []
    for i, q in enumerate(questions_data[:3]):
        if not isinstance(q, dict) or not q.get("concept"):
            continue
        try:
            item = QuestionnaireItemSchema.model_validate(q)
            out.append({**item.model_dump(), "id": f"q{next_id_start + i}", "stage": next_stage})
        except (ValueError, Exception):
            continue
    return out


def stream_questionnaire_stage_next(
    extracted_skills: dict,
    next_stage: int,
    previous_all_questions: list,
    current_stage_yes_questions: list,
    progress_callback: Callable[[str, int], None] | None = None,
):
    """Generate next batch of questions from current stage's YES answers only. LLM decides count (2–10). Yields question dicts with stage=next_stage. No repeats from previous stages.
    Optional progress_callback(phase, progress_pct) for LLM generation progress."""
    next_id_start = len(previous_all_questions) + 1
    previous_concepts = [str(q.get("concept", "")).strip() for q in previous_all_questions if q.get("concept")]
    previous_concepts_line = "Concepts already asked (do NOT repeat any of these): " + ", ".join(previous_concepts) if previous_concepts else ""

    user = (
        _user_prompt_from_extracted(extracted_skills)
        + f"\n\n{previous_concepts_line}"
    )
    collected = []
    for i, q_dict in enumerate(_stream_ndjson_questions(
        _stage_next_from_yes_ndjson_system(next_id_start, current_stage_yes_questions),
        user,
        stage_name=f"questionnaire_stage_{next_stage}",
        progress_callback=progress_callback,
    )):
        concept = q_dict.get("concept") or ""
        if not _is_duplicate_concept(concept, previous_concepts):
            qid = next_id_start + len(collected)
            collected.append({**q_dict, "id": f"q{qid}", "stage": next_stage})
            previous_concepts.append(concept)
        if len(collected) >= MAX_QUESTIONS_NEXT_STAGE:
            break
    if len(collected) < MIN_QUESTIONS_NEXT_STAGE and len(collected) > 0:
        # Accept 1 question if LLM only gave 1
        pass
    if not collected:
        logger.info("Stream returned 0 questions for stage %s, trying structured fallback", next_stage)
        fallback = generate_next_stage_structured(
            extracted_skills, next_stage, previous_all_questions, current_stage_yes_questions
        )
        for q in fallback:
            concept = q.get("concept") or ""
            if not _is_duplicate_concept(concept, previous_concepts):
                collected.append(q)
                previous_concepts.append(concept)
    if not collected:
        logger.info("Structured fallback returned 0 for stage %s, trying last-resort generation", next_stage)
        for q in _generate_last_resort_next_stage(
            extracted_skills, next_id_start, previous_concepts, next_stage
        ):
            concept = q.get("concept") or ""
            if not _is_duplicate_concept(concept, previous_concepts):
                collected.append(q)
                previous_concepts.append(concept)
    for q in collected:
        yield q
