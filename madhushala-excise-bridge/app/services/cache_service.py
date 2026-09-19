from __future__ import annotations

import asyncio
import json
import logging
import secrets
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
        self._redis_retry_after = 0.0

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
        if not settings.REDIS_URL:
            return None
        if self._redis is not None:
            return self._redis
        if time.monotonic() < self._redis_retry_after:
            return None
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
            self._redis_retry_after = 0.0
            logger.info("cache_backend=redis status=ready")
            return self._redis
        except Exception as exc:
            # Redis is an acceleration layer, not the system of record. Degrade
            # to the local cache temporarily and retry Redis after a short
            # cooldown instead of permanently disabling it for the process.
            self._redis_retry_after = time.monotonic() + 5.0
            logger.warning("cache_backend=memory redis_unavailable=%s retryInSeconds=5", exc)
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

    async def _acquire_redis_lock(self, redis_client: Any, key: str) -> tuple[str, str] | None:
        owner = secrets.token_hex(16)
        lock_key = self._key(f"lock:{key}")
        acquired = await redis_client.set(
            lock_key,
            owner,
            nx=True,
            ex=max(1, int(settings.CACHE_LOCK_TTL_SECONDS)),
        )
        if acquired:
            return lock_key, owner
        return None

    @staticmethod
    async def _release_redis_lock(redis_client: Any, lock_key: str, owner: str) -> None:
        script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        end
        return 0
        """
        try:
            await redis_client.eval(script, 1, lock_key, owner)
        except Exception as exc:
            logger.warning("cache_lock_release_failed key=%s error=%s", lock_key, exc)

    async def get_or_load(
        self,
        key: str,
        ttl_seconds: int,
        loader: Callable[[], Awaitable[Any]],
    ) -> Any:
        cached = await self.get_json(key)
        if cached is not None:
            return cached

        # Local lock collapses concurrent misses inside one FastAPI worker.
        lock = await self._get_lock(key)
        async with lock:
            cached = await self.get_json(key)
            if cached is not None:
                return cached

            redis_client = await self._redis_client()
            if redis_client is None:
                value = await loader()
                await self.set_json(key, value, ttl_seconds)
                return value

            # Redis lock collapses the same cache miss across every FastAPI
            # replica. Only the lock owner calls the Madhushala upstream API;
            # other replicas wait for the newly populated cache value.
            deadline = time.monotonic() + max(0.1, float(settings.CACHE_LOCK_WAIT_SECONDS))
            while True:
                acquired: tuple[str, str] | None = None
                try:
                    acquired = await self._acquire_redis_lock(redis_client, key)
                except Exception as exc:
                    logger.warning("cache_lock_acquire_failed key=%s error=%s", key, exc)

                if acquired is not None:
                    lock_key, owner = acquired
                    try:
                        # Double-check after acquiring: another owner may have
                        # populated the value just before this lock was won.
                        cached = await self.get_json(key)
                        if cached is not None:
                            return cached
                        value = await loader()
                        await self.set_json(key, value, ttl_seconds)
                        return value
                    finally:
                        await self._release_redis_lock(redis_client, lock_key, owner)

                cached = await self.get_json(key)
                if cached is not None:
                    return cached

                if time.monotonic() >= deadline:
                    # Graceful degradation: because the process-local lock is
                    # still held, this creates at most one fallback upstream
                    # load per replica rather than one per waiting request.
                    logger.warning(
                        "cache_lock_wait_timeout key=%s waitSeconds=%s fallback=local_loader",
                        key,
                        settings.CACHE_LOCK_WAIT_SECONDS,
                    )
                    value = await loader()
                    await self.set_json(key, value, ttl_seconds)
                    return value

                await asyncio.sleep(max(0.01, float(settings.CACHE_LOCK_POLL_SECONDS)))

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
        self._redis = None
        self._redis_retry_after = 0.0


cache_service = CacheService()
