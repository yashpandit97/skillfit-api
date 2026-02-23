"""
Skill Extraction Node: extract structured, ATS-relevant requirements from JD.
Quality: concrete skills and concepts suitable for yes/no concept checklist.
"""
import logging
from backend.routers.graph.state import ResumeWorkflowState
from backend.services.ollama_llm_service import get_llm_service
from backend.routers.graph.schemas import ExtractedSkills

logger = logging.getLogger(__name__)


SKILL_EXTRACTION_SYSTEM = """You are an expert technical recruiter and ATS specialist. Your task is to deeply understand the job description and extract structured requirements so we can build a concept checklist (yes/no) for candidates.

Read the full job description carefully. Pay attention to:
- Role title and seniority (entry / mid / senior) and what that implies for depth of questions.
- Must-have vs nice-to-have or preferred skills (prioritize must-haves).
- Domain and industry (e.g. fintech, healthcare, e-commerce) so concepts are relevant.
- Key responsibilities and day-to-day work (derive concrete skills from these).
- Explicit technical requirements, tools, and frameworks mentioned.
- Soft or behavioral signals that can be turned into assessable concepts (e.g. "stakeholder communication" → "presenting to non-technical stakeholders").

Output a single JSON object with exactly these keys:
- "required_skills": list of strings. Concrete, nameable skills (e.g. "REST API design", "Unit testing", "Python"). Prefer 6–15 items when the JD is detailed. Avoid vague phrases like "good communication"; be specific.
- "required_tools": list of strings. Specific tools, frameworks, or platforms (e.g. "Docker", "PostgreSQL", "React", "AWS"). 4–10 items.
- "concepts": list of strings. Important concepts or knowledge areas that can be assessed (e.g. "database indexing", "CI/CD pipelines", "security best practices"). 5–12 items. Include both breadth and role-specific depth.
- "responsibilities": list of strings. Key job duties in short form. 4–8 items. These help align follow-up questions with what the role actually does.
- "experience_level": exactly one of "entry", "mid", "senior".
- "ats_keywords": list of strings. Terms that ATS systems and recruiters search for. Include variants (e.g. "Python", "Python 3", "REST", "APIs"). 8–18 items when the JD is rich.

Rules:
- Be specific and role-relevant. No generic filler.
- Every list item should be something a candidate could reasonably answer "I know this" or "I need to prepare" for.
- Richer JDs should yield more items; short JDs can have fewer. Quality over arbitrary counts.
- Output only valid JSON. No markdown, no code fences, no commentary."""


def skill_extraction_node(state: ResumeWorkflowState) -> dict:
    """
    Extract skills, tools, concepts, responsibilities, experience level, ATS keywords.
    Returns validated JSON; on validation failure sets extraction_validation_error for retry.
    """
    description = state.get("expanded_description") or state.get("normalized_description") or ""
    if not description.strip():
        return {
            "extracted_skills": None,
            "extraction_validation_error": "No description to extract from",
            "current_node": "skill_extraction",
        }

    llm = get_llm_service()
    user = f"Job description (read fully for role level, must-haves, domain, and responsibilities):\n\n{description}"
    try:
        result = llm.invoke_structured(
            SKILL_EXTRACTION_SYSTEM,
            user,
            schema=ExtractedSkills,
            stage="skill_extraction",
        )
        return {
            "extracted_skills": result.model_dump(),
            "extraction_validation_error": None,
            "error": None,
            "current_node": "skill_extraction",
        }
    except Exception as e:
        logger.warning("Skill extraction validation failed: %s", e)
        return {
            "extracted_skills": None,
            "extraction_validation_error": str(e),
            "retry_count": (state.get("retry_count") or 0) + 1,
            "current_node": "skill_extraction",
        }
