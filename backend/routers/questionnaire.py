"""
Submit user answers and continue workflow to evaluation → gap → resume → docx.
"""
import json
import logging
import threading
from queue import Queue
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.models.schemas.job import UserAnswersPayload, EvaluationResultResponse
from backend.utils.auth import get_current_user
from backend.models.user import User
from backend.db import get_db_dependency, get_db
from backend.models.job import JobSubmission
from backend.models.skill_gap import SkillGapRecord
from backend.models.resume import ResumeVersion
from backend.routers.graph.workflow import build_resume_workflow
from backend.routers.graph.state import ResumeWorkflowState
from backend.routers.profile import get_user_baseline

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/questionnaire", tags=["questionnaire"])


def _sse_message(event: str, data: dict | list) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _run_submit_to_queue(
    user_id: int,
    job_submission_id: int,
    answers: dict[str, str],
    queue: "Queue[dict[str, Any]]",
) -> None:
    """Run submit workflow in thread; put progress and done/error into queue."""
    try:
        queue.put({"type": "progress", "progress_pct": 0})
        with get_db() as db:
            submission = db.query(JobSubmission).filter(
                JobSubmission.id == job_submission_id,
                JobSubmission.user_id == user_id,
            ).first()
            if not submission:
                queue.put({"type": "error", "detail": "Job submission not found"})
                return
            if not submission.questionnaire:
                queue.put({"type": "error", "detail": "Questionnaire not generated"})
                return
            min_questions = 3
            if len(submission.questionnaire) < min_questions:
                queue.put({"type": "error", "detail": f"Answer at least {min_questions} questions first"})
                return
<<<<<<< HEAD
            allowed = {"yes", "no"}
            for qid, val in (answers or {}).items():
                if val is not None and str(val).strip().lower() not in allowed:
                    queue.put({"type": "error", "detail": f"Answer for '{qid}' must be 'yes' or 'no'"})
                    return

            config = {"configurable": {"thread_id": str(submission.id)}}
=======
            from backend.models.schemas.job import VALID_ANSWER_VALUES
            allowed = VALID_ANSWER_VALUES
            for qid, val in (answers or {}).items():
                if val is not None and str(val).strip().lower().replace(" ", "_") not in allowed:
                    queue.put({"type": "error", "detail": f"Answer for '{qid}' must be 'yes', 'no', or 'a_bit'"})
                    return

            config = {"configurable": {"thread_id": str(submission.id)}}
            baseline = get_user_baseline(db, user_id)
>>>>>>> 1aa7648 (deployment changes + bug fixes)
            initial: ResumeWorkflowState = {
                "job_submission_id": submission.id,
                "user_id": user_id,
                "job_title": submission.job_title,
                "job_description_raw": submission.job_description_raw,
<<<<<<< HEAD
=======
                "company_name": getattr(submission, "company_name", None),
>>>>>>> 1aa7648 (deployment changes + bug fixes)
                "normalized_description": submission.normalized_input,
                "extracted_skills": submission.extracted_skills,
                "questionnaire": submission.questionnaire,
                "user_answers": answers,
                "retry_count": 0,
<<<<<<< HEAD
=======
                "user_baseline": baseline,
>>>>>>> 1aa7648 (deployment changes + bug fixes)
            }
            queue.put({"type": "progress", "progress_pct": 15})

            workflow = build_resume_workflow()
            result = workflow.invoke(initial, config=config)
            queue.put({"type": "progress", "progress_pct": 90})

            if not result or not isinstance(result, dict):
                queue.put({"type": "error", "detail": "Workflow failed"})
                return
            if result.get("error"):
                queue.put({"type": "error", "detail": result["error"]})
                return

            submission.user_answers = answers
            submission.evaluation_result = result.get("evaluation_result")
            submission.skill_gap_summary = result.get("skill_gap_summary")
            submission.status = "resume_generated"
            db.commit()

            if result.get("skill_gap_summary"):
                gap_record = SkillGapRecord(
                    user_id=user_id,
                    job_submission_id=submission.id,
                    gap_summary=result["skill_gap_summary"],
                    scores_by_area=result.get("evaluation_result", {}).get("scores"),
                    resume_risk_claims=result["skill_gap_summary"].get("resume_risk_claims"),
                )
                db.add(gap_record)
                db.commit()

            if result.get("resume_structured") and result.get("docx_path"):
<<<<<<< HEAD
=======
                from pathlib import Path
                from backend.services.resume_builder import render_resume_pdf
                from backend.models.schemas.resume import ResumeStructured
                next_ver = (
                    db.query(ResumeVersion)
                    .filter(
                        ResumeVersion.job_submission_id == submission.id,
                        ResumeVersion.user_id == user_id,
                    )
                    .count()
                ) + 1
>>>>>>> 1aa7648 (deployment changes + bug fixes)
                rv = ResumeVersion(
                    user_id=user_id,
                    job_submission_id=submission.id,
                    content_json=json.dumps(result["resume_structured"]) if isinstance(result["resume_structured"], dict) else result["resume_structured"],
                    file_path=result["docx_path"],
<<<<<<< HEAD
                )
                db.add(rv)
                db.commit()
=======
                    version=next_ver,
                )
                db.add(rv)
                db.commit()
                try:
                    pdf_path = Path(result["docx_path"]).with_suffix(".pdf")
                    resume_obj = ResumeStructured.model_validate(result["resume_structured"])
                    render_resume_pdf(resume_obj, pdf_path)
                    rv.file_path_pdf = str(pdf_path)
                    db.commit()
                except Exception as e:
                    logger.warning("PDF generation failed: %s", e)
>>>>>>> 1aa7648 (deployment changes + bug fixes)

            eval_result = result.get("evaluation_result") or {}
            queue.put({"type": "progress", "progress_pct": 100})
            queue.put({
                "type": "done",
                "scores": eval_result.get("scores", []),
                "overall_score": eval_result.get("overall_score"),
                "summary": eval_result.get("summary"),
                "concepts_to_prepare": eval_result.get("concepts_to_prepare"),
            })
    except Exception as e:
        logger.exception("Submit to queue failed")
        queue.put({"type": "error", "detail": str(e)})


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

    # Validate answers: "yes", "no", or "a_bit" per question
    from backend.models.schemas.job import VALID_ANSWER_VALUES
    allowed = VALID_ANSWER_VALUES
    for qid, val in (body.answers or {}).items():
        if val is not None and str(val).strip().lower().replace(" ", "_") not in allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Answer for '{qid}' must be 'yes', 'no', or 'a_bit'.",
            )

    config = {"configurable": {"thread_id": str(submission.id)}}
    baseline = get_user_baseline(db, current_user.id)
    initial: ResumeWorkflowState = {
        "job_submission_id": submission.id,
        "user_id": current_user.id,
        "job_title": submission.job_title,
        "job_description_raw": submission.job_description_raw,
        "company_name": getattr(submission, "company_name", None),
        "normalized_description": submission.normalized_input,
        "extracted_skills": submission.extracted_skills,
        "questionnaire": submission.questionnaire,
        "user_answers": body.answers,
        "retry_count": 0,
        "user_baseline": baseline,
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

    # Persist resume version, docx path, and optional PDF
    if result.get("resume_structured") and result.get("docx_path"):
        import json
        from pathlib import Path
        from backend.services.resume_builder import render_resume_pdf
        from backend.models.schemas.resume import ResumeStructured
        next_ver = (
            db.query(ResumeVersion)
            .filter(
                ResumeVersion.job_submission_id == submission.id,
                ResumeVersion.user_id == current_user.id,
            )
            .count()
        ) + 1
        rv = ResumeVersion(
            user_id=current_user.id,
            job_submission_id=submission.id,
            content_json=json.dumps(result["resume_structured"]) if isinstance(result["resume_structured"], dict) else result["resume_structured"],
            file_path=result["docx_path"],
            version=next_ver,
        )
        db.add(rv)
        db.commit()
        try:
            pdf_path = Path(result["docx_path"]).with_suffix(".pdf")
            resume_obj = ResumeStructured.model_validate(result["resume_structured"])
            render_resume_pdf(resume_obj, pdf_path)
            rv.file_path_pdf = str(pdf_path)
            db.commit()
        except Exception as e:
            logger.warning("PDF generation failed: %s", e)

    eval_result = result.get("evaluation_result") or {}
    return EvaluationResultResponse(
        scores=eval_result.get("scores", []),
        overall_score=eval_result.get("overall_score"),
        summary=eval_result.get("summary"),
        concepts_to_prepare=eval_result.get("concepts_to_prepare"),
    )


@router.post("/{job_submission_id}/submit/stream")
def submit_answers_stream(
    job_submission_id: int,
    body: UserAnswersPayload,
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Stream submit: SSE progress (0, 15, 90, 100) then done with EvaluationResultResponse payload."""
    user_id = current_user.id
    answers = body.answers or {}

    def generate():
        queue: Queue[dict[str, Any]] = Queue()
        thread = threading.Thread(
            target=_run_submit_to_queue,
            args=(user_id, job_submission_id, answers, queue),
        )
        thread.start()
        try:
            while True:
                item = queue.get()
                event = item.get("type", "message")
                payload = {k: v for k, v in item.items() if k != "type"}
                yield _sse_message(event, payload)
                if event in ("done", "error"):
                    break
        finally:
            thread.join(timeout=30.0)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
