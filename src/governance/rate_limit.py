from __future__ import annotations

import time
from dataclasses import dataclass

from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import rate_limit_decisions

logger = get_logger(__name__)


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_in: int

    @property
    def headers(self) -> dict[str, str]:
        """Standard rate-limit headers.

        Returned on *every* response, not only rejections: a client that can
        see it has 3 requests left can slow down, whereas one that only learns
        on a 429 has already been refused.
        """
        h = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(max(0, self.remaining)),
            "X-RateLimit-Reset": str(self.reset_in),
        }
        if not self.allowed:
            h["Retry-After"] = str(self.reset_in)
        return h


class RateLimiter:
    """Fixed-window request limiter backed by Redis.

    Fixed window rather than a sliding log: one `INCR` plus one `EXPIRE` per
    request, versus storing and trimming a timestamp set. The known trade is
    burstiness at the boundary — a client can send `limit` requests at the end
    of one window and `limit` again at the start of the next. For protecting
    spend and blunting brute force that is an acceptable 2x, and it costs a
    fraction of the memory.

    **Fails open.** If Redis is unavailable the request is allowed and the
    failure is counted. A rate limiter that takes the product down when its
    own cache blips has caused a worse outage than the abuse it prevents —
    the `rate_limit_decisions{outcome="error"}` counter is what makes that
    state visible rather than silent.
    """

    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    async def check(
        self, key: str, limit: int, window_seconds: int = 60, *, bucket: str = "default"
    ) -> RateLimitResult:
        if limit <= 0:
            return RateLimitResult(True, limit, limit, 0)

        # The window is embedded in the key, so expiry and window rollover are
        # the same event and a stale counter cannot outlive its window.
        window_id = int(time.time()) // window_seconds
        redis_key = f"ratelimit:{bucket}:{key}:{window_id}"
        reset_in = window_seconds - (int(time.time()) % window_seconds)

        try:
            count = await self._redis.incr(redis_key)
            if count == 1:
                # Only the first request in a window sets the TTL; doing it on
                # every request would slide the expiry forward and the window
                # would never end for a continuously-active client.
                await self._redis.expire(redis_key, window_seconds)
        except Exception as exc:
            rate_limit_decisions.labels(bucket=bucket, outcome="error").inc()
            logger.warning("rate_limit_backend_unavailable", error=str(exc))
            return RateLimitResult(True, limit, limit, reset_in)

        allowed = count <= limit
        rate_limit_decisions.labels(
            bucket=bucket, outcome="allowed" if allowed else "limited"
        ).inc()
        if not allowed:
            logger.warning(
                "rate_limit_exceeded", bucket=bucket, key=key[:40], count=count, limit=limit
            )
        return RateLimitResult(allowed, limit, limit - count, reset_in)


_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        from src.infrastructure.database.redis.connection import get_redis_client

        _limiter = RateLimiter(get_redis_client())
    return _limiter


def reset_rate_limiter() -> None:
    """Test helper."""
    global _limiter
    _limiter = None
