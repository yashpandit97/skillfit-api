"""
Job input and workflow trigger. No graph logic here — call workflow.
"""
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.models.schemas.job import (
    JobInputRequest,
    JobInputResponse,
    QuestionnaireResponse,
    StageAnswersRequest,
    StageAnswersResponse,
)
from backend.utils.auth import get_current_user
from backend.models.user import User
from backend.db import get_db_dependency
from backend.models.job import JobSubmission
from backend.routers.graph.state import ResumeWorkflowState
from backend.routers.graph.nodes import (
    input_normalization_node,
    jd_expansion_node,
    skill_extraction_node,
)
from backend.routers.graph.nodes.questionnaire_generation import (
    stream_questionnaire_stage_1,
    stream_questionnaire_stage_next,
    MIN_QUESTIONS_STAGE_1,
    _total_stages_from_question_count,
)

# Cap questionnaire so it ends after this many stages; then we show "Submit & generate resume".
MAX_QUESTIONNAIRE_STAGES = 6

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/job", tags=["job"])


@router.post("/input", response_model=JobInputResponse)
def job_input(
    body: JobInputRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """Submit job title and/or JD. Runs up to skill extraction, then generates stage 1 (LLM decides how many questions, 3–12)."""
    if not body.job_title and not body.job_description:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provide job_title or job_description")

    submission = JobSubmission(
        user_id=current_user.id,
        job_title=body.job_title,
        job_description_raw=body.job_description,
        status="draft",
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)

    initial: ResumeWorkflowState = {
        "job_title": body.job_title,
        "job_description_raw": body.job_description,
        "user_id": current_user.id,
        "job_submission_id": submission.id,
        "retry_count": 0,
    }
    state = dict(initial)
    state.update(input_normalization_node(state))
    if state.get("error"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=state["error"])
    if state.get("needs_jd_expansion"):
        state.update(jd_expansion_node(state))
    state.update(skill_extraction_node(state))
    extracted = state.get("extracted_skills")
    if not extracted:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=state.get("extraction_validation_error") or "Skill extraction failed",
        )

    description_text = state.get("expanded_description") or state.get("normalized_description") or ""
    stage1_questions = list(stream_questionnaire_stage_1(extracted, description_text=description_text))
    if len(stage1_questions) < MIN_QUESTIONS_STAGE_1:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not generate enough questions for stage 1 (got {len(stage1_questions)}, need at least {MIN_QUESTIONS_STAGE_1})",
        )

    submission.normalized_input = state.get("expanded_description") or state.get("normalized_description")
    submission.extracted_skills = extracted
    submission.questionnaire = stage1_questions
    submission.status = "questionnaire_ready"
    submission.current_stage = 1
    db.commit()

    return JobInputResponse(
        job_submission_id=submission.id,
        job_title=submission.job_title,
        normalized_description=submission.normalized_input,
        extracted_skills=submission.extracted_skills,
        questionnaire=submission.questionnaire,
        message="Questionnaire ready",
    )


@router.get("/{job_submission_id}/questionnaire", response_model=QuestionnaireResponse)
def get_questionnaire(
    job_submission_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """Get generated questionnaire for a job submission."""
    submission = db.query(JobSubmission).filter(
        JobSubmission.id == job_submission_id,
        JobSubmission.user_id == current_user.id,
    ).first()
    if not submission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job submission not found")
    if not submission.questionnaire:
        from backend.models.schemas.job import QuestionnaireItem
        current_stage = getattr(submission, "current_stage", 1) or 1
        return QuestionnaireResponse(
            questions=[],
            current_stage=current_stage,
            total_stages=1,
            ready=False,
        )
    from backend.models.schemas.job import QuestionnaireItem
    questions = [QuestionnaireItem(**q) for q in submission.questionnaire]
    current_stage = getattr(submission, "current_stage", 1) or 1
    total_stages = _total_stages_from_question_count(submission.questionnaire or [])
    return QuestionnaireResponse(
        questions=questions,
        current_stage=current_stage,
        total_stages=total_stages,
        ready=True,
    )


@router.post("/{job_submission_id}/questionnaire/stage-answers", response_model=StageAnswersResponse)
def submit_stage_answers(
    job_submission_id: int,
    body: StageAnswersRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """Submit answers for one stage. Backend generates next batch only from questions user answered YES to; LLM decides how many (2–10). If 0 next questions, done=True."""
    submission = db.query(JobSubmission).filter(
        JobSubmission.id == job_submission_id,
        JobSubmission.user_id == current_user.id,
    ).first()
    if not submission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job submission not found")
    if not submission.questionnaire or not submission.extracted_skills:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Questionnaire not ready")
    current_stage = getattr(submission, "current_stage", 1) or 1
    total_stages = _total_stages_from_question_count(submission.questionnaire)
    if body.stage != current_stage:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Expected answers for stage {current_stage}, got {body.stage}",
        )
    # Current stage questions: by stage number (supports variable questions per stage)
    has_stages = any(isinstance(q, dict) and q.get("stage") is not None for q in (submission.questionnaire or []))
    if has_stages:
        stage_questions = [q for q in submission.questionnaire if isinstance(q, dict) and q.get("stage") == body.stage]
    else:
        # Legacy: no stage field — treat all questions as stage 1
        stage_questions = list(submission.questionnaire) if body.stage == 1 else []
    if not stage_questions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No questions found for stage {body.stage}",
        )
    allowed = {"yes", "no"}
    # Normalize: missing or blank => "no" so we don't fail on client omissions
    stage_answers = {}
    for q in stage_questions:
        qid = q.get("id")
        val = (body.answers or {}).get(qid)
        if val is None or (isinstance(val, str) and val.strip() == ""):
            val = "no"
        normalized = str(val).strip().lower() if val else "no"
        if normalized not in allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Answer for '{qid}' must be 'yes' or 'no'.",
            )
        stage_answers[qid] = normalized
    # Merge answers (use normalized stage answers so every stage question is present)
    existing = submission.user_answers or {}
    merged = {**existing, **stage_answers}
    submission.user_answers = merged

    # Next stage: generate only from concepts user answered YES to in this stage
    yes_questions = [q for q in stage_questions if (merged.get(q.get("id")) or "").strip().lower() == "yes"]
    next_stage = body.stage + 1
    previous_all_questions = submission.questionnaire
    next_questions = []
    if next_stage <= MAX_QUESTIONNAIRE_STAGES:
        try:
            next_questions = list(
                stream_questionnaire_stage_next(
                    submission.extracted_skills,
                    next_stage,
                    previous_all_questions,
                    yes_questions,
                )
            )
        except Exception as e:
            logger.exception("Next stage questionnaire generation failed")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Could not generate next stage questions: {e!s}",
            ) from e

    if next_questions:
        # Add new stage and return next questions
        from backend.models.schemas.job import QuestionnaireItem
        next_items = []
        for q in next_questions:
            item = QuestionnaireItem(
                id=str(q.get("id", "")),
                concept=str(q.get("concept", "")),
                category=str(q.get("category", "fundamentals")),
                description=q.get("description"),
                stage=q.get("stage"),
            )
            next_items.append(item)
        submission.questionnaire = submission.questionnaire + next_questions
        submission.current_stage = next_stage
        db.commit()
        new_total = _total_stages_from_question_count(submission.questionnaire)
        return StageAnswersResponse(
            next_stage_questions=next_items,
            current_stage=next_stage,
            done=False,
            message=f"Stage {next_stage} of {new_total} ready.",
        )
    else:
        # No more questions: user can submit to generate resume
        db.commit()
        return StageAnswersResponse(
            next_stage_questions=[],
            current_stage=current_stage,
            done=True,
            message="No more questions. Submit to generate your resume.",
        )


def _sse_message(event: str, data: dict | list) -> str:
    """Format one SSE message: event type + data line."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/input/stream")
def job_input_stream(
    body: JobInputRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """Submit job title and/or JD. Streams questionnaire questions as they are generated (SSE)."""
    if not body.job_title and not body.job_description:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provide job_title or job_description")

    # Capture values before generator runs so we don't touch session-bound objects after yield
    user_id = current_user.id
    job_title = body.job_title
    job_description = body.job_description

    def generate():
        try:
            submission = JobSubmission(
                user_id=user_id,
                job_title=job_title,
                job_description_raw=job_description,
                status="draft",
            )
            db.add(submission)
            db.commit()
            db.refresh(submission)

            yield _sse_message("started", {"job_submission_id": submission.id})

            initial: ResumeWorkflowState = {
                "job_title": job_title,
                "job_description_raw": job_description,
                "user_id": user_id,
                "job_submission_id": submission.id,
                "retry_count": 0,
            }
            state = dict(initial)
            state.update(input_normalization_node(state))
            if state.get("error"):
                yield _sse_message("error", {"detail": state["error"]})
                return
            if state.get("needs_jd_expansion"):
                state.update(jd_expansion_node(state))
            state.update(skill_extraction_node(state))
            extracted = state.get("extracted_skills")
            if not extracted:
                yield _sse_message("error", {"detail": state.get("extraction_validation_error") or "Skill extraction failed"})
                return

            description_text = state.get("expanded_description") or state.get("normalized_description") or ""
            all_questions = []
            try:
                for q in stream_questionnaire_stage_1(extracted, description_text=description_text):
                    all_questions.append(q)
                    yield _sse_message("question", q)
                if all_questions:
                    submission.normalized_input = state.get("expanded_description") or state.get("normalized_description")
                    submission.extracted_skills = extracted
                    submission.questionnaire = all_questions
                    submission.status = "questionnaire_ready"
                    submission.current_stage = 1
                    db.commit()
            except Exception as e:
                logger.exception("Questionnaire stream failed")
                yield _sse_message("error", {"detail": str(e)})
                return

            yield _sse_message("done", {"job_submission_id": submission.id})
        except Exception as e:
            logger.exception("Job input stream failed")
            yield _sse_message("error", {"detail": str(e)})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
