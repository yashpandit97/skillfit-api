"""
Skill gap dashboard: list gaps and improvement suggestions with study URLs.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from backend.utils.auth import get_current_user
from backend.models.user import User
from backend.db import get_db_dependency
from backend.models.job import JobSubmission
from backend.models.skill_gap import SkillGapRecord

router = APIRouter(prefix="/gap", tags=["skill_gap"])


class StudyUrls(BaseModel):
    """Study materials: websites and YouTube URLs for one weakness or suggestion."""
    websites: list[str] = Field(default_factory=list)
    youtube: list[str] = Field(default_factory=list)


class WeaknessItem(BaseModel):
    """One weakness with optional study links."""
    text: str
    study_urls: StudyUrls = Field(default_factory=lambda: StudyUrls())


class ImprovementItem(BaseModel):
    """One improvement suggestion with optional study links."""
    text: str
    study_urls: StudyUrls = Field(default_factory=lambda: StudyUrls())


def _normalize_gap_items(raw_list: list) -> list[dict]:
    """Convert stored gap data (strings or objects) to list of {text, study_urls}."""
    out = []
    for x in raw_list or []:
        if isinstance(x, str):
            out.append({"text": x, "study_urls": {"websites": [], "youtube": []}})
        elif isinstance(x, dict):
            su = x.get("study_urls") or {}
            if not isinstance(su, dict):
                su = {}
            out.append({
                "text": x.get("text", ""),
                "study_urls": {"websites": list(su.get("websites") or []), "youtube": list(su.get("youtube") or [])},
            })
        else:
            out.append({"text": "", "study_urls": {"websites": [], "youtube": []}})
    return out


class SkillGapDashboardItem(BaseModel):
    job_submission_id: int
    weaknesses: list[WeaknessItem]
    improvement_suggestions: list[ImprovementItem]
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
    weaknesses = _normalize_gap_items(gap.get("weaknesses", []))
    improvement_suggestions = _normalize_gap_items(gap.get("improvement_suggestions", []))
    return SkillGapDashboardItem(
        job_submission_id=submission.id,
        weaknesses=[WeaknessItem(**w) for w in weaknesses],
        improvement_suggestions=[ImprovementItem(**i) for i in improvement_suggestions],
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
    items = []
    for r in records:
        gap = r.gap_summary or {}
        weaknesses = _normalize_gap_items(gap.get("weaknesses", []))
        improvement_suggestions = _normalize_gap_items(gap.get("improvement_suggestions", []))
        items.append(
            SkillGapDashboardItem(
                job_submission_id=r.job_submission_id or 0,
                weaknesses=[WeaknessItem(**w) for w in weaknesses],
                improvement_suggestions=[ImprovementItem(**i) for i in improvement_suggestions],
                resume_risk_claims=gap.get("resume_risk_claims", []),
                overall_gap_severity=gap.get("overall_gap_severity", "medium"),
                scores_by_area=r.scores_by_area,
            )
        )
    return SkillGapDashboardResponse(items=items)
