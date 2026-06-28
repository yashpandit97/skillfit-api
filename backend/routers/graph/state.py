"""
LangGraph workflow state. Flows across all nodes; each node reads/updates relevant keys.
"""
from typing import Any, Optional
from typing import TypedDict


class ResumeWorkflowState(TypedDict, total=False):
    # Input
    job_title: Optional[str]
    job_description_raw: Optional[str]
    company_name: Optional[str]
    user_id: Optional[int]
    job_submission_id: Optional[int]
    user_baseline: Optional[dict[str, Any]]  # From UserProfile: skills, experience_summary, baseline_resume_json

    # After input normalization
    normalized_description: Optional[str]
    needs_jd_expansion: bool

    # After JD expansion (if applied)
    expanded_description: Optional[str]

    # After skill extraction — validated JSON
    extracted_skills: Optional[dict[str, Any]]
    extraction_validation_error: Optional[str]

    # After questionnaire generation
    questionnaire: Optional[list[dict[str, Any]]]
    questionnaire_validation_error: Optional[str]

    # User answers (injected by API): question_id -> "yes" | "no" only (concept awareness)
    user_answers: Optional[dict[str, str]]

    # After evaluation (rich: scores per area, missing_concepts, strong_areas, summary, concepts_to_prepare)
    evaluation_result: Optional[dict[str, Any]]
    evaluation_validation_error: Optional[str]

    # After skill gap analysis
    skill_gap_summary: Optional[dict[str, Any]]
    gap_validation_error: Optional[str]

    # After resume generation
    resume_structured: Optional[dict[str, Any]]
    resume_validation_error: Optional[str]

    # After docx rendering
    docx_path: Optional[str]
    docx_error: Optional[str]

    # Control / retry
    retry_count: int
    current_node: Optional[str]
    error: Optional[str]
