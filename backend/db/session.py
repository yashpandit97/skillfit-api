"""
Database session management. Default: SQLite (no setup). Set DATABASE_URL for PostgreSQL.
"""
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session, DeclarativeBase

from backend.config import get_settings


class Base(DeclarativeBase):
    pass


def get_engine():
    url = get_settings().database_url
    kwargs = {"pool_pre_ping": True, "echo": get_settings().debug}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


engine = get_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@contextmanager
def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db_dependency() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def ensure_current_stage_column(eng):
    """Add current_stage to job_submissions if missing (e.g. after model change without full recreate)."""
    with eng.connect() as conn:
        if eng.url.drivername == "sqlite":
            r = conn.execute(text("SELECT 1 FROM pragma_table_info('job_submissions') WHERE name = 'current_stage'"))
            if r.scalar() is None:
                conn.execute(text("ALTER TABLE job_submissions ADD COLUMN current_stage INTEGER NOT NULL DEFAULT 1"))
                conn.commit()
        else:
            conn.execute(text("ALTER TABLE job_submissions ADD COLUMN IF NOT EXISTS current_stage INTEGER NOT NULL DEFAULT 1"))
            conn.commit()


def ensure_company_name_column(eng):
    """Add company_name to job_submissions if missing."""
    with eng.connect() as conn:
        if eng.url.drivername == "sqlite":
            r = conn.execute(text("SELECT 1 FROM pragma_table_info('job_submissions') WHERE name = 'company_name'"))
            if r.scalar() is None:
                conn.execute(text("ALTER TABLE job_submissions ADD COLUMN company_name VARCHAR(255)"))
                conn.commit()
        else:
            conn.execute(text("ALTER TABLE job_submissions ADD COLUMN IF NOT EXISTS company_name VARCHAR(255)"))
            conn.commit()


def ensure_resume_version_pdf_column(eng):
    """Add file_path_pdf to resume_versions if missing."""
    with eng.connect() as conn:
        if eng.url.drivername == "sqlite":
            r = conn.execute(text("SELECT 1 FROM pragma_table_info('resume_versions') WHERE name = 'file_path_pdf'"))
            if r.scalar() is None:
                conn.execute(text("ALTER TABLE resume_versions ADD COLUMN file_path_pdf VARCHAR(1024)"))
                conn.commit()
        else:
            conn.execute(text("ALTER TABLE resume_versions ADD COLUMN IF NOT EXISTS file_path_pdf VARCHAR(1024)"))
            conn.commit()


def ensure_fit_report_columns(eng):
    """Add fit_report and workflow_mode to job_submissions if missing."""
    with eng.connect() as conn:
        if eng.url.drivername == "sqlite":
            for col, ddl in (
                ("fit_report", "ALTER TABLE job_submissions ADD COLUMN fit_report JSON"),
                ("workflow_mode", "ALTER TABLE job_submissions ADD COLUMN workflow_mode VARCHAR(50)"),
            ):
                r = conn.execute(text(f"SELECT 1 FROM pragma_table_info('job_submissions') WHERE name = '{col}'"))
                if r.scalar() is None:
                    conn.execute(text(ddl))
                    conn.commit()
        else:
            conn.execute(text("ALTER TABLE job_submissions ADD COLUMN IF NOT EXISTS fit_report JSON"))
            conn.execute(text("ALTER TABLE job_submissions ADD COLUMN IF NOT EXISTS workflow_mode VARCHAR(50)"))
            conn.commit()


def ensure_firebase_uid_column(eng):
    """Add firebase_uid to users if missing."""
    with eng.connect() as conn:
        if eng.url.drivername == "sqlite":
            r = conn.execute(text("SELECT 1 FROM pragma_table_info('users') WHERE name = 'firebase_uid'"))
            if r.scalar() is None:
                conn.execute(text("ALTER TABLE users ADD COLUMN firebase_uid VARCHAR(128)"))
                conn.commit()
        else:
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS firebase_uid VARCHAR(128)"))
            conn.commit()
