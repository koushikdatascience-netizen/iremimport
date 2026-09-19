
import asyncio
import json

from app.services.cache_service import CacheService


class FakeRedis:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def delete(self, key):
        self.values.pop(key, None)
        return 1

    async def eval(self, _script, _numkeys, key, owner):
        if self.values.get(key) == owner:
            self.values.pop(key, None)
            return 1
        return 0


def test_distributed_cache_lock_collapses_cross_instance_miss():
    redis = FakeRedis()
    first = CacheService()
    second = CacheService()

    async def redis_client():
        return redis

    first._redis_client = redis_client
    second._redis_client = redis_client

    calls = {"count": 0}

    async def loader():
        calls["count"] += 1
        await asyncio.sleep(0.05)
        return [{"itemCode": "M001"}]

    async def run():
        return await asyncio.gather(
            first.get_or_load("catalogue:SHOP:2:AI", 60, loader),
            second.get_or_load("catalogue:SHOP:2:AI", 60, loader),
        )

    results = asyncio.run(run())

    assert calls["count"] == 1
    assert results[0] == [{"itemCode": "M001"}]
    assert results[1] == [{"itemCode": "M001"}]
    encoded = redis.values[first._key("catalogue:SHOP:2:AI")]
    assert json.loads(encoded) == [{"itemCode": "M001"}]
