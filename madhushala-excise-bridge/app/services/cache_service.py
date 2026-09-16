from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.config import settings


logger = logging.getLogger("madhushala-excise-bridge.cache")


@dataclass
class _MemoryEntry:
    expires_at: float
    value: str


class CacheService:
    """Small async cache abstraction with Redis + memory fallback.

    The service deliberately keeps Redis optional. Current single-instance
    deployments work immediately with the in-process cache; setting REDIS_URL
    later gives all FastAPI instances the same cache without changing callers.
    """

    def __init__(self) -> None:
        self._memory: dict[str, _MemoryEntry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()
        self._redis: Any | None = None
        self._redis_disabled = False

    def _key(self, key: str) -> str:
        return f"{settings.CACHE_PREFIX}:{key}"

    async def _get_lock(self, key: str) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    async def _redis_client(self) -> Any | None:
        if not settings.REDIS_URL or self._redis_disabled:
            return None
        if self._redis is not None:
            return self._redis
        try:
            import redis.asyncio as redis  # type: ignore

            client = redis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
                health_check_interval=30,
            )
            await client.ping()
            self._redis = client
            logger.info("cache_backend=redis status=ready")
            return self._redis
        except Exception as exc:
            self._redis_disabled = True
            logger.warning("cache_backend=memory redis_unavailable=%s", exc)
            return None

    async def get_json(self, key: str) -> Any | None:
        full_key = self._key(key)
        redis_client = await self._redis_client()
        if redis_client is not None:
            try:
                raw = await redis_client.get(full_key)
                if raw is not None:
                    return json.loads(raw)
            except Exception as exc:
                logger.warning("cache_get redis_failed key=%s error=%s", key, exc)

        entry = self._memory.get(full_key)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            self._memory.pop(full_key, None)
            return None
        try:
            return json.loads(entry.value)
        except Exception:
            self._memory.pop(full_key, None)
            return None

    async def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        full_key = self._key(key)
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        ttl = max(1, int(ttl_seconds))
        self._memory[full_key] = _MemoryEntry(time.monotonic() + ttl, encoded)

        redis_client = await self._redis_client()
        if redis_client is not None:
            try:
                await redis_client.set(full_key, encoded, ex=ttl)
            except Exception as exc:
                logger.warning("cache_set redis_failed key=%s error=%s", key, exc)

    async def delete(self, key: str) -> None:
        full_key = self._key(key)
        self._memory.pop(full_key, None)
        redis_client = await self._redis_client()
        if redis_client is not None:
            try:
                await redis_client.delete(full_key)
            except Exception as exc:
                logger.warning("cache_delete redis_failed key=%s error=%s", key, exc)

    async def get_or_load(
        self,
        key: str,
        ttl_seconds: int,
        loader: Callable[[], Awaitable[Any]],
    ) -> Any:
        cached = await self.get_json(key)
        if cached is not None:
            return cached

        lock = await self._get_lock(key)
        async with lock:
            cached = await self.get_json(key)
            if cached is not None:
                return cached
            value = await loader()
            await self.set_json(key, value, ttl_seconds)
            return value

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
        self._redis = None


cache_service = CacheService()
