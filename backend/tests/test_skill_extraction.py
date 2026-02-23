"""
Unit tests: skill extraction node with mocked LLM.
"""
import pytest
from unittest.mock import patch, MagicMock
from backend.routers.graph.state import ResumeWorkflowState
from backend.routers.graph.nodes.skill_extraction import skill_extraction_node


def test_skill_extraction_returns_validated_structure(mock_llm_service):
    """Skill extraction node returns dict with required keys when LLM returns valid JSON."""
    mock_llm_service.invoke_structured.return_value = MagicMock(
        model_dump=lambda: {
            "required_skills": ["Python", "FastAPI"],
            "required_tools": ["Git", "Docker"],
            "concepts": ["REST", "OOP"],
            "responsibilities": ["Design APIs", "Write tests"],
            "experience_level": "mid",
            "ats_keywords": ["Python", "API", "SQL"],
        }
    )
    with patch("backend.routers.graph.nodes.skill_extraction.get_llm_service", return_value=mock_llm_service):
        state: ResumeWorkflowState = {
            "normalized_description": "Senior Python developer. Must have FastAPI, SQL, Docker.",
            "expanded_description": None,
        }
        out = skill_extraction_node(state)
    assert "extracted_skills" in out
    assert out.get("extraction_validation_error") is None
    skills = out["extracted_skills"]
    assert "required_skills" in skills
    assert "experience_level" in skills
    assert "ats_keywords" in skills


def test_skill_extraction_empty_description():
    """When description is empty, returns error and no extracted_skills."""
    state: ResumeWorkflowState = {"normalized_description": "", "expanded_description": None}
    out = skill_extraction_node(state)
    assert out.get("extracted_skills") is None
    assert out.get("extraction_validation_error") is not None
