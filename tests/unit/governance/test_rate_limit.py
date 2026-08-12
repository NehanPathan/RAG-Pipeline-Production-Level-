from __future__ import annotations

import pytest

from src.governance.rate_limit import RateLimiter


class FakeRedis:
    """Minimal INCR/EXPIRE stand-in. `fail=True` simulates Redis being down."""

    def __init__(self, fail: bool = False) -> None:
        self.counters: dict[str, int] = {}
        self.expiries: dict[str, int] = {}
        self.fail = fail

    async def incr(self, key: str) -> int:
        if self.fail:
            raise ConnectionError("redis unavailable")
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, key: str, seconds: int) -> None:
        if self.fail:
            raise ConnectionError("redis unavailable")
        self.expiries[key] = seconds


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


class TestLimiting:
    @pytest.mark.asyncio
    async def test_allows_up_to_the_limit(self, redis):
        limiter = RateLimiter(redis)
        for _ in range(3):
            assert (await limiter.check("user-1", limit=3)).allowed is True

    @pytest.mark.asyncio
    async def test_refuses_beyond_the_limit(self, redis):
        limiter = RateLimiter(redis)
        for _ in range(3):
            await limiter.check("user-1", limit=3)
        result = await limiter.check("user-1", limit=3)
        assert result.allowed is False
        assert result.remaining <= 0

    @pytest.mark.asyncio
    async def test_identities_are_metered_separately(self, redis):
        """Keyed on identity, not IP: users behind one office NAT must not
        throttle each other."""
        limiter = RateLimiter(redis)
        for _ in range(3):
            await limiter.check("user-1", limit=3)

        assert (await limiter.check("user-2", limit=3)).allowed is True

    @pytest.mark.asyncio
    async def test_buckets_are_independent(self, redis):
        """Exhausting the upload budget must not block chatting."""
        limiter = RateLimiter(redis)
        for _ in range(3):
            await limiter.check("user-1", limit=3, bucket="upload")

        assert (await limiter.check("user-1", limit=3, bucket="chat")).allowed is True

    @pytest.mark.asyncio
    async def test_zero_limit_disables_the_check(self, redis):
        assert (await RateLimiter(redis).check("user-1", limit=0)).allowed is True


class TestExpiry:
    @pytest.mark.asyncio
    async def test_ttl_is_set_once_per_window(self, redis):
        """Re-setting the TTL on every request would slide expiry forward, and
        the window would never end for a continuously-active client."""
        limiter = RateLimiter(redis)
        for _ in range(5):
            await limiter.check("user-1", limit=10, window_seconds=60)

        assert len(redis.expiries) == 1
        assert next(iter(redis.expiries.values())) == 60


class TestFailureBehaviour:
    @pytest.mark.asyncio
    async def test_fails_open_when_redis_is_down(self):
        """A limiter that takes the product down when its own cache blips has
        caused a worse outage than the abuse it prevents."""
        result = await RateLimiter(FakeRedis(fail=True)).check("user-1", limit=1)
        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_failing_open_is_still_observable(self):
        """Silence would hide that nothing is being protected; the error
        outcome is counted so an alert can fire on it."""
        from src.monitoring.prometheus_metrics import rate_limit_decisions

        before = rate_limit_decisions.labels(bucket="test", outcome="error")._value.get()
        await RateLimiter(FakeRedis(fail=True)).check("u", limit=1, bucket="test")
        after = rate_limit_decisions.labels(bucket="test", outcome="error")._value.get()
        assert after == before + 1


class TestHeaders:
    @pytest.mark.asyncio
    async def test_headers_present_on_allowed_requests(self, redis):
        """A client that can see it has 3 left can slow down; one that only
        learns on a 429 has already been refused."""
        result = await RateLimiter(redis).check("user-1", limit=5)
        assert result.headers["X-RateLimit-Limit"] == "5"
        assert result.headers["X-RateLimit-Remaining"] == "4"
        assert "Retry-After" not in result.headers

    @pytest.mark.asyncio
    async def test_retry_after_only_when_refused(self, redis):
        limiter = RateLimiter(redis)
        await limiter.check("user-1", limit=1)
        refused = await limiter.check("user-1", limit=1)
        assert "Retry-After" in refused.headers
        assert int(refused.headers["Retry-After"]) > 0

    @pytest.mark.asyncio
    async def test_remaining_never_reports_negative(self, redis):
        limiter = RateLimiter(redis)
        for _ in range(5):
            await limiter.check("user-1", limit=1)
        result = await limiter.check("user-1", limit=1)
        assert int(result.headers["X-RateLimit-Remaining"]) == 0
