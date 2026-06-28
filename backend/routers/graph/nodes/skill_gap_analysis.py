"""
Skill Gap Analysis Node: synthesize weaknesses, improvements, and resume risks.
Quality: actionable, specific, and aligned with yes/no concept gaps.
<<<<<<< HEAD
Includes study URLs (websites + YouTube) for each weakness and improvement suggestion.
=======
Includes study URLs (websites only) for each weakness and improvement suggestion.
>>>>>>> 1aa7648 (deployment changes + bug fixes)
"""
import logging
import json
from backend.routers.graph.state import ResumeWorkflowState
from backend.services.gemini_llm_service import get_llm_service
from backend.routers.graph.schemas import SkillGapSchema
from backend.utils.json_extract import extract_json_from_llm_response
<<<<<<< HEAD
=======
from backend.utils.study_urls import sanitize_study_urls
>>>>>>> 1aa7648 (deployment changes + bug fixes)

logger = logging.getLogger(__name__)


SKILL_GAP_SYSTEM = """You are an expert career coach and technical interviewer. Based on a candidate's evaluation (from their concept checklist: yes / a_bit / no) and the job requirements, you produce a structured gap analysis. "a_bit" means partial awareness; treat it as a softer gap than "no".

Output a JSON object with:
<<<<<<< HEAD
- "weaknesses": list of objects. Each object has "text" (string) and "study_urls" (object with "websites" and "youtube" arrays). "text" = specific gap (e.g. "Limited experience with distributed systems"). "study_urls.websites" = 1–2 high-quality article or documentation URLs to learn that topic. "study_urls.youtube" = 1–2 YouTube SEARCH URLs only (see rule below). Use real, well-known learning sites (e.g. MDN, official docs, freeCodeCamp, Khan Academy, Real Python). 3–8 weakness items. Be constructive.
- "improvement_suggestions": list of objects. Each has "text" (string) and "study_urls" (object with "websites", "youtube"). "text" = concrete next step. Same URL rules: 1–2 websites, 1–2 YouTube search URLs per item. 3–6 items. Prioritize by impact.
- "resume_risk_claims": list of objects, each with "claim" (string) and "risk" (string). Things the candidate might overstate, with interview risk. 0–5 items.
- "overall_gap_severity": exactly one of "low", "medium", "high".

IMPORTANT – YouTube URLs: Do NOT use specific video URLs (e.g. youtube.com/watch?v=...). They often break when videos are removed. Use ONLY YouTube search URLs so the user always gets current results. Format: https://www.youtube.com/results?search_query=TOPIC where TOPIC is 2–4 words (e.g. "docker tutorial", "distributed systems explained"). Replace spaces with + in the query.

Example weakness item: {"text": "No demonstrated Docker/containers", "study_urls": {"websites": ["https://docs.docker.com/get-started/"], "youtube": ["https://www.youtube.com/results?search_query=docker+tutorial"]}}
Example improvement item: {"text": "Complete a small project using Docker", "study_urls": {"websites": ["https://docs.docker.com/"], "youtube": ["https://www.youtube.com/results?search_query=docker+beginner+project"]}}
=======
- "weaknesses": list of objects. Each object has "text" (string) and "study_urls" (object with "websites" array only). "text" = specific gap (e.g. "Limited experience with distributed systems"). "study_urls.websites" = 1–2 high-quality article or documentation URLs to learn that topic. Use real, well-known learning sites (e.g. MDN, official docs, freeCodeCamp, Khan Academy, Real Python). Do NOT include YouTube or video links. 3–8 weakness items. Be constructive.
- "improvement_suggestions": list of objects. Each has "text" (string) and "study_urls" (object with "websites" array only). "text" = concrete next step. Same URL rules: 1–2 documentation or article URLs per item. No YouTube links. 3–6 items. Prioritize by impact.
- "resume_risk_claims": list of objects, each with "claim" (string) and "risk" (string). Things the candidate might overstate, with interview risk. 0–5 items.
- "overall_gap_severity": exactly one of "low", "medium", "high".

Example weakness item: {"text": "No demonstrated Docker/containers", "study_urls": {"websites": ["https://docs.docker.com/get-started/"]}}
Example improvement item: {"text": "Complete a small project using Docker", "study_urls": {"websites": ["https://docs.docker.com/"]}}
>>>>>>> 1aa7648 (deployment changes + bug fixes)

Use the evaluation summary and per-category scores. Do not invent concepts. Output only valid JSON; no markdown or code fences."""


def skill_gap_analysis_node(state: ResumeWorkflowState) -> dict:
    """
    Aggregate evaluation into weaknesses, suggestions, and resume-risk claims.
    """
    evaluation = state.get("evaluation_result") or {}
    extracted = state.get("extracted_skills") or {}

    if not evaluation:
        return {
            "skill_gap_summary": None,
            "gap_validation_error": "No evaluation result",
            "current_node": "skill_gap_analysis",
        }

    llm = get_llm_service()
    user = (
        f"Evaluation (from yes/no concept checklist):\n{json.dumps(evaluation, indent=2)}\n\n"
        f"Job requirements (for context):\n{json.dumps(extracted, indent=2)}"
    )
    def _normalize_item(x):  # str -> {text, study_urls}; dict -> ensure study_urls
        if isinstance(x, str):
            return {"text": x, "study_urls": {"websites": [], "youtube": []}}
        if isinstance(x, dict):
            su = x.get("study_urls")
            if not isinstance(su, dict):
                su = {}
            return {
                "text": x.get("text", ""),
<<<<<<< HEAD
                "study_urls": {
                    "websites": list(su.get("websites", [])) if isinstance(su.get("websites"), list) else [],
                    "youtube": list(su.get("youtube", [])) if isinstance(su.get("youtube"), list) else [],
                },
=======
                "study_urls": sanitize_study_urls(su),
>>>>>>> 1aa7648 (deployment changes + bug fixes)
            }
        return {"text": "", "study_urls": {"websites": [], "youtube": []}}

    try:
        raw = llm.invoke(
            SKILL_GAP_SYSTEM + "\n\nRespond with a single JSON object only. No markdown.",
            user,
            stage="skill_gap_analysis",
        )
        data = extract_json_from_llm_response(raw)
        # Accept both list-of-strings and list-of-objects from LLM
        data.setdefault("weaknesses", [])
        data.setdefault("improvement_suggestions", [])
        data["weaknesses"] = [_normalize_item(x) for x in data["weaknesses"]]
        data["improvement_suggestions"] = [_normalize_item(x) for x in data["improvement_suggestions"]]
        data.setdefault("resume_risk_claims", [])
        data.setdefault("overall_gap_severity", "medium")
        result = SkillGapSchema.model_validate(data)
        summary = result.model_dump()
        return {
            "skill_gap_summary": summary,
            "gap_validation_error": None,
            "current_node": "skill_gap_analysis",
        }
    except Exception as e:
        logger.warning("Skill gap validation failed: %s", e)
        return {
            "skill_gap_summary": None,
            "gap_validation_error": str(e),
            "retry_count": (state.get("retry_count") or 0) + 1,
            "current_node": "skill_gap_analysis",
        }
