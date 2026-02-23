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
