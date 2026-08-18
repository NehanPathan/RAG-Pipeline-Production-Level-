from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from src.auth.firebase import FirebaseIdentity
from src.config import get_settings
from src.infrastructure.database.postgres.connection import get_session_factory
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

# Deterministic namespace so a Firebase uid always maps to the same local
# UUID, even if provisioning is retried or the row is recreated. Without
# this, a re-provisioned user would get a new id and lose ownership of every
# document and conversation they had created.
_FIREBASE_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def local_user_id(firebase_uid: str) -> uuid.UUID:
    return uuid.uuid5(_FIREBASE_NAMESPACE, firebase_uid)


async def provision_user(identity: FirebaseIdentity) -> tuple[uuid.UUID, str, bool]:
    """Find or create the local user for a verified identity.

    Returns (user_id, role, is_active).

    New users always get `firebase_default_role` — deliberately the least
    privileged one. Authenticating proves who someone is; it says nothing
    about what they should be allowed to read, and conflating the two is how
    "sign in with Google" quietly becomes "anyone with a Google account is an
    analyst". Promotion is a separate, audited admin action.

    An existing user's role is never overwritten here, so a promotion is not
    silently undone by the next login.
    """
    from src.infrastructure.database.postgres.models import UserModel

    settings = get_settings()
    user_id = local_user_id(identity.uid)
    session_factory = get_session_factory()

    async with session_factory() as session:
        existing = (
            await session.execute(
                select(UserModel).where(
                    (UserModel.firebase_uid == identity.uid) | (UserModel.id == user_id)
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            existing.last_login_at = datetime.now(UTC)
            # Keep profile fields fresh, never the role.
            existing.firebase_uid = existing.firebase_uid or identity.uid
            existing.email_verified = identity.email_verified
            if identity.name:
                existing.display_name = identity.name
            await session.commit()
            return existing.id, (existing.role or settings.firebase_default_role), bool(existing.is_active)

        user = UserModel(
            id=user_id,
            email=identity.email or f"{identity.uid}@firebase.local",
            firebase_uid=identity.uid,
            display_name=identity.name or None,
            email_verified=identity.email_verified,
            role=settings.firebase_default_role,
            is_active=True,
            last_login_at=datetime.now(UTC),
        )
        session.add(user)
        await session.commit()

    logger.info(
        "user_provisioned",
        user_id=str(user_id),
        email=identity.email,
        role=settings.firebase_default_role,
    )
    return user_id, settings.firebase_default_role, True
