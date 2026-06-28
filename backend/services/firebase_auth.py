"""Verify Firebase ID tokens issued by the web client."""
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from backend.config import get_settings


def verify_firebase_id_token(token: str) -> dict:
    """Return decoded Firebase token claims (uid, email, name, …)."""
    settings = get_settings()
    if not settings.firebase_project_id:
        raise ValueError("FIREBASE_PROJECT_ID is not configured")
    request = google_requests.Request()
    return id_token.verify_firebase_token(
        token,
        request,
        audience=settings.firebase_project_id,
    )
