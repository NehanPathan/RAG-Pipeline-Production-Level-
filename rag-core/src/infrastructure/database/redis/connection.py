import json
from typing import Any

import redis.asyncio as aioredis

from src.config import get_settings

_redis_client: aioredis.Redis | None = None


def get_redis_client() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_client


class RedisCache:
    def __init__(self, client: aioredis.Redis) -> None:
        self._client = client

    async def get(self, key: str) -> Any | None:
        value = await self._client.get(key)
        if value is None:
            return None
        return json.loads(value)

    async def set(self, key: str, value: Any, ttl: int) -> None:
        await self._client.setex(key, ttl, json.dumps(value))

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def delete_pattern(self, pattern: str) -> int:
        keys = await self._client.keys(pattern)
        if keys:
            return await self._client.delete(*keys)
        return 0

    async def exists(self, key: str) -> bool:
        return bool(await self._client.exists(key))

    async def ping(self) -> bool:
        try:
            return await self._client.ping()
        except Exception:
            return False
