"""
Submit user answers and continue workflow to evaluation → gap → resume → docx.
"""
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.models.schemas.job import UserAnswersPayload, EvaluationResultResponse
from backend.utils.auth import get_current_user
from backend.models.user import User
from backend.db import get_db_dependency
from backend.models.job import JobSubmission
from backend.models.skill_gap import SkillGapRecord
from backend.models.resume import ResumeVersion
from backend.routers.graph.workflow import build_resume_workflow
from backend.routers.graph.state import ResumeWorkflowState

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/questionnaire", tags=["questionnaire"])


@router.post("/{job_submission_id}/submit", response_model=EvaluationResultResponse)
def submit_answers(
    job_submission_id: int,
    body: UserAnswersPayload,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """Submit questionnaire answers. Runs workflow: evaluation → gap analysis → resume → docx."""
    submission = db.query(JobSubmission).filter(
        JobSubmission.id == job_submission_id,
        JobSubmission.user_id == current_user.id,
    ).first()
    if not submission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job submission not found")
    if not submission.questionnaire:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Questionnaire not generated")
    min_questions = 3
    if len(submission.questionnaire) < min_questions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Answer at least {min_questions} questions first ({len(submission.questionnaire)} in questionnaire).",
        )

    # Validate answers: only "yes" or "no" per question
    allowed = {"yes", "no"}
    for qid, val in (body.answers or {}).items():
        if val is not None and str(val).strip().lower() not in allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Answer for '{qid}' must be 'yes' or 'no'.",
            )

    config = {"configurable": {"thread_id": str(submission.id)}}
    initial: ResumeWorkflowState = {
        "job_submission_id": submission.id,
        "user_id": current_user.id,
        "job_title": submission.job_title,
        "job_description_raw": submission.job_description_raw,
        "normalized_description": submission.normalized_input,
        "extracted_skills": submission.extracted_skills,
        "questionnaire": submission.questionnaire,
        "user_answers": body.answers,
        "retry_count": 0,
    }

    workflow = build_resume_workflow()
    result = workflow.invoke(initial, config=config)
    if not result or not isinstance(result, dict):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Workflow failed")
    if result.get("error"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=result["error"])

    submission.user_answers = body.answers
    submission.evaluation_result = result.get("evaluation_result")
    submission.skill_gap_summary = result.get("skill_gap_summary")
    submission.status = "resume_generated"
    db.commit()

    # Persist skill gap record
    if result.get("skill_gap_summary"):
        gap_record = SkillGapRecord(
            user_id=current_user.id,
            job_submission_id=submission.id,
            gap_summary=result["skill_gap_summary"],
            scores_by_area=result.get("evaluation_result", {}).get("scores"),
            resume_risk_claims=result["skill_gap_summary"].get("resume_risk_claims"),
        )
        db.add(gap_record)
        db.commit()

    # Persist resume version and docx path
    if result.get("resume_structured") and result.get("docx_path"):
        import json
        rv = ResumeVersion(
            user_id=current_user.id,
            job_submission_id=submission.id,
            content_json=json.dumps(result["resume_structured"]) if isinstance(result["resume_structured"], dict) else result["resume_structured"],
            file_path=result["docx_path"],
        )
        db.add(rv)
        db.commit()

    eval_result = result.get("evaluation_result") or {}
    return EvaluationResultResponse(
        scores=eval_result.get("scores", []),
        overall_score=eval_result.get("overall_score"),
        summary=eval_result.get("summary"),
        concepts_to_prepare=eval_result.get("concepts_to_prepare"),
    )
