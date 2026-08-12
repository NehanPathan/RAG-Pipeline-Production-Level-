from __future__ import annotations

from fastapi import Depends, HTTPException, Request, Response
from fastapi import status as http_status

from src.config import get_settings
from src.governance.rate_limit import get_rate_limiter
from src.governance.rbac import Principal, Role, get_principal, require_role
from src.monitoring.logger import get_logger

logger = get_logger(__name__)


def rate_limit(bucket: str, per_minute: int | None = None):
    """Dependency factory enforcing a per-identity request limit.

    Keyed on the **authenticated principal**, not the client IP. IP-keyed
    limits are the wrong unit here: several users behind one office NAT share
    an address and would throttle each other, while one user with a handful of
    addresses evades the limit entirely. Identity is what costs money, so
    identity is what gets metered.

    Reads its limit from settings at call time rather than at import, so
    `RATE_LIMIT_CHAT` can be changed without a code change.
    """

    async def _guard(
        request: Request,
        response: Response,
        principal: Principal = Depends(get_principal),
    ) -> Principal:
        settings = get_settings()
        limit = per_minute if per_minute is not None else getattr(
            settings, f"rate_limit_{bucket}", settings.rate_limit_default
        )

        result = await get_rate_limiter().check(
            key=str(principal.user_id), limit=limit, window_seconds=60, bucket=bucket
        )

        # Headers on every response, not just rejections, so a client can slow
        # down before it is refused.
        for name, value in result.headers.items():
            response.headers[name] = value

        if not result.allowed:
            raise HTTPException(
                status_code=http_status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Rate limit reached ({limit} {bucket} requests per minute). "
                    f"Try again in {result.reset_in}s."
                ),
                headers=result.headers,
            )
        return principal

    return _guard


def rate_limit_role(bucket: str, *allowed: Role, per_minute: int | None = None):
    """Role gate *and* rate limit in one dependency.

    FastAPI resolves each `Depends` separately, so stacking `require_role` and
    `rate_limit` on one parameter is not possible -- and putting them on two
    parameters would resolve the principal twice, doubling the database lookup
    on every request. Composing them here keeps a single resolution.

    Order matters: the role check runs first, so a caller who may not perform
    the action at all is refused with 403 without consuming their rate budget.
    """
    role_guard = require_role(*allowed)
    limit_guard = rate_limit(bucket, per_minute)

    async def _guard(
        request: Request,
        response: Response,
        principal: Principal = Depends(get_principal),
    ) -> Principal:
        await role_guard(principal=principal)
        return await limit_guard(request=request, response=response, principal=principal)

    return _guard
