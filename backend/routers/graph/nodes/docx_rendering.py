"""
Docx Rendering Node: convert structured resume to .docx with red font for deficiencies.
"""
import logging
import os
import uuid
from pathlib import Path

from backend.routers.graph.state import ResumeWorkflowState
from backend.services.resume_builder import render_resume_docx
from backend.models.schemas.resume import ResumeStructured

logger = logging.getLogger(__name__)

# Directory for generated resumes (configurable via env in production)
OUTPUT_DIR = Path(os.getenv("RESUME_OUTPUT_DIR", "generated_resumes"))


def docx_rendering_node(state: ResumeWorkflowState) -> dict:
    """
    Convert resume_structured to .docx. Red font for deficient content; inline comments.
    No tables for layout; ATS-friendly.
    """
    structured = state.get("resume_structured")
    if not structured:
        return {
            "docx_path": None,
            "docx_error": "No resume structure to render",
            "current_node": "docx_rendering",
        }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"resume_{uuid.uuid4().hex[:12]}.docx"
    path = OUTPUT_DIR / filename

    try:
        resume = ResumeStructured.model_validate(structured)
        render_resume_docx(resume, path)
        return {
            "docx_path": str(path),
            "docx_error": None,
            "current_node": "docx_rendering",
        }
    except Exception as e:
        logger.exception("Docx rendering failed")
        return {
            "docx_path": None,
            "docx_error": str(e),
            "current_node": "docx_rendering",
        }
