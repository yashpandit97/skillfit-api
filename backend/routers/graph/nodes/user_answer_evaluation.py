"""
User Answer Evaluation Node: interpret yes/no answers and produce rich evaluation.
Answers are strictly "yes" (aware) or "no" (needs to prepare). No free text.
"""
import logging
import json
from backend.routers.graph.state import ResumeWorkflowState
from backend.services.gemini_llm_service import get_llm_service
from backend.routers.graph.schemas import EvaluationSchema

logger = logging.getLogger(__name__)


EVALUATION_SYSTEM = """You are an expert career coach and technical assessor. You interpret a candidate's concept checklist and produce a structured, actionable evaluation.

Input: Each question has a "concept" and the candidate answered: "yes" (I am aware / can demonstrate), "a_bit" (partial awareness / some experience), or "no" (I need to prepare).
When computing scores: treat "yes" as 1, "a_bit" as 0.5, "no" as 0. So score = (100 * (count_yes + 0.5 * count_a_bit) / total) rounded to integer.

Output a JSON object with:
- "scores": list of objects, one per category that appeared in the questionnaire. Each object has: "area" (category name), "score" (0–100 integer using the weighting above), "missing_concepts" (concept names where they answered no), "partial_concepts" (concept names where they answered a_bit), "strong_areas" (concept names where they answered yes), "recommendation" (one short sentence or null).
- "overall_score": integer 0–100, weighted by importance of categories/concepts for the role.
- "summary": 2–4 sentences. Summarize fit: strengths, partial areas, main gaps, and one clear recommendation.
- "concepts_to_prepare": list of concept names the candidate said no to (and optionally a_bit), ordered by suggested priority for preparation.

Quality: Be precise and fair. Use the exact concept names from the input. Do not invent concepts. Output only valid JSON; no markdown or code fences."""


def user_answer_evaluation_node(state: ResumeWorkflowState) -> dict:
    """
    Evaluate yes/no answers. Produce per-category scores, missing concepts, and rich summary.
    """
    questionnaire = state.get("questionnaire") or []
    user_answers = state.get("user_answers") or {}
    extracted_skills = state.get("extracted_skills") or {}

    if not questionnaire or not user_answers:
        return {
            "evaluation_result": None,
            "evaluation_validation_error": "Missing questionnaire or user answers",
            "current_node": "user_answer_evaluation",
        }

    # Normalize answers to yes / a_bit / no for LLM
    normalized_answers = {}
    for q in questionnaire:
        qid = q.get("id")
        raw = (user_answers.get(qid) or "").strip().lower().replace(" ", "_")
        if raw in ("yes", "y", "1", "true"):
            normalized_answers[qid] = "yes"
        elif raw in ("a_bit", "abit", "a bit", "partial"):
            normalized_answers[qid] = "a_bit"
        elif raw in ("no", "n", "0", "false"):
            normalized_answers[qid] = "no"
        else:
            normalized_answers[qid] = "no"

    llm = get_llm_service()
    payload = [
        {
            "id": q.get("id"),
            "concept": q.get("concept"),
            "category": q.get("category"),
            "answer": normalized_answers.get(q.get("id"), "no"),
        }
        for q in questionnaire
    ]
    user = (
        f"Job context:\n{json.dumps(extracted_skills, indent=2)}\n\n"
        f"Concept checklist and candidate answers (yes = aware, a_bit = partial, no = needs to prepare):\n{json.dumps(payload, indent=2)}"
    )
    try:
        result = llm.invoke_structured(
            EVALUATION_SYSTEM,
            user,
            schema=EvaluationSchema,
            stage="user_answer_evaluation",
        )
        return {
            "evaluation_result": result.model_dump(),
            "evaluation_validation_error": None,
            "current_node": "user_answer_evaluation",
        }
    except Exception as e:
        logger.warning("Evaluation validation failed: %s", e)
        return {
            "evaluation_result": None,
            "evaluation_validation_error": str(e),
            "retry_count": (state.get("retry_count") or 0) + 1,
            "current_node": "user_answer_evaluation",
        }
