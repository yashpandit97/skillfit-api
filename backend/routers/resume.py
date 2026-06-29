"""
Resume preview (structured JSON), versions, diff, and .docx download.
"""
import json
import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from backend.utils.auth import get_current_user
from backend.models.user import User
from backend.db import get_db_dependency
from backend.models.job import JobSubmission
from backend.models.resume import ResumeVersion
from backend.models.share_token import ShareToken

router = APIRouter(prefix="/resume", tags=["resume"])


class ResumeVersionItem(BaseModel):
    id: int
    version: int
    created_at: str


class ResumeVersionsResponse(BaseModel):
    job_submission_id: int
    versions: list[ResumeVersionItem]


class ResumePreviewResponse(BaseModel):
    job_submission_id: int
    resume_structured: dict
    docx_path: str | None


class ShareRequest(BaseModel):
    job_submission_id: int
    email: str | None = None


class ShareResponse(BaseModel):
    share_url: str
    expires_at: str


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


@router.get("/versions/{job_submission_id}", response_model=ResumeVersionsResponse)
def list_versions(
    job_submission_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """List resume versions for a job submission."""
    sub = db.query(JobSubmission).filter(
        JobSubmission.id == job_submission_id,
        JobSubmission.user_id == current_user.id,
    ).first()
    if not sub:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    rows = (
        db.query(ResumeVersion)
        .filter(
            ResumeVersion.job_submission_id == job_submission_id,
            ResumeVersion.user_id == current_user.id,
        )
        .order_by(ResumeVersion.version.desc())
        .all()
    )
    return ResumeVersionsResponse(
        job_submission_id=job_submission_id,
        versions=[
            ResumeVersionItem(id=r.id, version=r.version, created_at=r.created_at.isoformat() if r.created_at else "")
            for r in rows
        ],
    )


def _resume_to_bullets(data: dict) -> list[tuple[str, str]]:
    """Flatten resume to (section_heading, bullet_text) for diff."""
    out: list[tuple[str, str]] = []
    if data.get("summary"):
        out.append(("Summary", data["summary"]))
    for sec in data.get("sections") or []:
        heading = sec.get("heading", "")
        for b in sec.get("bullets") or []:
            out.append((heading, (b.get("text") or "")))
    return out


@router.get("/diff/{job_submission_id}")
def resume_diff(
    job_submission_id: int,
    v1: int,
    v2: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """Structured diff between two resume versions (section/bullet level)."""
    sub = db.query(JobSubmission).filter(
        JobSubmission.id == job_submission_id,
        JobSubmission.user_id == current_user.id,
    ).first()
    if not sub:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    r1 = db.query(ResumeVersion).filter(
        ResumeVersion.job_submission_id == job_submission_id,
        ResumeVersion.user_id == current_user.id,
        ResumeVersion.version == v1,
    ).first()
    r2 = db.query(ResumeVersion).filter(
        ResumeVersion.job_submission_id == job_submission_id,
        ResumeVersion.user_id == current_user.id,
        ResumeVersion.version == v2,
    ).first()
    if not r1 or not r2:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    d1 = json.loads(r1.content_json) if isinstance(r1.content_json, str) else (r1.content_json or {})
    d2 = json.loads(r2.content_json) if isinstance(r2.content_json, str) else (r2.content_json or {})
    bullets1 = _resume_to_bullets(d1)
    bullets2 = _resume_to_bullets(d2)
    set1 = {(h, t) for h, t in bullets1}
    set2 = {(h, t) for h, t in bullets2}
    only1 = [{"section": h, "text": t, "status": "removed"} for h, t in bullets1 if (h, t) not in set2]
    only2 = [{"section": h, "text": t, "status": "added"} for h, t in bullets2 if (h, t) not in set1]
    common = [{"section": h, "text": t, "status": "unchanged"} for h, t in bullets2 if (h, t) in set1]
    return {
        "job_submission_id": job_submission_id,
        "v1": v1,
        "v2": v2,
        "removed": only1,
        "added": only2,
        "unchanged": common,
    }


@router.get("/preview/{job_submission_id}/version/{version}")
def resume_preview_version(
    job_submission_id: int,
    version: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """Get structured resume for a specific version (for preview)."""
    sub = db.query(JobSubmission).filter(
        JobSubmission.id == job_submission_id,
        JobSubmission.user_id == current_user.id,
    ).first()
    if not sub:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    rv = db.query(ResumeVersion).filter(
        ResumeVersion.job_submission_id == job_submission_id,
        ResumeVersion.user_id == current_user.id,
        ResumeVersion.version == version,
    ).first()
    if not rv or not rv.content_json:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    structured = json.loads(rv.content_json) if isinstance(rv.content_json, str) else rv.content_json
    return ResumePreviewResponse(
        job_submission_id=job_submission_id,
        resume_structured=structured,
        docx_path=rv.file_path,
    )


@router.get("/ats-score/{job_submission_id}")
def ats_score(
    job_submission_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """Compute ATS keyword coverage score from extracted ats_keywords vs resume text."""
    sub = db.query(JobSubmission).filter(
        JobSubmission.id == job_submission_id,
        JobSubmission.user_id == current_user.id,
    ).first()
    if not sub:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    rv = (
        db.query(ResumeVersion)
        .filter(
            ResumeVersion.job_submission_id == job_submission_id,
            ResumeVersion.user_id == current_user.id,
        )
        .order_by(ResumeVersion.created_at.desc())
        .first()
    )
    if not rv or not rv.content_json:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found")
    extracted = sub.extracted_skills or {}
    keywords = extracted.get("ats_keywords") or []
    if not isinstance(keywords, list):
        keywords = []
    data = json.loads(rv.content_json) if isinstance(rv.content_json, str) else (rv.content_json or {})
    text_parts = [data.get("summary") or ""]
    for sec in data.get("sections") or []:
        for b in sec.get("bullets") or []:
            text_parts.append(b.get("text") or "")
    resume_text = " ".join(text_parts).lower()
    matched = [k for k in keywords if isinstance(k, str) and k.lower() in resume_text]
    missing = [k for k in keywords if isinstance(k, str) and k.lower() not in resume_text]
    score = round(100 * len(matched) / len(keywords)) if keywords else 0
    return {"score": min(100, score), "matched_keywords": matched, "missing_keywords": missing[:20]}


@router.get("/download/{job_submission_id}/pdf")
def download_resume_pdf(
    job_submission_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """Download generated .pdf file if available."""
    rv = db.query(ResumeVersion).filter(
        ResumeVersion.job_submission_id == job_submission_id,
        ResumeVersion.user_id == current_user.id,
    ).order_by(ResumeVersion.created_at.desc()).first()
    if not rv or not getattr(rv, "file_path_pdf", None) or not os.path.isfile(rv.file_path_pdf):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PDF not found")
    return FileResponse(rv.file_path_pdf, filename=Path(rv.file_path_pdf).name, media_type="application/pdf")


@router.post("/tailored-summary")
def get_tailored_summary(
    body: ShareRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """Generate a 1-2 sentence tailored one-liner for applying to this role."""
    sub = db.query(JobSubmission).filter(
        JobSubmission.id == body.job_submission_id,
        JobSubmission.user_id == current_user.id,
    ).first()
    if not sub:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    rv = (
        db.query(ResumeVersion)
        .filter(
            ResumeVersion.job_submission_id == body.job_submission_id,
            ResumeVersion.user_id == current_user.id,
        )
        .order_by(ResumeVersion.created_at.desc())
        .first()
    )
    if not rv or not rv.content_json:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found")
    from backend.services.gemini_llm_service import get_llm_service
    data = json.loads(rv.content_json) if isinstance(rv.content_json, str) else rv.content_json
    summary = data.get("summary") or ""
    job_title = sub.job_title or "the role"
    jd = (sub.normalized_input or sub.job_description_raw or "")[:1500]
    llm = get_llm_service()
    system = "You are a career coach. Generate exactly 1-2 sentences that the candidate can use as a tailored one-liner when applying to this specific role. Base it on their resume summary and the job. Output only the one-liner text, no quotes or prefix."
    user = f"Resume summary: {summary}\n\nJob title: {job_title}\n\nJob context: {jd}"
    raw = llm.invoke(system, user, stage="tailored_summary")
    one_liner = (raw or "").strip().strip('"').strip("'")
    return {"one_liner": one_liner[:500]}


@router.post("/share", response_model=ShareResponse)
def create_share_link(
    body: ShareRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """Create a short-lived share link for the resume. Optional email for future email delivery."""
    sub = db.query(JobSubmission).filter(
        JobSubmission.id == body.job_submission_id,
        JobSubmission.user_id == current_user.id,
    ).first()
    if not sub:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    import secrets
    from datetime import datetime as dt_util, timedelta
    token = secrets.token_urlsafe(32)
    expires_at = dt_util.utcnow() + timedelta(hours=24)
    st = ShareToken(
        token=token,
        job_submission_id=body.job_submission_id,
        user_id=current_user.id,
        expires_at=expires_at,
    )
    db.add(st)
    db.commit()
    import os
    base_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
    share_url = f"{base_url}/shared/{token}"
    return ShareResponse(share_url=share_url, expires_at=expires_at.isoformat())


@router.get("/shared/{token}")
def get_shared_resume(
    token: str,
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """Unauthenticated: resolve share token and return resume file or 404."""
    from datetime import datetime as dt
    st = db.query(ShareToken).filter(ShareToken.token == token).first()
    if not st or st.expires_at < dt.utcnow():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link expired or invalid")
    rv = db.query(ResumeVersion).filter(
        ResumeVersion.job_submission_id == st.job_submission_id,
        ResumeVersion.user_id == st.user_id,
    ).order_by(ResumeVersion.created_at.desc()).first()
    if not rv or not rv.file_path or not os.path.isfile(rv.file_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found")
    return FileResponse(rv.file_path, filename=Path(rv.file_path).name, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


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
