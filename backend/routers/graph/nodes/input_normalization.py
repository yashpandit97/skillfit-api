"""
Input Normalization Node: validate job input; decide if we need JD expansion.
"""
import logging
from backend.routers.graph.state import ResumeWorkflowState

logger = logging.getLogger(__name__)


def input_normalization_node(state: ResumeWorkflowState) -> dict:
    """
    Validate job descriptions. If only job title given -> set needs_jd_expansion.
    Edge case: empty input -> set error and stop.
    """
    job_title = (state.get("job_title") or "").strip()
    job_description_raw = (state.get("job_description_raw") or "").strip()

    # Edge case: empty input
    if not job_title and not job_description_raw:
        return {
            "normalized_description": None,
            "needs_jd_expansion": False,
            "error": "Empty job input. Provide at least a job title or job description.",
            "current_node": "input_normalization",
        }

    # Has substantial JD text -> use as-is (normalized)
    if job_description_raw and len(job_description_raw) > 50:
        return {
            "normalized_description": job_description_raw,
            "needs_jd_expansion": False,
            "error": None,
            "current_node": "input_normalization",
        }

    # Only title or very vague -> expand later
    return {
        "normalized_description": job_title or job_description_raw or "",
        "needs_jd_expansion": True,
        "error": None,
        "current_node": "input_normalization",
    }
