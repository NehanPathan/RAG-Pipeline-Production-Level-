from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum

from fastapi import Depends, HTTPException, Request
from fastapi import status as http_status
from sqlalchemy import select

from src.config import get_settings
from src.governance.policy import Sensitivity, get_policy
from src.infrastructure.database.postgres.connection import get_session_factory
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import access_denied

logger = get_logger(__name__)

# The placeholder identity every un-headered request resolves to. Kept here
# rather than in api/dependencies.py so the auth seam has no dependency on
# the retrieval object graph; api/dependencies.py re-exports it unchanged.
DEFAULT_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class Role(str, Enum):
    """Roles as stored in `users.role`.

    VIEWER is the DB default and deliberately the least privileged — a row
    written without an explicit role must not accidentally gain access.
    """

    VIEWER = "viewer"
    ANALYST = "analyst"
    STEWARD = "steward"
    ADMIN = "admin"


@dataclass(frozen=True)
class Principal:
    """Who is making this request, and what they may see.

    `clearance` is derived from the role via AIPolicy.role_clearance rather
    than stored on the user row, so re-classifying what a role may read is a
    policy change (one config value, one audit entry) instead of a data
    migration across every user.
    """

    user_id: uuid.UUID
    role: str
    clearance: Sensitivity
    is_active: bool = True
    email: str = ""
    #: How this identity was established. "firebase" means a verified token;
    #: "none" means the development header, which proves nothing. Recorded on
    #: audit entries so a reviewer can tell trustworthy attribution from the
    #: development placeholder.
    auth_provider: str = "none"

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN.value

    @property
    def is_authenticated(self) -> bool:
        return self.auth_provider != "none"

    def as_dict(self) -> dict:
        return {
            "user_id": str(self.user_id),
            "role": self.role,
            "clearance": self.clearance.value,
            "email": self.email,
            "auth_provider": self.auth_provider,
            "authenticated": self.is_authenticated,
        }


def system_principal() -> Principal:
    """Full-clearance identity for trusted internal callers.

    Used by the offline evaluation runner, which must be able to retrieve
    across every classification or its scores would describe a corpus subset
    rather than the corpus. Deliberately a named, greppable function rather
    than a `clearance=None means everything` default, so every elevation is
    an explicit line of code someone can review.
    """
    return Principal(
        user_id=DEFAULT_USER_ID,
        role=Role.ADMIN.value,
        clearance=get_policy().clearance_for_role(Role.ADMIN.value),
    )


def anonymous_principal() -> Principal:
    """Least-privilege identity for callers that supplied no identity.

    Fails closed: an un-identified caller gets the policy's default
    clearance (`public`), never the maximum.
    """
    policy = get_policy()
    return Principal(
        user_id=DEFAULT_USER_ID,
        role=Role.VIEWER.value,
        clearance=policy.default_clearance,
    )


async def resolve_principal(request: Request) -> Principal:
    """Resolve the caller's identity and clearance.

    Two modes, chosen by `FIREBASE_ENABLED`:

    **Verified (production).** A Firebase ID token in the `Authorization:
    Bearer` header is cryptographically verified, the local user is
    provisioned or looked up, and the role comes from the database. This is
    what closes risk R-G03.

    **Development.** With Firebase disabled, identity comes from the
    `X-User-Id` header, which proves nothing. Every deployment reachable by
    anyone untrusted must enable Firebase; the startup log says so loudly
    when it is off.

    In both modes the *role* is read from the database and never from a
    client-supplied value, so a caller cannot escalate by asserting one.
    """
    settings = get_settings()
    policy = get_policy()

    if settings.firebase_enabled:
        principal = await _resolve_firebase_principal(request, settings, policy)
    else:
        principal = await _resolve_dev_principal(request, policy)

    request.state.principal = principal
    return principal


async def _resolve_firebase_principal(request: Request, settings, policy) -> Principal:
    from src.auth.firebase import FirebaseAuthError, get_verifier
    from src.auth.provisioning import provision_user

    token = _bearer_token(request)
    if not token:
        if settings.firebase_require_auth:
            access_denied.labels(reason="missing_token").inc()
            raise HTTPException(
                status_code=http_status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required. Supply a Firebase ID token as "
                "'Authorization: Bearer <token>'.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # Migration window only: anonymous traffic keeps working at the
        # lowest clearance while clients are updated.
        logger.warning("request_unauthenticated_allowed", path=request.url.path)
        return anonymous_principal()

    try:
        identity = get_verifier().verify(token)
    except FirebaseAuthError as exc:
        access_denied.labels(reason=f"token_{exc.reason}").inc()
        logger.warning("token_verification_failed", reason=exc.reason)
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if not identity.email_verified and settings.firebase_require_auth:
        # The claim can be legitimately stale: a Firebase ID token is a snapshot, so
        # someone who verifies their email after signing in keeps `email_verified:
        # false` for up to an hour, stranded behind a 403 that re-verifying cannot
        # fix. So on the *unverified* path only, check the live record before
        # refusing -- a round trip paid only when we are about to deny someone.
        if not _live_email_verified(identity.uid):
            access_denied.labels(reason="email_unverified").inc()
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail="Verify your email address, then sign out and back in.",
            )
        logger.info("email_verified_via_live_lookup", uid=identity.uid)

    user_id, role, is_active = await provision_user(identity)
    if not is_active:
        access_denied.labels(reason="account_inactive").inc()
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN, detail="User account is inactive."
        )

    return Principal(
        user_id=user_id,
        role=role,
        clearance=policy.clearance_for_role(role),
        is_active=is_active,
        email=identity.email,
        auth_provider="firebase",
    )


async def _resolve_dev_principal(request: Request, policy) -> Principal:
    raw_user_id = request.headers.get("X-User-Id")

    user_id = DEFAULT_USER_ID
    if raw_user_id:
        try:
            user_id = uuid.UUID(raw_user_id)
        except ValueError:
            logger.warning("principal_user_id_malformed", value=raw_user_id[:64])

    role, is_active = await _lookup_role(user_id)
    return Principal(
        user_id=user_id,
        role=role,
        clearance=policy.clearance_for_role(role),
        is_active=is_active,
        auth_provider="none",
    )


def _live_email_verified(uid: str) -> bool:
    """Ask Firebase for the account's current verification state.

    Fails closed: any error resolves to "not verified", so a Firebase outage
    cannot become a way past the check.
    """
    try:
        from firebase_admin import auth as firebase_auth

        from src.auth.firebase import get_verifier

        verifier = get_verifier()
        app = verifier._ensure_app()  # noqa: SLF001 - same package, single caller
        return bool(firebase_auth.get_user(uid, app=app).email_verified)
    except Exception as exc:
        logger.warning("live_email_verification_failed", uid=uid, error=str(exc))
        return False


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


async def _lookup_role(user_id: uuid.UUID) -> tuple[str, bool]:
    """Read role/active from the users table.

    A DB failure must not silently upgrade the caller, so any error resolves
    to the least-privileged role rather than propagating a 500 — the request
    then fails on the specific control it lacks clearance for, which is both
    safer and a clearer signal than an opaque database error.
    """
    from src.infrastructure.database.postgres.models import UserModel

    try:
        session_factory = get_session_factory()
        async with session_factory() as session:
            result = await session.execute(
                select(UserModel.role, UserModel.is_active).where(UserModel.id == user_id)
            )
            row = result.first()
    except Exception as exc:
        logger.warning("principal_lookup_failed", user_id=str(user_id), error=str(exc))
        return Role.VIEWER.value, True

    if row is None:
        return Role.VIEWER.value, True
    return (row[0] or Role.VIEWER.value), bool(row[1])


async def get_principal(principal: Principal = Depends(resolve_principal)) -> Principal:
    """Dependency for routes that need an identity but no specific role."""
    if not principal.is_active:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN, detail="User account is inactive."
        )
    return principal


def require_role(*allowed: Role):
    """Dependency factory gating a route on an explicit role allow-list.

    Allow-list rather than a rank comparison: role hierarchies quietly grant
    privileges nobody reviewed when a new role is inserted in the middle.
    """
    allowed_values = {r.value for r in allowed}

    async def _guard(principal: Principal = Depends(get_principal)) -> Principal:
        if principal.role not in allowed_values:
            logger.warning(
                "rbac_denied",
                user_id=str(principal.user_id),
                role=principal.role,
                required=sorted(allowed_values),
            )
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail=f"Role '{principal.role}' may not perform this action. "
                f"Required: {sorted(allowed_values)}.",
            )
        return principal

    return _guard
