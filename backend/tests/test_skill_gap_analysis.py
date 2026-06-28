"""
Unit tests: skill gap analysis node with mocked LLM.
"""
import pytest
from unittest.mock import patch, MagicMock
from backend.routers.graph.state import ResumeWorkflowState
from backend.routers.graph.nodes.skill_gap_analysis import skill_gap_analysis_node


def test_skill_gap_analysis_aggregates_result(mock_llm_service):
    """Gap analysis returns weaknesses, suggestions, resume_risk_claims, severity and study_urls."""
    mock_llm_service.invoke.return_value = (
        '{"weaknesses": [{"text": "Weak in testing", "study_urls": {"websites": ["https://example.com"], "youtube": []}}], '
        '"improvement_suggestions": [{"text": "Learn pytest", "study_urls": {"websites": [], "youtube": ["https://youtube.com/watch?v=1"]}}], '
        '"resume_risk_claims": [{"claim": "Led projects", "risk": "No metrics"}], '
        '"overall_gap_severity": "medium"}'
    )
    with patch("backend.routers.graph.nodes.skill_gap_analysis.get_llm_service", return_value=mock_llm_service):
        state: ResumeWorkflowState = {
            "evaluation_result": {"scores": [{"area": "Testing", "score": 40}], "overall_score": 40},
            "extracted_skills": {"required_skills": ["Python", "pytest"]},
        }
        out = skill_gap_analysis_node(state)
    assert out.get("skill_gap_summary") is not None
    assert out.get("gap_validation_error") is None
    summary = out["skill_gap_summary"]
    assert "weaknesses" in summary
    assert "overall_gap_severity" in summary
    assert summary["weaknesses"][0]["text"] == "Weak in testing"
    assert summary["weaknesses"][0]["study_urls"]["websites"] == ["https://example.com"]
<<<<<<< HEAD
    assert summary["improvement_suggestions"][0]["study_urls"]["youtube"] == ["https://youtube.com/watch?v=1"]
=======
    assert summary["improvement_suggestions"][0]["study_urls"]["youtube"] == []
>>>>>>> 1aa7648 (deployment changes + bug fixes)


def test_skill_gap_analysis_no_evaluation():
    """When evaluation is missing, returns error."""
    state: ResumeWorkflowState = {}
    out = skill_gap_analysis_node(state)
    assert out.get("skill_gap_summary") is None
    assert out.get("gap_validation_error") is not None
