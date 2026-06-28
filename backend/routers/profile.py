"""
User profile: baseline resume, skills, experience summary.
"""
import json
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, status, UploadFile
from sqlalchemy.orm import Session

from backend.db import get_db_dependency
from backend.models.user import User
from backend.models.user_profile import UserProfile
from backend.models.schemas.profile import ProfileUpdate, ProfileResponse
from backend.utils.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/profile", tags=["profile"])


def get_user_baseline(db: Session, user_id: int) -> dict | None:
    """Return dict for workflow state user_baseline (skills, experience_summary, baseline_resume_json) or None."""
    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if not profile:
        return None
    baseline = None
    if profile.baseline_resume_json:
        try:
            baseline = json.loads(profile.baseline_resume_json) if isinstance(profile.baseline_resume_json, str) else profile.baseline_resume_json
        except (TypeError, json.JSONDecodeError):
            baseline = {"raw_text": (profile.baseline_resume_json or "")[:5000]}
    skills = profile.skills_json if isinstance(profile.skills_json, list) else []
    if not skills and not profile.experience_summary and not baseline:
        return None
    return {
        "skills": skills,
        "experience_summary": profile.experience_summary,
        "baseline_resume_json": baseline,
    }


def _get_or_create_profile(db: Session, user_id: int) -> UserProfile:
    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if not profile:
        profile = UserProfile(user_id=user_id)
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


def _extract_text_from_docx(file_path: Path) -> str:
    """Extract raw text from .docx for baseline snippet."""
    try:
        from docx import Document
        doc = Document(file_path)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as e:
        logger.warning("Docx extract failed: %s", e)
        return ""


def _extract_text_from_pdf(file_path: Path) -> str:
    """Extract raw text from PDF for baseline snippet."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(file_path))
        parts = []
        for page in reader.pages:
            text = page.extract_text()
            if text and text.strip():
                parts.append(text.strip())
        return "\n".join(parts)
    except Exception as e:
        logger.warning("PDF extract failed: %s", e)
        return ""


ALLOWED_RESUME_EXTENSIONS = (".pdf", ".docx", ".doc")


def _extract_text_from_resume(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return _extract_text_from_pdf(file_path)
    if suffix in (".docx", ".doc"):
        return _extract_text_from_docx(file_path)
    return ""


@router.get("", response_model=ProfileResponse)
def get_profile(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """Get current user's profile (skills, experience, baseline resume snippet)."""
    profile = _get_or_create_profile(db, current_user.id)
    skills = profile.skills_json if isinstance(profile.skills_json, list) else []
    baseline = None
    if profile.baseline_resume_json:
        try:
            baseline = json.loads(profile.baseline_resume_json) if isinstance(profile.baseline_resume_json, str) else profile.baseline_resume_json
        except (TypeError, json.JSONDecodeError):
            baseline = {"raw_text": profile.baseline_resume_json[:2000] if profile.baseline_resume_json else ""}
    return ProfileResponse(
        skills=skills,
        experience_summary=profile.experience_summary,
        baseline_resume_json=baseline,
    )


@router.put("", response_model=ProfileResponse)
def update_profile(
    body: ProfileUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """Update profile (skills, experience summary)."""
    profile = _get_or_create_profile(db, current_user.id)
    if body.skills is not None:
        profile.skills_json = body.skills
    if body.experience_summary is not None:
        profile.experience_summary = body.experience_summary
    db.commit()
    db.refresh(profile)
    skills = profile.skills_json if isinstance(profile.skills_json, list) else []
    baseline = None
    if profile.baseline_resume_json:
        try:
            baseline = json.loads(profile.baseline_resume_json) if isinstance(profile.baseline_resume_json, str) else profile.baseline_resume_json
        except (TypeError, json.JSONDecodeError):
            baseline = {"raw_text": (profile.baseline_resume_json or "")[:2000]}
    return ProfileResponse(
        skills=skills,
        experience_summary=profile.experience_summary,
        baseline_resume_json=baseline,
    )


@router.put("/resume")
def upload_baseline_resume(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
    file: UploadFile = File(...),
):
    """Upload a resume file; extract text and store as baseline snippet."""
    if not file.filename or not file.filename.lower().endswith(ALLOWED_RESUME_EXTENSIONS):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only .pdf or .docx files allowed")
    import tempfile
    suffix = Path(file.filename).suffix.lower() or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = file.file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        text = _extract_text_from_resume(tmp_path)
        if not text.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Could not extract text from file")
        profile = _get_or_create_profile(db, current_user.id)
        profile.baseline_resume_json = json.dumps({"raw_text": text[:10000]})
        db.commit()
        db.refresh(profile)
        return {"message": "Resume uploaded", "chars_extracted": len(text)}
    finally:
        tmp_path.unlink(missing_ok=True)
