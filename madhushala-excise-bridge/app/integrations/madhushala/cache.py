from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from app.config import settings


logger = logging.getLogger("madhushala-excise-bridge")


@dataclass
class _MemoryEntry:
    value: Any
    expires_at: float


class MadhushalaCache:
    """Async JSON cache with optional Redis and an always-available memory fallback.

    The current deployment works without Redis. When REDIS_URL is configured, the
    exact same application code becomes multi-instance cache capable.
    """

    def __init__(self) -> None:
        self._memory: dict[str, _MemoryEntry] = {}
        self._memory_lock = asyncio.Lock()
        self._redis: Any | None = None
        self._redis_disabled = False

    def _key(self, key: str) -> str:
        prefix = str(settings.MADHUSHALA_CACHE_PREFIX or "madhushala").strip(":")
        return f"{prefix}:{key}"

    async def _redis_client(self) -> Any | None:
        if self._redis_disabled or not str(settings.REDIS_URL or "").strip():
            return None
        if self._redis is not None:
            return self._redis
        try:
            import redis.asyncio as redis  # type: ignore

            self._redis = redis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=1.5,
                socket_timeout=2.0,
                health_check_interval=30,
            )
            await self._redis.ping()
            logger.info("event=cache_backend_ready backend=redis")
            return self._redis
        except Exception as exc:
            self._redis_disabled = True
            self._redis = None
            logger.warning("event=cache_backend_fallback backend=memory reason=%s", exc)
            return None

    async def get(self, key: str) -> Any | None:
        full_key = self._key(key)
        redis_client = await self._redis_client()
        if redis_client is not None:
            try:
                raw = await redis_client.get(full_key)
                if raw is not None:
                    return json.loads(raw)
            except Exception as exc:
                logger.warning("event=cache_redis_get_failed key=%s reason=%s", full_key, exc)

        now = time.monotonic()
        async with self._memory_lock:
            entry = self._memory.get(full_key)
            if not entry:
                return None
            if entry.expires_at <= now:
                self._memory.pop(full_key, None)
                return None
            return entry.value

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        full_key = self._key(key)
        ttl = max(1, int(ttl_seconds))
        redis_client = await self._redis_client()
        if redis_client is not None:
            try:
                await redis_client.set(full_key, json.dumps(value, ensure_ascii=False), ex=ttl)
            except Exception as exc:
                logger.warning("event=cache_redis_set_failed key=%s reason=%s", full_key, exc)

        async with self._memory_lock:
            self._memory[full_key] = _MemoryEntry(value=value, expires_at=time.monotonic() + ttl)

    async def delete(self, key: str) -> None:
        full_key = self._key(key)
        redis_client = await self._redis_client()
        if redis_client is not None:
            try:
                await redis_client.delete(full_key)
            except Exception:
                pass
        async with self._memory_lock:
            self._memory.pop(full_key, None)

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None


madhushala_cache = MadhushalaCache()
