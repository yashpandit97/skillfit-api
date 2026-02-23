"""
Auth: register, login, JWT.
"""
from typing import Annotated

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.db import get_db_dependency
from backend.models.user import User
from backend.models.schemas.auth import UserCreate, UserResponse, Token
from backend.utils.auth import create_access_token, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


def _hash_password(password: str) -> str:
    pwd_bytes = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pwd_bytes, bcrypt.gensalt()).decode("utf-8")


def _verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8")[:72], hashed.encode("utf-8"))
    except Exception:
        return False


@router.post("/register", response_model=UserResponse)
def register(
    body: UserCreate,
    db: Annotated[Session, Depends(get_db_dependency)],
):
    existing = db.query(User).filter(User.email == body.email).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
    user = User(
        email=body.email,
        hashed_password=_hash_password(body.password),
        full_name=body.full_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserResponse(id=user.id, email=user.email, full_name=user.full_name, is_active=user.is_active)


@router.post("/login", response_model=Token)
def login(
    body: UserCreate,
    db: Annotated[Session, Depends(get_db_dependency)],
):
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not _verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    token = create_access_token(user.id, user.email)
    return Token(access_token=token, token_type="bearer")
