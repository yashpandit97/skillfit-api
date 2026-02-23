"""
Skill Gap Analysis Node: synthesize weaknesses, improvements, and resume risks.
Quality: actionable, specific, and aligned with yes/no concept gaps.
"""
import logging
import json
from backend.routers.graph.state import ResumeWorkflowState
from backend.services.ollama_llm_service import get_llm_service
from backend.routers.graph.schemas import SkillGapSchema

logger = logging.getLogger(__name__)


SKILL_GAP_SYSTEM = """You are an expert career coach and technical interviewer. Based on a candidate's evaluation (from their yes/no concept checklist) and the job requirements, you produce a structured gap analysis that will guide resume building and preparation.

Output a JSON object with:
- "weaknesses": list of strings. Specific gaps (e.g. "Limited experience with distributed systems", "No demonstrated Docker/containers"). Reference the concepts they marked "no" or low-scoring areas. 3–8 items. Be constructive, not harsh.
- "improvement_suggestions": list of strings. Concrete next steps (e.g. "Complete a small project using Docker", "Review database indexing and query optimization"). 3–6 items. Prioritize by impact for this role.
- "resume_risk_claims": list of objects, each with "claim" (string) and "risk" (string). These are things the candidate might be tempted to overstate on a resume, with the risk of getting caught in an interview. Example: {"claim": "Led system redesign", "risk": "No measurable impact or scope provided"}. 0–5 items. Only include real risks given their answers.
- "overall_gap_severity": exactly one of "low", "medium", "high". "low" = mostly aligned, few gaps. "medium" = several gaps but addressable. "high" = major misalignment or many critical gaps.

Use the evaluation summary and per-category scores. Do not invent concepts; refer to the job requirements and the concepts from the checklist. Output only valid JSON; no markdown or code fences."""


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
    try:
        result = llm.invoke_structured(
            SKILL_GAP_SYSTEM,
            user,
            schema=SkillGapSchema,
            stage="skill_gap_analysis",
        )
        return {
            "skill_gap_summary": result.model_dump(),
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
