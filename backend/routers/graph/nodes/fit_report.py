"""
Fit Report Node: compare candidate resume/profile against job requirements.
Used when user has a resume and chooses the fit-report workflow (no questionnaire).
"""
import json
import logging

from backend.routers.graph.state import ResumeWorkflowState
from backend.services.gemini_llm_service import get_llm_service
from backend.routers.graph.schemas import FitReportSchema
from backend.utils.study_urls import filter_website_urls

logger = logging.getLogger(__name__)


FIT_REPORT_SYSTEM = """You are an expert career coach and technical recruiter. Compare the candidate's resume and profile against the job requirements.

Produce a honest, actionable fit report that helps the candidate understand:
1. What they already know and can demonstrate (strengths)
2. What they lack or need to deepen (gaps)
3. Concrete preparation steps to become an ideal fit for this role

Be specific to the job description and the candidate's actual background. Do not invent experience the candidate does not have.
Reference ATS keywords from the job when assessing match.

Output JSON with:
- overall_fit_score: 0-100 integer (how ready they are today for this role)
- role_readiness_summary: 2-4 sentence narrative
- strengths: list of specific things the candidate already knows/has (from resume)
- gaps: list of specific missing or weak areas
- preparation_plan: list of objects with topic, detail (actionable guidance), study_urls (optional website/documentation URL strings only — no YouTube or video links)
- ats_keywords_matched: keywords from the JD that appear supported by the resume
- ats_keywords_missing: important JD keywords not evidenced in the resume

Output only valid JSON. No markdown."""


def _format_baseline(baseline: dict | None) -> str:
    if not baseline:
        return "No candidate profile provided."
    parts = []
    skills = baseline.get("skills") or []
    if skills:
        parts.append(f"Skills: {', '.join(skills)}")
    exp = baseline.get("experience_summary") or ""
    if exp:
        parts.append(f"Experience summary: {exp}")
    resume_json = baseline.get("baseline_resume_json") or {}
    if isinstance(resume_json, str):
        try:
            resume_json = json.loads(resume_json)
        except json.JSONDecodeError:
            resume_json = {"raw_text": resume_json}
    raw = (resume_json or {}).get("raw_text") or ""
    if raw:
        parts.append(f"Resume text:\n{raw[:8000]}")
    return "\n\n".join(parts) if parts else "No candidate profile provided."


def _format_extracted(extracted: dict | None) -> str:
    if not extracted:
        return "No extracted requirements."
    return json.dumps(extracted, indent=2)


def generate_fit_report(state: ResumeWorkflowState) -> dict:
    """Generate structured fit report from JD + extracted skills + user baseline."""
    description = state.get("expanded_description") or state.get("normalized_description") or ""
    extracted = state.get("extracted_skills")
    baseline = state.get("user_baseline")

    if not description.strip():
        return {"fit_report": None, "error": "No job description to analyze", "current_node": "fit_report"}
    if not baseline:
        return {"fit_report": None, "error": "Resume or profile required for fit report", "current_node": "fit_report"}

    llm = get_llm_service()
    user = (
        f"Job description:\n{description}\n\n"
        f"Extracted job requirements:\n{_format_extracted(extracted)}\n\n"
        f"Candidate profile:\n{_format_baseline(baseline)}"
    )
    try:
        result = llm.invoke_structured(
            FIT_REPORT_SYSTEM,
            user,
            schema=FitReportSchema,
            stage="fit_report",
        )
        payload = result.model_dump()
        for item in payload.get("preparation_plan") or []:
            if isinstance(item, dict):
                item["study_urls"] = filter_website_urls(item.get("study_urls"))
        return {
            "fit_report": payload,
            "error": None,
            "current_node": "fit_report",
        }
    except Exception as e:
        logger.warning("Fit report generation failed: %s", e)
        return {"fit_report": None, "error": str(e), "current_node": "fit_report"}


def fit_report_node(state: ResumeWorkflowState) -> dict:
    """LangGraph-compatible wrapper."""
    return generate_fit_report(state)
