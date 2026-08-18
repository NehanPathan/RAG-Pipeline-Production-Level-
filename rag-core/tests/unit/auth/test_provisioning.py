from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.auth.firebase import FirebaseIdentity
from src.auth.provisioning import local_user_id, provision_user
from src.config import Settings
from src.infrastructure.database.postgres.models import UserModel


class _FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _session_returning(existing):
    session = AsyncMock()
    session.add = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    session.execute = AsyncMock(return_value=result)
    return session


def _identity(**overrides) -> FirebaseIdentity:
    fields = {
        "uid": "firebase-uid-1",
        "email": "someone@example.com",
        "email_verified": True,
        "name": "Someone",
    }
    fields.update(overrides)
    return FirebaseIdentity(**fields)


def _existing_user(**overrides):
    user = MagicMock()
    user.id = local_user_id("firebase-uid-1")
    user.role = "steward"
    user.is_active = True
    user.firebase_uid = "firebase-uid-1"
    for key, value in overrides.items():
        setattr(user, key, value)
    return user


@pytest.fixture
def provision(request):
    """Runs `provision_user` against a fake session, yielding (result, session).

    Indirect parametrisation supplies the row the lookup should find: a
    `UserModel`-shaped mock for the returning-user paths, or None for a
    first sign-in.
    """
    existing = getattr(request, "param", None)
    session = _session_returning(existing)

    async def _run(identity=None, **settings_overrides):
        settings = Settings(_env_file=None, **settings_overrides)
        with (
            patch(
                "src.auth.provisioning.get_session_factory",
                return_value=MagicMock(return_value=_FakeSessionContext(session)),
            ),
            patch("src.auth.provisioning.get_settings", return_value=settings),
        ):
            return await provision_user(identity or _identity()), session

    return _run


class TestLocalUserId:
    def test_is_deterministic_for_a_given_firebase_uid(self):
        """A re-provisioned user must keep their id, or they lose ownership of
        every document and conversation they created."""
        assert local_user_id("uid-a") == local_user_id("uid-a")

    def test_differs_between_firebase_uids(self):
        assert local_user_id("uid-a") != local_user_id("uid-b")


class TestFirstSignIn:
    async def test_creates_the_user_with_the_deterministic_id(self, provision):
        (user_id, _role, _active), session = await provision()

        added = session.add.call_args[0][0]
        assert isinstance(added, UserModel)
        assert added.id == local_user_id("firebase-uid-1")
        assert user_id == added.id
        session.commit.assert_awaited()

    async def test_new_users_get_the_least_privileged_role(self, provision):
        """Authenticating proves who someone is and says nothing about what
        they may read; promotion is a separate, audited admin action."""
        (_user_id, role, is_active), session = await provision(firebase_default_role="viewer")

        assert role == "viewer"
        assert is_active is True
        assert session.add.call_args[0][0].role == "viewer"

    async def test_synthesises_an_address_when_the_token_carries_no_email(self, provision):
        _result, session = await provision(_identity(email=""))

        assert session.add.call_args[0][0].email == "firebase-uid-1@firebase.local"

    async def test_a_missing_display_name_is_stored_as_null_not_empty(self, provision):
        _result, session = await provision(_identity(name=""))

        assert session.add.call_args[0][0].display_name is None


@pytest.mark.parametrize("provision", [_existing_user()], indirect=True)
class TestReturningUser:
    async def test_keeps_the_stored_role(self, provision):
        """An existing role is never overwritten, so a promotion is not
        silently undone by the next login."""
        (_user_id, role, _active), session = await provision(firebase_default_role="viewer")

        assert role == "steward"
        session.add.assert_not_called()

    async def test_refreshes_the_profile_fields(self, provision):
        _result, session = await provision(_identity(name="New Name", email_verified=False))

        existing = session.execute.return_value.scalar_one_or_none.return_value
        assert existing.display_name == "New Name"
        assert existing.email_verified is False
        assert existing.last_login_at is not None
        session.commit.assert_awaited()

    async def test_does_not_blank_a_display_name_the_token_omits(self, provision):
        existing = _existing_user(display_name="Stored Name")
        session = _session_returning(existing)
        with (
            patch(
                "src.auth.provisioning.get_session_factory",
                return_value=MagicMock(return_value=_FakeSessionContext(session)),
            ),
            patch("src.auth.provisioning.get_settings", return_value=Settings(_env_file=None)),
        ):
            await provision_user(_identity(name=""))

        assert existing.display_name == "Stored Name"


class TestReturningUserEdgeCases:
    async def test_a_row_with_no_role_falls_back_to_the_default(self):
        session = _session_returning(_existing_user(role=None))
        with (
            patch(
                "src.auth.provisioning.get_session_factory",
                return_value=MagicMock(return_value=_FakeSessionContext(session)),
            ),
            patch(
                "src.auth.provisioning.get_settings",
                return_value=Settings(_env_file=None, firebase_default_role="viewer"),
            ),
        ):
            _user_id, role, _active = await provision_user(_identity())

        assert role == "viewer"

    async def test_a_deactivated_account_is_reported_as_inactive(self):
        session = _session_returning(_existing_user(is_active=False))
        with (
            patch(
                "src.auth.provisioning.get_session_factory",
                return_value=MagicMock(return_value=_FakeSessionContext(session)),
            ),
            patch("src.auth.provisioning.get_settings", return_value=Settings(_env_file=None)),
        ):
            _user_id, _role, is_active = await provision_user(_identity())

        assert is_active is False

    async def test_backfills_the_firebase_uid_on_a_pre_existing_local_row(self):
        """A user created before Firebase was enabled matches on id alone; the
        uid is filled in so subsequent logins match on it directly."""
        session = _session_returning(_existing_user(firebase_uid=None))
        with (
            patch(
                "src.auth.provisioning.get_session_factory",
                return_value=MagicMock(return_value=_FakeSessionContext(session)),
            ),
            patch("src.auth.provisioning.get_settings", return_value=Settings(_env_file=None)),
        ):
            await provision_user(_identity())

        existing = session.execute.return_value.scalar_one_or_none.return_value
        assert existing.firebase_uid == "firebase-uid-1"
