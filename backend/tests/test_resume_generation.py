"""
Unit tests: resume generation and docx rendering.
"""
from pathlib import Path

import pytest
from backend.models.schemas.resume import ResumeStructured, ResumeSection, ResumeBullet
from backend.services.resume_builder import render_resume_docx


def test_resume_structured_validation():
    """ResumeStructured accepts valid dict with summary and sections."""
    data = {
        "summary": "Experienced developer.",
        "summary_deficiency": None,
        "sections": [
            {"heading": "Skills", "bullets": [{"text": "Python", "is_deficient": False, "deficiency_comment": None}]},
            {"heading": "Experience", "bullets": [{"text": "Led team", "is_deficient": True, "deficiency_comment": "[No measurable impact]"}]},
        ],
        "max_pages": 2,
    }
    resume = ResumeStructured.model_validate(data)
    assert resume.summary == data["summary"]
    assert len(resume.sections) == 2
    assert resume.sections[1].bullets[0].is_deficient is True
    assert resume.sections[1].bullets[0].deficiency_comment == "[No measurable impact]"


def test_render_resume_docx_creates_file(tmp_path):
    """Docx rendering produces a file with content."""
    resume = ResumeStructured(
        summary="Test summary.",
        summary_deficiency="[Needs more detail]",
        sections=[
            ResumeSection(
                heading="Skills",
                bullets=[
                    ResumeBullet(text="Python", is_deficient=False),
                    ResumeBullet(text="Weak area", is_deficient=True, deficiency_comment="[Concept not demonstrated]"),
                ],
            ),
        ],
        max_pages=2,
    )
    path = tmp_path / "resume.docx"
    render_resume_docx(resume, path)
    assert path.exists()
    assert path.stat().st_size > 0
