"""
Skill gap dashboard: list gaps and improvement suggestions.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from backend.utils.auth import get_current_user
from backend.models.user import User
from backend.db import get_db_dependency
from backend.models.job import JobSubmission
from backend.models.skill_gap import SkillGapRecord

router = APIRouter(prefix="/gap", tags=["skill_gap"])


class SkillGapDashboardItem(BaseModel):
    job_submission_id: int
    weaknesses: list[str]
    improvement_suggestions: list[str]
    resume_risk_claims: list[dict]
    overall_gap_severity: str
    scores_by_area: list[dict] | None


class SkillGapDashboardResponse(BaseModel):
    items: list[SkillGapDashboardItem]


@router.get("/{job_submission_id}", response_model=SkillGapDashboardItem)
def get_gap_for_job(
    job_submission_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """Get skill gap summary for a job submission."""
    submission = db.query(JobSubmission).filter(
        JobSubmission.id == job_submission_id,
        JobSubmission.user_id == current_user.id,
    ).first()
    if not submission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    gap = submission.skill_gap_summary or {}
    return SkillGapDashboardItem(
        job_submission_id=submission.id,
        weaknesses=gap.get("weaknesses", []),
        improvement_suggestions=gap.get("improvement_suggestions", []),
        resume_risk_claims=gap.get("resume_risk_claims", []),
        overall_gap_severity=gap.get("overall_gap_severity", "medium"),
        scores_by_area=submission.evaluation_result.get("scores") if submission.evaluation_result else None,
    )


@router.get("/", response_model=SkillGapDashboardResponse)
def list_gaps(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """List all skill gap records for the current user."""
    records = db.query(SkillGapRecord).filter(SkillGapRecord.user_id == current_user.id).order_by(SkillGapRecord.created_at.desc()).all()
    items = [
        SkillGapDashboardItem(
            job_submission_id=r.job_submission_id or 0,
            weaknesses=(r.gap_summary or {}).get("weaknesses", []),
            improvement_suggestions=(r.gap_summary or {}).get("improvement_suggestions", []),
            resume_risk_claims=(r.gap_summary or {}).get("resume_risk_claims", []),
            overall_gap_severity=(r.gap_summary or {}).get("overall_gap_severity", "medium"),
            scores_by_area=r.scores_by_area,
        )
        for r in records
    ]
    return SkillGapDashboardResponse(items=items)
