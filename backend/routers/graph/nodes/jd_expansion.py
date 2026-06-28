"""
JD Expansion Node: expand job titles into detailed, realistic job descriptions.
Quality-focused: specific responsibilities, skills, and level.
"""
import logging
from backend.routers.graph.state import ResumeWorkflowState
from backend.services.gemini_llm_service import get_llm_service

logger = logging.getLogger(__name__)


JD_EXPANSION_SYSTEM = """You are a senior technical recruiter and job description writer. Given only a job title, you produce a single, realistic job description paragraph that will be used later for skill extraction and candidate evaluation.

Requirements:
- Write 5–8 clear, specific sentences. No placeholders like [Company], [X years], or [Technology].
- Include: 2–3 core responsibilities (action-oriented), 2–4 concrete skills or technologies, experience level (entry/mid/senior), and at least one outcome or metric the role might own.
- Use language typical of real job posts: "Design and implement...", "Collaborate with...", "Own...", "Experience with...".
- Be role-specific: a "Senior Python Developer" description should differ clearly from a "Data Engineer" or "DevOps Engineer".
- Output only the paragraph. No headings, no bullet points, no "Job description:" prefix."""


def jd_expansion_node(state: ResumeWorkflowState) -> dict:
    """
    Expand job title into a detailed JD. Skip if normalized_description is already substantial.
    """
    normalized = (state.get("normalized_description") or "").strip()
    if not normalized:
        return {"expanded_description": None, "current_node": "jd_expansion"}

    llm = get_llm_service()
    company = (state.get("company_name") or "").strip()
    user = f"Job title to expand into a full job description paragraph:\n\n{normalized}"
    if company:
        user = f"Company context: {company}. Align tone and requirements with this context.\n\n" + user
    try:
        expanded = llm.invoke(JD_EXPANSION_SYSTEM, user, stage="jd_expansion")
        expanded = expanded.strip()
        if expanded.lower().startswith("job description"):
            expanded = expanded.split(":", 1)[-1].strip()
        return {
            "expanded_description": expanded,
            "error": None,
            "current_node": "jd_expansion",
        }
    except Exception as e:
        logger.exception("JD expansion failed")
        return {
            "expanded_description": None,
            "error": str(e),
            "current_node": "jd_expansion",
        }
