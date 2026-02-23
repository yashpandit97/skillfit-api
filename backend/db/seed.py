"""
Seed default admin user for local/dev. Do not use in production as-is.
"""
import logging
import bcrypt

from backend.db.session import SessionLocal
from backend.models.user import User

logger = logging.getLogger(__name__)

ADMIN_EMAIL = "admin"
ADMIN_PASSWORD = "admin123"


def _hash_password(password: str) -> str:
    # Bcrypt limit 72 bytes; use first 72 bytes of utf-8
    pwd_bytes = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pwd_bytes, bcrypt.gensalt()).decode("utf-8")


def seed_admin_user() -> None:
    """Create admin/admin123 user if it doesn't exist."""
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == ADMIN_EMAIL).first()
        if existing:
            return
        user = User(
            email=ADMIN_EMAIL,
            hashed_password=_hash_password(ADMIN_PASSWORD),
            full_name="Admin",
        )
        db.add(user)
        db.commit()
        logger.info("Seeded default user: %s", ADMIN_EMAIL)
    except Exception as e:
        logger.warning("Could not seed admin user (e.g. DB not ready): %s", e)
        db.rollback()
    finally:
        db.close()
