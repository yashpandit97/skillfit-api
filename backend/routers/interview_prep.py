"""
Interview prep: concepts to prepare, likely questions, key points, study links from skill gap.
"""
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.db import get_db_dependency
from backend.models.user import User
from backend.models.job import JobSubmission
from backend.utils.auth import get_current_user
from backend.utils.study_urls import sanitize_study_urls

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/interview-prep", tags=["interview_prep"])


class LikelyQuestionItem(BaseModel):
    concept: str
    question: str
    key_points: list[str] = []


class InterviewPrepResponse(BaseModel):
    job_submission_id: int
    concepts_to_prepare: list[str] = []
    likely_questions: list[LikelyQuestionItem] = []
    study_links: list[dict] = []  # { "text": str, "websites": [], "youtube": [] }


def _normalize_gap_items(raw_list: list) -> list[dict]:
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
                "study_urls": sanitize_study_urls(su),
            })
        else:
            out.append({"text": "", "study_urls": {"websites": [], "youtube": []}})
    return out


@router.get("/{job_submission_id}", response_model=InterviewPrepResponse)
def get_interview_prep(
    job_submission_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """Get interview prep: concepts to prepare, likely questions (optional LLM), study links from gap."""
    sub = db.query(JobSubmission).filter(
        JobSubmission.id == job_submission_id,
        JobSubmission.user_id == current_user.id,
    ).first()
    if not sub:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    evaluation = sub.evaluation_result or {}
    gap = sub.skill_gap_summary or {}
    concepts_to_prepare = evaluation.get("concepts_to_prepare") or []
    if not isinstance(concepts_to_prepare, list):
        concepts_to_prepare = []
    weaknesses = _normalize_gap_items(gap.get("weaknesses", []))
    improvements = _normalize_gap_items(gap.get("improvement_suggestions", []))
    study_links = []
    for w in weaknesses + improvements:
        study_links.append({
            "text": w.get("text", ""),
            "websites": sanitize_study_urls(w.get("study_urls") or {}).get("websites", []),
            "youtube": [],
        })
    likely_questions: list[LikelyQuestionItem] = []
    try:
        from backend.services.gemini_llm_service import get_llm_service
        llm = get_llm_service()
        system = (
            "You are a career coach. For each concept given, generate exactly 1 likely interview question and 2-3 key points to mention. "
            "Output a JSON array of objects with keys: concept, question, key_points (array of strings). No markdown, no code fences."
        )
        user = "Concepts to prepare: " + json.dumps(concepts_to_prepare[:15])
        raw = llm.invoke(system, user, stage="interview_prep")
        import re
        text = (raw or "").strip()
        if "```" in text:
            for m in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text):
                text = m.group(1).strip()
                break
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            from backend.utils.json_extract import extract_json_from_llm_response
            data = extract_json_from_llm_response(raw or "")
        if isinstance(data, list):
            for item in data[:20]:
                if isinstance(item, dict) and item.get("concept"):
                    likely_questions.append(LikelyQuestionItem(
                        concept=item.get("concept", ""),
                        question=item.get("question", ""),
                        key_points=item.get("key_points") if isinstance(item.get("key_points"), list) else [],
                    ))
        elif isinstance(data, dict) and data.get("concept"):
            likely_questions.append(LikelyQuestionItem(
                concept=data.get("concept", ""),
                question=data.get("question", ""),
                key_points=data.get("key_points") if isinstance(data.get("key_points"), list) else [],
            ))
    except Exception as e:
        logger.warning("Interview prep LLM failed: %s", e)
    return InterviewPrepResponse(
        job_submission_id=job_submission_id,
        concepts_to_prepare=concepts_to_prepare,
        likely_questions=likely_questions,
        study_links=study_links,
    )
