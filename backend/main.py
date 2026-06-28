"""
FastAPI application: AI Resume Intelligence. Centralized error handling, logging, rate limit.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from backend.config import get_settings
from backend.utils.logging_config import setup_logging
from backend.utils.errors import AppError, app_error_handler, validation_exception_handler, generic_exception_handler
from backend.routers import auth, job, questionnaire, skill_gap, resume, profile, interview_prep

# Rate limit: in-memory for simplicity (use Redis in production)
from collections import defaultdict
from time import time
_rate: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_REQUESTS = 60
RATE_LIMIT_WINDOW = 60


def _rate_limit_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(debug=get_settings().debug)
    from backend.db.session import engine
    from backend.db.session import Base
    from backend.db.session import ensure_current_stage_column, ensure_company_name_column, ensure_resume_version_pdf_column, ensure_fit_report_columns, ensure_firebase_uid_column, ensure_firebase_uid_column
    # Create tables on startup (SQLite local dev, PostgreSQL in Docker); safe no-op if they exist
    import backend.models  # noqa: F401 — register all models
    Base.metadata.create_all(bind=engine)
    ensure_current_stage_column(engine)
    ensure_company_name_column(engine)
    ensure_resume_version_pdf_column(engine)
    ensure_fit_report_columns(engine)
    ensure_firebase_uid_column(engine)
    yield
    # shutdown: close db pools, etc.


app = FastAPI(
    title=get_settings().app_name,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, generic_exception_handler)

# Rate limiting middleware (simple)
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    key = _rate_limit_key(request)
    now = time()
    _rate[key] = [t for t in _rate[key] if now - t < RATE_LIMIT_WINDOW]
    if len(_rate[key]) >= RATE_LIMIT_REQUESTS:
        return JSONResponse(status_code=429, content={"detail": "Too many requests"})
    _rate[key].append(now)
    return await call_next(request)

app.include_router(auth.router, prefix="/api")
app.include_router(job.router, prefix="/api")
app.include_router(questionnaire.router, prefix="/api")
app.include_router(skill_gap.router, prefix="/api")
app.include_router(resume.router, prefix="/api")
app.include_router(profile.router, prefix="/api")
app.include_router(interview_prep.router, prefix="/api")


@app.get("/health")
def health():
    return {"status": "ok"}
