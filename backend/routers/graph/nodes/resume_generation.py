"""
Resume Generation Node: generate ATS-friendly resume content with deficiency markers.
"""
import logging
import json
from backend.routers.graph.state import ResumeWorkflowState
from backend.services.ollama_llm_service import get_llm_service
from backend.services.resume_builder import build_resume_structured
from backend.config import get_settings

logger = logging.getLogger(__name__)


def resume_generation_node(state: ResumeWorkflowState) -> dict:
    """
    Generate ideal resume content from profile + gaps. Missing/weak areas in RED (marked in structure).
    Inline deficiency comments: [Needs deeper experience in X], [Concept not demonstrated], etc.
    """
    evaluation = state.get("evaluation_result") or {}
    skill_gap = state.get("skill_gap_summary") or {}
    extracted = state.get("extracted_skills") or {}
    user_answers = state.get("user_answers") or {}

    if not evaluation or not skill_gap:
        return {
            "resume_structured": None,
            "resume_validation_error": "Missing evaluation or skill gap data",
            "current_node": "resume_generation",
        }

    try:
        # Use resume_builder service to produce structured resume (dict compatible with ResumeStructured)
        structured = build_resume_structured(
            extracted_skills=extracted,
            evaluation_result=evaluation,
            skill_gap_summary=skill_gap,
            user_answers=user_answers,
            max_pages=get_settings().resume_max_pages,
        )
        return {
            "resume_structured": structured,
            "resume_validation_error": None,
            "current_node": "resume_generation",
        }
    except Exception as e:
        logger.exception("Resume generation failed")
        return {
            "resume_structured": None,
            "resume_validation_error": str(e),
            "retry_count": (state.get("retry_count") or 0) + 1,
            "current_node": "resume_generation",
        }
