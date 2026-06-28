"""
Auth: Firebase sign-in exchange for app JWT.
"""
import secrets
from typing import Annotated

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.db import get_db_dependency
from backend.models.user import User
from backend.models.schemas.auth import UserResponse, Token, FirebaseLoginRequest
from backend.utils.auth import create_access_token, get_current_user
from backend.services.firebase_auth import verify_firebase_id_token

router = APIRouter(prefix="/auth", tags=["auth"])


def _hash_password(password: str) -> str:
    pwd_bytes = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pwd_bytes, bcrypt.gensalt()).decode("utf-8")


def _firebase_placeholder_password_hash() -> str:
    return _hash_password(secrets.token_urlsafe(32))


def _get_or_create_user_from_firebase(db: Session, claims: dict) -> User:
    uid = claims.get("uid") or claims.get("user_id") or claims.get("sub")
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Firebase token")
    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Firebase account must have an email address",
        )
    name = claims.get("name") or claims.get("display_name")

    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        user = db.query(User).filter(User.email == email).first()
        if user:
            user.firebase_uid = uid
            if name and not user.full_name:
                user.full_name = name
        else:
            user = User(
                email=email,
                firebase_uid=uid,
                full_name=name,
                hashed_password=_firebase_placeholder_password_hash(),
            )
            db.add(user)
    elif name and not user.full_name:
        user.full_name = name

    db.commit()
    db.refresh(user)
    return user


@router.post("/firebase", response_model=Token)
def firebase_login(
    body: FirebaseLoginRequest,
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """Exchange a Firebase ID token for an app JWT."""
    try:
        claims = verify_firebase_id_token(body.id_token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Firebase token")
    user = _get_or_create_user_from_firebase(db, claims)
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive")
    token = create_access_token(user.id, user.email)
    return Token(access_token=token, token_type="bearer")


@router.get("/me", response_model=UserResponse)
def me(current_user: Annotated[User, Depends(get_current_user)]):
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        is_active=current_user.is_active,
    )
