"""
Job input and workflow trigger. No graph logic here — call workflow.
WebSocket endpoint for job input streams LLM progress (progress bar) to the client.
"""
import asyncio
import json
import logging
import threading
from queue import Empty, Queue
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.models.schemas.job import (
    JobInputRequest,
    JobInputResponse,
    JobListResponse,
    JobSubmissionListItem,
    JobCompareResponse,
    JobCompareItem,
    QuestionnaireResponse,
    StageAnswersRequest,
    StageAnswersResponse,
    FitReportResponse,
    FitReportData,
)
from backend.utils.auth import get_current_user
from backend.models.user import User
from backend.db import get_db_dependency, get_db
from backend.models.job import JobSubmission
from backend.routers.graph.state import ResumeWorkflowState
from backend.routers.graph.nodes import (
    input_normalization_node,
    jd_expansion_node,
    skill_extraction_node,
)
from backend.routers.graph.nodes.fit_report import generate_fit_report
from backend.routers.graph.nodes.questionnaire_generation import (
    stream_questionnaire_stage_1,
    stream_questionnaire_stage_next,
    MIN_QUESTIONS_STAGE_1,
    _total_stages_from_question_count,
)
from backend.routers.profile import get_user_baseline

# Cap questionnaire so it ends after this many stages; then we show "Submit & generate resume".
MAX_QUESTIONNAIRE_STAGES = 6

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/job", tags=["job"])


@router.get("", response_model=JobListResponse)
def list_jobs(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """List job submissions for the current user."""
    rows = (
        db.query(JobSubmission)
        .filter(JobSubmission.user_id == current_user.id)
        .order_by(JobSubmission.created_at.desc())
        .all()
    )
    items = [
        JobSubmissionListItem(
            id=r.id,
            job_title=r.job_title,
            company_name=r.company_name,
            status=r.status or "draft",
            workflow_mode=r.workflow_mode,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in rows
    ]
    return JobListResponse(items=items)


@router.get("/compare", response_model=JobCompareResponse)
def compare_jobs(
    job_id_1: int,
    job_id_2: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """Compare two job submissions (extracted skills and gap summary)."""
    for jid in (job_id_1, job_id_2):
        sub = db.query(JobSubmission).filter(
            JobSubmission.id == jid,
            JobSubmission.user_id == current_user.id,
        ).first()
        if not sub:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {jid} not found")
    s1 = db.query(JobSubmission).filter(JobSubmission.id == job_id_1, JobSubmission.user_id == current_user.id).first()
    s2 = db.query(JobSubmission).filter(JobSubmission.id == job_id_2, JobSubmission.user_id == current_user.id).first()
    def item(s: JobSubmission) -> JobCompareItem:
        gap = s.skill_gap_summary or {}
        return JobCompareItem(
            job_submission_id=s.id,
            job_title=s.job_title,
            company_name=s.company_name,
            extracted_skills=s.extracted_skills,
            skill_gap_summary=s.skill_gap_summary,
            overall_gap_severity=gap.get("overall_gap_severity", "medium"),
        )
    return JobCompareResponse(job_1=item(s1), job_2=item(s2))


class FetchJdRequest(BaseModel):
    url: str


@router.post("/fetch-jd")
def fetch_jd_from_url(
    body: FetchJdRequest,
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Fetch job description text from a URL (HTML). Returns job_description and optional job_title."""
    url = (body.url or "").strip()
    if not url or not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Valid URL required")
    try:
        import httpx
        from bs4 import BeautifulSoup
        with httpx.Client(follow_redirects=True, timeout=15.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            job_description = "\n".join(lines[:200])
            job_title = None
            if soup.title and soup.title.string:
                job_title = soup.title.string.strip()[:200]
            return {"job_description": job_description[:15000], "job_title": job_title}
    except Exception as e:
        logger.warning("Fetch JD failed: %s", e)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Could not fetch or parse URL")


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
        company_name=body.company_name,
        job_title=body.job_title,
        job_description_raw=body.job_description,
        status="draft",
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)

    baseline = get_user_baseline(db, current_user.id)
    initial: ResumeWorkflowState = {
        "job_title": body.job_title,
        "job_description_raw": body.job_description,
        "company_name": body.company_name,
        "user_id": current_user.id,
        "job_submission_id": submission.id,
        "retry_count": 0,
        "user_baseline": baseline,
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
            progress_pct=0,
            ready=False,
        )
    from backend.models.schemas.job import QuestionnaireItem
    questions = [QuestionnaireItem(**q) for q in submission.questionnaire]
    current_stage = getattr(submission, "current_stage", 1) or 1
    total_stages = _total_stages_from_question_count(submission.questionnaire or [])
    progress_pct = min(100, round(100 * current_stage / total_stages)) if total_stages else 0
    return QuestionnaireResponse(
        questions=questions,
        current_stage=current_stage,
        total_stages=total_stages,
        progress_pct=progress_pct,
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
    from backend.models.schemas.job import VALID_ANSWER_VALUES
    allowed = VALID_ANSWER_VALUES
    # Normalize: missing or blank => "no" so we don't fail on client omissions
    stage_answers = {}
    for q in stage_questions:
        qid = q.get("id")
        val = (body.answers or {}).get(qid)
        if val is None or (isinstance(val, str) and val.strip() == ""):
            val = "no"
        normalized = str(val).strip().lower().replace(" ", "_") or "no"
        if normalized == "a_bit":
            pass
        elif normalized not in allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Answer for '{qid}' must be 'yes', 'no', or 'a_bit'.",
            )
        stage_answers[qid] = normalized
    # Merge answers (use normalized stage answers so every stage question is present)
    existing = submission.user_answers or {}
    merged = {**existing, **stage_answers}
    submission.user_answers = merged

    # Next stage: generate only from concepts user answered YES (or a_bit) to in this stage
    yes_questions = [q for q in stage_questions if (merged.get(q.get("id")) or "").strip().lower() in ("yes", "a_bit")]
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
        progress_pct = min(100, round(100 * next_stage / new_total)) if new_total else 0
        return StageAnswersResponse(
            next_stage_questions=next_items,
            current_stage=next_stage,
            total_stages=new_total,
            progress_pct=progress_pct,
            done=False,
            message=f"Stage {next_stage} of {new_total} ready.",
        )
    else:
        # No more questions: user can submit to generate resume
        db.commit()
        total_stages = _total_stages_from_question_count(submission.questionnaire)
        progress_pct = 100  # Section complete
        return StageAnswersResponse(
            next_stage_questions=[],
            current_stage=current_stage,
            total_stages=total_stages,
            progress_pct=progress_pct,
            done=True,
            message="No more questions. Submit to generate your resume.",
        )


def _sse_message(event: str, data: dict | list) -> str:
    """Format one SSE message: event type + data line."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _baseline_has_content(baseline: dict | None) -> bool:
    """True if user has resume text or meaningful profile for fit report."""
    if not baseline:
        return False
    skills = baseline.get("skills") or []
    exp = (baseline.get("experience_summary") or "").strip()
    resume_json = baseline.get("baseline_resume_json") or {}
    if isinstance(resume_json, str):
        try:
            resume_json = json.loads(resume_json)
        except (TypeError, json.JSONDecodeError):
            resume_json = {"raw_text": resume_json}
    raw = ((resume_json or {}).get("raw_text") or "").strip()
    return bool(skills or exp or raw)


def _run_job_input_stream_to_queue(
    user_id: int,
    job_title: str | None,
    job_description: str | None,
    queue: "Queue[dict[str, Any]]",
) -> None:
    """Run job input pipeline (sync). Puts LLM progress and questions into queue for WebSocket."""
    try:
        queue.put({"type": "progress", "phase": "start", "progress_pct": 0})
        with get_db() as db:
            submission = JobSubmission(
                user_id=user_id,
                job_title=job_title,
                job_description_raw=job_description,
                status="draft",
            )
            db.add(submission)
            db.commit()
            db.refresh(submission)
            queue.put({"type": "started", "job_submission_id": submission.id, "progress_pct": 0})
            queue.put({"type": "progress", "phase": "input", "progress_pct": 5})
            baseline = get_user_baseline(db, user_id)
            initial: ResumeWorkflowState = {
                "job_title": job_title,
                "job_description_raw": job_description,
                "company_name": None,
                "user_id": user_id,
                "job_submission_id": submission.id,
                "retry_count": 0,
                "user_baseline": baseline,
            }
            state = dict(initial)
            state.update(input_normalization_node(state))
            if state.get("error"):
                queue.put({"type": "error", "detail": state["error"]})
                return
            queue.put({"type": "progress", "phase": "normalization", "progress_pct": 10})
            if state.get("needs_jd_expansion"):
                state.update(jd_expansion_node(state))
                queue.put({"type": "progress", "phase": "jd_expansion", "progress_pct": 15})
            state.update(skill_extraction_node(state))
            extracted = state.get("extracted_skills")
            if not extracted:
                queue.put({"type": "error", "detail": state.get("extraction_validation_error") or "Skill extraction failed"})
                return
            queue.put({"type": "progress", "phase": "skill_extraction", "progress_pct": 25})

            description_text = state.get("expanded_description") or state.get("normalized_description") or ""
            all_questions: list[dict] = []

            def on_progress(phase: str, progress_pct: int) -> None:
                queue.put({"type": "progress", "phase": phase, "progress_pct": progress_pct})

            for q in stream_questionnaire_stage_1(
                extracted,
                description_text=description_text,
                progress_callback=on_progress,
            ):
                all_questions.append(q)
                queue.put({"type": "question", "data": q})

            if all_questions:
                submission.normalized_input = state.get("expanded_description") or state.get("normalized_description")
                submission.extracted_skills = extracted
                submission.questionnaire = all_questions
                submission.status = "questionnaire_ready"
                submission.current_stage = 1
                db.commit()
            queue.put({"type": "done", "job_submission_id": submission.id})
    except Exception as e:
        logger.exception("Job input stream to queue failed")
        queue.put({"type": "error", "detail": str(e)})


def _run_stage_answers_to_queue(
    user_id: int,
    job_submission_id: int,
    stage: int,
    answers: dict[str, str],
    queue: "Queue[dict[str, Any]]",
) -> None:
    """Run stage-answers pipeline (sync). Streams LLM progress and next questions to queue."""
    try:
        queue.put({"type": "progress", "phase": "questionnaire", "progress_pct": 0})
        queue.put({"type": "progress", "phase": "questionnaire", "progress_pct": 5})
        with get_db() as db:
            submission = db.query(JobSubmission).filter(
                JobSubmission.id == job_submission_id,
                JobSubmission.user_id == user_id,
            ).first()
            if not submission:
                queue.put({"type": "error", "detail": "Job submission not found"})
                return
            if not submission.questionnaire or not submission.extracted_skills:
                queue.put({"type": "error", "detail": "Questionnaire not ready"})
                return
            current_stage = getattr(submission, "current_stage", 1) or 1
            total_stages = _total_stages_from_question_count(submission.questionnaire)
            if stage != current_stage:
                queue.put({"type": "error", "detail": f"Expected stage {current_stage}, got {stage}"})
                return
            has_stages = any(
                isinstance(q, dict) and q.get("stage") is not None
                for q in (submission.questionnaire or [])
            )
            if has_stages:
                stage_questions = [
                    q for q in submission.questionnaire
                    if isinstance(q, dict) and q.get("stage") == stage
                ]
            else:
                stage_questions = list(submission.questionnaire) if stage == 1 else []
            if not stage_questions:
                queue.put({"type": "error", "detail": f"No questions for stage {stage}"})
                return
            from backend.models.schemas.job import VALID_ANSWER_VALUES
            allowed = VALID_ANSWER_VALUES
            stage_answers = {}
            for q in stage_questions:
                qid = q.get("id")
                val = (answers or {}).get(qid)
                if val is None or (isinstance(val, str) and val.strip() == ""):
                    val = "no"
                normalized = str(val).strip().lower().replace(" ", "_") if val else "no"
                if normalized not in allowed:
                    queue.put({"type": "error", "detail": f"Answer for '{qid}' must be 'yes', 'no', or 'a_bit'"})
                    return
                stage_answers[qid] = normalized
            existing = submission.user_answers or {}
            merged = {**existing, **stage_answers}
            submission.user_answers = merged
            queue.put({"type": "progress", "phase": "questionnaire", "progress_pct": 10})

            next_stage = stage + 1
            yes_questions = [
                q for q in stage_questions
                if (merged.get(q.get("id")) or "").strip().lower() in ("yes", "a_bit")
            ]

            def on_progress(phase: str, progress_pct: int) -> None:
                queue.put({"type": "progress", "phase": phase, "progress_pct": progress_pct})

            next_questions = list(
                stream_questionnaire_stage_next(
                    submission.extracted_skills,
                    next_stage,
                    submission.questionnaire,
                    yes_questions,
                    progress_callback=on_progress,
                )
            )
            if next_questions:
                submission.questionnaire = submission.questionnaire + next_questions
                submission.current_stage = next_stage
                new_total = _total_stages_from_question_count(submission.questionnaire)
                queue.put({
                    "type": "done",
                    "next_stage_questions": next_questions,
                    "current_stage": next_stage,
                    "total_stages": new_total,
                    "done": False,
                    "message": f"Stage {next_stage} of {new_total} ready.",
                })
            else:
                queue.put({
                    "type": "done",
                    "next_stage_questions": [],
                    "current_stage": current_stage,
                    "total_stages": total_stages,
                    "done": True,
                    "message": "No more questions. Submit to generate your resume.",
                })
            db.commit()
    except Exception as e:
        logger.exception("Stage answers stream to queue failed")
        queue.put({"type": "error", "detail": str(e)})


@router.websocket("/input/stream-ws")
async def job_input_stream_ws(websocket: WebSocket):
    """WebSocket: submit job title/description, receive LLM generation progress (progress bar) and questions.
    First message must be JSON: { \"token\": \"<jwt>\", \"job_title\": \"...\", \"job_description\": \"...\" }."""
    await websocket.accept()
    try:
        raw = await websocket.receive_text()
        data = json.loads(raw)
        token = data.get("token") or data.get("access_token")
        job_title = data.get("job_title")
        job_description = data.get("job_description")
        if not token:
            await websocket.send_json({"type": "error", "detail": "Missing token"})
            await websocket.close(code=4001)
            return
        if not job_title and not job_description:
            await websocket.send_json({"type": "error", "detail": "Provide job_title or job_description"})
            await websocket.close(code=4000)
            return

        from backend.utils.auth import get_user_from_token
        with get_db() as db:
            user = get_user_from_token(token, db)
        user_id = user.id

        queue: Queue[dict[str, Any]] = Queue()
        loop = asyncio.get_event_loop()
        thread = threading.Thread(
            target=_run_job_input_stream_to_queue,
            args=(user_id, job_title, job_description, queue),
        )
        thread.start()

        while True:
            try:
                item = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: queue.get(timeout=0.3)),
                    timeout=5.0,
                )
                await websocket.send_json(item)
                if item.get("type") in ("done", "error"):
                    break
            except Empty:
                if not thread.is_alive():
                    break
                await asyncio.sleep(0.05)
            except asyncio.TimeoutError:
                if not thread.is_alive():
                    break
                await asyncio.sleep(0.05)
            except WebSocketDisconnect:
                break
        thread.join(timeout=2.0)
    except json.JSONDecodeError as e:
        try:
            await websocket.send_json({"type": "error", "detail": "Invalid JSON"})
        except Exception:
            pass
        await websocket.close(code=4000)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception("WebSocket job input failed")
        try:
            await websocket.send_json({"type": "error", "detail": str(e)})
        except Exception:
            pass
        await websocket.close(code=1011)


@router.post("/{job_submission_id}/questionnaire/stage-answers/stream")
def stage_answers_stream(
    job_submission_id: int,
    body: StageAnswersRequest,
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Stream stage answers: SSE progress (0–100) then done. Same as WebSocket but over HTTP (reliable progress)."""
    user_id = current_user.id
    stage = body.stage
    answers = body.answers or {}

    def generate():
        queue: Queue[dict[str, Any]] = Queue()
        thread = threading.Thread(
            target=_run_stage_answers_to_queue,
            args=(user_id, job_submission_id, stage, answers, queue),
        )
        thread.start()
        try:
            while True:
                try:
                    item = queue.get(timeout=120)
                except Empty:
                    if not thread.is_alive():
                        yield _sse_message("error", {"detail": "Generation timed out or failed"})
                        break
                    continue
                event = item.get("type", "message")
                payload = {k: v for k, v in item.items() if k != "type"}
                yield _sse_message(event, payload)
                if event in ("done", "error"):
                    break
        finally:
            thread.join(timeout=5.0)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.websocket("/{job_submission_id}/questionnaire/stage-answers-ws")
async def stage_answers_stream_ws(websocket: WebSocket, job_submission_id: int):
    """WebSocket: submit stage answers, receive LLM generation progress (progress bar) and next questions.
    First message: { \"token\": \"<jwt>\", \"stage\": 1, \"answers\": { \"q1\": \"yes\", ... } }."""
    await websocket.accept()
    try:
        raw = await websocket.receive_text()
        data = json.loads(raw)
        token = data.get("token") or data.get("access_token")
        stage = data.get("stage")
        answers = data.get("answers") or {}
        if not token:
            await websocket.send_json({"type": "error", "detail": "Missing token"})
            await websocket.close(code=4001)
            return
        if stage is None:
            await websocket.send_json({"type": "error", "detail": "Missing stage"})
            await websocket.close(code=4000)
            return

        from backend.utils.auth import get_user_from_token
        with get_db() as db:
            user = get_user_from_token(token, db)
        user_id = user.id

        queue = Queue()
        loop = asyncio.get_event_loop()
        thread = threading.Thread(
            target=_run_stage_answers_to_queue,
            args=(user_id, job_submission_id, stage, answers, queue),
        )
        thread.start()

        while True:
            try:
                item = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: queue.get(timeout=0.3)),
                    timeout=5.0,
                )
                await websocket.send_json(item)
                if item.get("type") in ("done", "error"):
                    break
            except Empty:
                if not thread.is_alive():
                    break
                await asyncio.sleep(0.05)
            except asyncio.TimeoutError:
                if not thread.is_alive():
                    break
                await asyncio.sleep(0.05)
            except WebSocketDisconnect:
                break
        thread.join(timeout=2.0)
    except json.JSONDecodeError:
        try:
            await websocket.send_json({"type": "error", "detail": "Invalid JSON"})
        except Exception:
            pass
        await websocket.close(code=4000)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception("WebSocket stage answers failed")
        try:
            await websocket.send_json({"type": "error", "detail": str(e)})
        except Exception:
            pass
        await websocket.close(code=1011)


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
            yield _sse_message("progress", {"progress_pct": 0, "phase": "start"})
            baseline = get_user_baseline(db, user_id)
            initial: ResumeWorkflowState = {
                "job_title": job_title,
                "job_description_raw": job_description,
                "company_name": None,
                "user_id": user_id,
                "job_submission_id": submission.id,
                "retry_count": 0,
                "user_baseline": baseline,
            }
            state = dict(initial)
            state.update(input_normalization_node(state))
            if state.get("error"):
                yield _sse_message("error", {"detail": state["error"]})
                return
            yield _sse_message("progress", {"progress_pct": 10, "phase": "normalization"})
            if state.get("needs_jd_expansion"):
                state.update(jd_expansion_node(state))
                yield _sse_message("progress", {"progress_pct": 15, "phase": "jd_expansion"})
            state.update(skill_extraction_node(state))
            extracted = state.get("extracted_skills")
            if not extracted:
                yield _sse_message("error", {"detail": state.get("extraction_validation_error") or "Skill extraction failed"})
                return
            yield _sse_message("progress", {"progress_pct": 25, "phase": "skill_extraction"})

            description_text = state.get("expanded_description") or state.get("normalized_description") or ""
            all_questions = []
            try:
                for i, q in enumerate(stream_questionnaire_stage_1(extracted, description_text=description_text)):
                    all_questions.append(q)
                    pct = min(95, 30 + (i + 1) * 6)
                    yield _sse_message("progress", {"progress_pct": pct, "phase": "questionnaire"})
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

            yield _sse_message("progress", {"progress_pct": 100, "phase": "done"})
            yield _sse_message("done", {"job_submission_id": submission.id})
        except Exception as e:
            logger.exception("Job input stream failed")
            yield _sse_message("error", {"detail": str(e)})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/input/fit-report/stream")
def job_input_fit_report_stream(
    body: JobInputRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """Submit job title/JD with resume on file. Streams fit report generation (SSE, no questionnaire)."""
    if not body.job_title and not body.job_description:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provide job_title or job_description")

    baseline = get_user_baseline(db, current_user.id)
    if not _baseline_has_content(baseline):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Upload your resume or add profile details before running a fit report.",
        )

    user_id = current_user.id
    job_title = body.job_title
    job_description = body.job_description
    company_name = body.company_name

    def generate():
        try:
            submission = JobSubmission(
                user_id=user_id,
                job_title=job_title,
                company_name=company_name,
                job_description_raw=job_description,
                status="draft",
                workflow_mode="fit_report",
            )
            db.add(submission)
            db.commit()
            db.refresh(submission)

            yield _sse_message("started", {"job_submission_id": submission.id})
            yield _sse_message("progress", {"progress_pct": 0, "phase": "start"})

            initial: ResumeWorkflowState = {
                "job_title": job_title,
                "job_description_raw": job_description,
                "company_name": company_name,
                "user_id": user_id,
                "job_submission_id": submission.id,
                "retry_count": 0,
                "user_baseline": baseline,
            }
            state = dict(initial)
            state.update(input_normalization_node(state))
            if state.get("error"):
                yield _sse_message("error", {"detail": state["error"]})
                return
            yield _sse_message("progress", {"progress_pct": 15, "phase": "normalization"})
            if state.get("needs_jd_expansion"):
                state.update(jd_expansion_node(state))
                yield _sse_message("progress", {"progress_pct": 25, "phase": "jd_expansion"})
            state.update(skill_extraction_node(state))
            extracted = state.get("extracted_skills")
            if not extracted:
                yield _sse_message("error", {"detail": state.get("extraction_validation_error") or "Skill extraction failed"})
                return
            yield _sse_message("progress", {"progress_pct": 50, "phase": "skill_extraction"})

            yield _sse_message("progress", {"progress_pct": 60, "phase": "fit_report"})
            state.update(generate_fit_report(state))
            fit_report = state.get("fit_report")
            if not fit_report:
                yield _sse_message("error", {"detail": state.get("error") or "Fit report generation failed"})
                return

            submission.normalized_input = state.get("expanded_description") or state.get("normalized_description")
            submission.extracted_skills = extracted
            submission.fit_report = fit_report
            submission.status = "fit_report_ready"
            db.commit()

            yield _sse_message("progress", {"progress_pct": 100, "phase": "done"})
            yield _sse_message("done", {"job_submission_id": submission.id})
        except Exception as e:
            logger.exception("Fit report stream failed")
            yield _sse_message("error", {"detail": str(e)})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{job_id}/fit-report", response_model=FitReportResponse)
def get_fit_report(
    job_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """Return stored fit report for a job submission."""
    submission = db.query(JobSubmission).filter(
        JobSubmission.id == job_id,
        JobSubmission.user_id == current_user.id,
    ).first()
    if not submission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job submission not found")
    if not submission.fit_report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fit report not available for this job")
    return FitReportResponse(
        job_submission_id=submission.id,
        job_title=submission.job_title,
        company_name=submission.company_name,
        status=submission.status or "fit_report_ready",
        fit_report=FitReportData(**submission.fit_report),
    )
