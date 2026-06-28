"""Helpers for study / guidance URLs returned to clients."""

def is_youtube_url(url: str) -> bool:
    lowered = (url or "").lower()
    return "youtube.com" in lowered or "youtu.be" in lowered


def filter_website_urls(urls: list[str] | None) -> list[str]:
    return [u for u in (urls or []) if u and not is_youtube_url(u)]


def sanitize_study_urls(study_urls: dict | None) -> dict:
    """Strip YouTube links; guidance should only include article/documentation URLs."""
    if not isinstance(study_urls, dict):
        study_urls = {}
    return {
        "websites": filter_website_urls(study_urls.get("websites")),
        "youtube": [],
    }
