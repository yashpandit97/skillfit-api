"""
Resume preview (structured JSON) and .docx download.
"""
import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from backend.utils.auth import get_current_user
from backend.models.user import User
from backend.db import get_db_dependency
from backend.models.job import JobSubmission
from backend.models.resume import ResumeVersion

router = APIRouter(prefix="/resume", tags=["resume"])


class ResumePreviewResponse(BaseModel):
    job_submission_id: int
    resume_structured: dict
    docx_path: str | None


@router.get("/preview/{job_submission_id}", response_model=ResumePreviewResponse)
def resume_preview(
    job_submission_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """Get structured resume for preview (with red-highlight / deficiency markers)."""
    submission = db.query(JobSubmission).filter(
        JobSubmission.id == job_submission_id,
        JobSubmission.user_id == current_user.id,
    ).first()
    if not submission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    # Resume structured and docx path are stored in ResumeVersion or we need to run workflow to get docx
    # For simplicity: if we have a resume version for this job, return it; else return evaluation-based structure from state
    rv = db.query(ResumeVersion).filter(
        ResumeVersion.job_submission_id == job_submission_id,
        ResumeVersion.user_id == current_user.id,
    ).order_by(ResumeVersion.created_at.desc()).first()
    if rv and rv.content_json:
        import json
        structured = json.loads(rv.content_json) if isinstance(rv.content_json, str) else rv.content_json
        return ResumePreviewResponse(
            job_submission_id=job_submission_id,
            resume_structured=structured,
            docx_path=rv.file_path,
        )
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not yet generated for this job")


@router.get("/download/{job_submission_id}")
def download_resume(
    job_submission_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """Download generated .docx file."""
    rv = db.query(ResumeVersion).filter(
        ResumeVersion.job_submission_id == job_submission_id,
        ResumeVersion.user_id == current_user.id,
    ).order_by(ResumeVersion.created_at.desc()).first()
    if not rv or not rv.file_path or not os.path.isfile(rv.file_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume file not found")
    return FileResponse(rv.file_path, filename=Path(rv.file_path).name, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
