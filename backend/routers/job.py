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
from backend.db import get_db_dependency, get_db
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
            allowed = {"yes", "no"}
            stage_answers = {}
            for q in stage_questions:
                qid = q.get("id")
                val = (answers or {}).get(qid)
                if val is None or (isinstance(val, str) and val.strip() == ""):
                    val = "no"
                normalized = str(val).strip().lower() if val else "no"
                if normalized not in allowed:
                    queue.put({"type": "error", "detail": f"Answer for '{qid}' must be 'yes' or 'no'"})
                    return
                stage_answers[qid] = normalized
            existing = submission.user_answers or {}
            merged = {**existing, **stage_answers}
            submission.user_answers = merged
            queue.put({"type": "progress", "phase": "questionnaire", "progress_pct": 10})

            next_stage = stage + 1
            yes_questions = [
                q for q in stage_questions
                if (merged.get(q.get("id")) or "").strip().lower() == "yes"
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
