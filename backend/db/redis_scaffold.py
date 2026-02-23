"""
Redis scaffold for caching. Stub implementation when Redis unavailable.
Production: use redis.asyncio or redis-py for rate limiting / response cache.
"""
import logging
from typing import Any

from backend.config import get_settings

logger = logging.getLogger(__name__)

_redis_client: Any = None


def get_redis():
    """Return Redis client or None if not configured/available."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis
        url = get_settings().redis_url
        _redis_client = redis.from_url(url, decode_responses=True)
        _redis_client.ping()
        return _redis_client
    except Exception as e:
        logger.warning("Redis not available: %s. Caching disabled.", e)
        return None


def cache_get(key: str) -> Any | None:
    """Get cached value. Returns None if Redis unavailable or key missing."""
    r = get_redis()
    if r is None:
        return None
    try:
        return r.get(key)
    except Exception:
        return None


def cache_set(key: str, value: str, ttl_seconds: int = 3600) -> None:
    """Set cache value. No-op if Redis unavailable."""
    r = get_redis()
    if r is None:
        return
    try:
        r.setex(key, ttl_seconds, value)
    except Exception:
        pass
