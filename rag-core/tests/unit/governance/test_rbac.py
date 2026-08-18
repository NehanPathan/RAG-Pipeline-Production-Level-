from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.auth.firebase import FirebaseAuthError, FirebaseIdentity
from src.config import Settings
from src.governance.policy import get_policy
from src.governance.rbac import (
    DEFAULT_USER_ID,
    Principal,
    Role,
    _bearer_token,
    _live_email_verified,
    _lookup_role,
    anonymous_principal,
    get_principal,
    require_role,
    resolve_principal,
    system_principal,
)


class _FakeRequest:
    def __init__(self, headers=None, path="/api/v1/chat"):
        self.headers = headers or {}
        self.url = SimpleNamespace(path=path)
        self.state = SimpleNamespace()


class _FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _session_with_row(row):
    session = AsyncMock()
    result = MagicMock()
    result.first.return_value = row
    session.execute = AsyncMock(return_value=result)
    return session


def _patched_session(row):
    return patch(
        "src.governance.rbac.get_session_factory",
        return_value=MagicMock(return_value=_FakeSessionContext(_session_with_row(row))),
    )


def _identity(**overrides) -> FirebaseIdentity:
    fields = {"uid": "uid-1", "email": "someone@example.com", "email_verified": True}
    fields.update(overrides)
    return FirebaseIdentity(**fields)


def _verifier_returning(identity):
    verifier = MagicMock()
    verifier.verify.return_value = identity
    return MagicMock(return_value=verifier)


class TestPrincipal:
    def test_is_admin_only_for_the_admin_role(self):
        clearance = get_policy().default_clearance
        admin = Principal(user_id=DEFAULT_USER_ID, role="admin", clearance=clearance)
        viewer = Principal(user_id=DEFAULT_USER_ID, role="viewer", clearance=clearance)

        assert admin.is_admin is True
        assert viewer.is_admin is False

    def test_the_development_header_does_not_count_as_authenticated(self):
        """"none" means the X-User-Id header, which proves nothing; a reviewer
        has to be able to tell that from a verified token."""
        clearance = get_policy().default_clearance
        dev = Principal(user_id=DEFAULT_USER_ID, role="viewer", clearance=clearance)
        verified = Principal(
            user_id=DEFAULT_USER_ID, role="viewer", clearance=clearance, auth_provider="firebase"
        )

        assert dev.is_authenticated is False
        assert verified.is_authenticated is True

    def test_as_dict_is_audit_shaped(self):
        principal = Principal(
            user_id=DEFAULT_USER_ID,
            role="analyst",
            clearance=get_policy().default_clearance,
            email="a@b.com",
            auth_provider="firebase",
        )

        assert principal.as_dict() == {
            "user_id": str(DEFAULT_USER_ID),
            "role": "analyst",
            "clearance": principal.clearance.value,
            "email": "a@b.com",
            "auth_provider": "firebase",
            "authenticated": True,
        }


class TestNamedPrincipals:
    def test_system_principal_is_admin_clearance(self):
        """The offline evaluation runner must retrieve across every
        classification, or its scores describe a subset of the corpus."""
        principal = system_principal()

        assert principal.role == Role.ADMIN.value
        assert principal.clearance == get_policy().clearance_for_role(Role.ADMIN.value)

    def test_anonymous_principal_fails_closed(self):
        principal = anonymous_principal()

        assert principal.role == Role.VIEWER.value
        assert principal.clearance == get_policy().default_clearance


class TestBearerToken:
    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ({"Authorization": "Bearer abc123"}, "abc123"),
            ({"Authorization": "bearer abc123"}, "abc123"),
            ({"Authorization": "Bearer   abc123  "}, "abc123"),
            ({"Authorization": "Basic abc123"}, ""),
            ({"Authorization": "abc123"}, ""),
            ({}, ""),
        ],
    )
    def test_only_the_bearer_scheme_yields_a_token(self, header, expected):
        assert _bearer_token(_FakeRequest(headers=header)) == expected


class TestLookupRole:
    async def test_reads_role_and_active_from_the_row(self):
        with _patched_session(("steward", True)):
            assert await _lookup_role(DEFAULT_USER_ID) == ("steward", True)

    async def test_an_unknown_user_gets_the_least_privileged_role(self):
        with _patched_session(None):
            assert await _lookup_role(DEFAULT_USER_ID) == (Role.VIEWER.value, True)

    async def test_a_null_role_column_falls_back_to_viewer(self):
        with _patched_session((None, True)):
            assert await _lookup_role(DEFAULT_USER_ID) == (Role.VIEWER.value, True)

    async def test_a_database_failure_never_upgrades_the_caller(self):
        """The request then fails on the specific control it lacks clearance
        for, which is safer and clearer than an opaque 500."""
        with patch(
            "src.governance.rbac.get_session_factory", side_effect=RuntimeError("db down")
        ):
            assert await _lookup_role(DEFAULT_USER_ID) == (Role.VIEWER.value, True)


class TestDevelopmentMode:
    async def test_reads_the_identity_from_the_x_user_id_header(self):
        user_id = uuid.uuid4()
        request = _FakeRequest(headers={"X-User-Id": str(user_id)})

        with (
            patch("src.governance.rbac.get_settings", return_value=Settings(_env_file=None)),
            _patched_session(("analyst", True)),
        ):
            principal = await resolve_principal(request)

        assert principal.user_id == user_id
        assert principal.role == "analyst"
        assert principal.auth_provider == "none"

    async def test_a_malformed_header_falls_back_to_the_default_identity(self):
        request = _FakeRequest(headers={"X-User-Id": "not-a-uuid"})

        with (
            patch("src.governance.rbac.get_settings", return_value=Settings(_env_file=None)),
            _patched_session(None),
        ):
            principal = await resolve_principal(request)

        assert principal.user_id == DEFAULT_USER_ID

    async def test_the_resolved_principal_is_stashed_on_the_request(self):
        request = _FakeRequest()

        with (
            patch("src.governance.rbac.get_settings", return_value=Settings(_env_file=None)),
            _patched_session(None),
        ):
            principal = await resolve_principal(request)

        assert request.state.principal is principal

    async def test_the_role_is_never_taken_from_a_client_header(self):
        """A caller asserting `X-Role: admin` must not escalate; the role only
        ever comes from the database."""
        request = _FakeRequest(headers={"X-User-Id": str(DEFAULT_USER_ID), "X-Role": "admin"})

        with (
            patch("src.governance.rbac.get_settings", return_value=Settings(_env_file=None)),
            _patched_session(("viewer", True)),
        ):
            principal = await resolve_principal(request)

        assert principal.role == "viewer"


class TestFirebaseMode:
    def _settings(self, **overrides):
        overrides.setdefault("firebase_enabled", True)
        return Settings(_env_file=None, **overrides)

    async def test_a_missing_token_is_a_401_with_a_challenge_header(self):
        request = _FakeRequest()

        with (
            patch("src.governance.rbac.get_settings", return_value=self._settings()),
            pytest.raises(HTTPException) as exc_info,
        ):
            await resolve_principal(request)

        assert exc_info.value.status_code == 401
        assert exc_info.value.headers["WWW-Authenticate"] == "Bearer"

    async def test_anonymous_traffic_is_tolerated_during_a_migration_window(self):
        request = _FakeRequest()

        with patch(
            "src.governance.rbac.get_settings",
            return_value=self._settings(firebase_require_auth=False),
        ):
            principal = await resolve_principal(request)

        assert principal.role == Role.VIEWER.value
        assert principal.is_authenticated is False

    async def test_a_rejected_token_becomes_a_401(self):
        request = _FakeRequest(headers={"Authorization": "Bearer bad"})
        verifier = MagicMock()
        verifier.verify.side_effect = FirebaseAuthError("Token has expired.", reason="expired")

        with (
            patch("src.governance.rbac.get_settings", return_value=self._settings()),
            patch("src.auth.firebase.get_verifier", return_value=verifier),
            pytest.raises(HTTPException) as exc_info,
        ):
            await resolve_principal(request)

        assert exc_info.value.status_code == 401

    async def test_a_verified_token_yields_a_firebase_principal(self):
        request = _FakeRequest(headers={"Authorization": "Bearer good"})
        user_id = uuid.uuid4()

        with (
            patch("src.governance.rbac.get_settings", return_value=self._settings()),
            patch("src.auth.firebase.get_verifier", _verifier_returning(_identity())),
            patch(
                "src.auth.provisioning.provision_user",
                new=AsyncMock(return_value=(user_id, "analyst", True)),
            ),
        ):
            principal = await resolve_principal(request)

        assert principal.user_id == user_id
        assert principal.role == "analyst"
        assert principal.email == "someone@example.com"
        assert principal.auth_provider == "firebase"

    async def test_an_unverified_email_is_rechecked_live_before_refusing(self):
        """A token is a snapshot: someone who verifies their email after
        signing in would otherwise be stranded behind a 403 for an hour."""
        request = _FakeRequest(headers={"Authorization": "Bearer good"})
        user_id = uuid.uuid4()

        with (
            patch("src.governance.rbac.get_settings", return_value=self._settings()),
            patch(
                "src.auth.firebase.get_verifier",
                _verifier_returning(_identity(email_verified=False)),
            ),
            patch("src.governance.rbac._live_email_verified", return_value=True) as live,
            patch(
                "src.auth.provisioning.provision_user",
                new=AsyncMock(return_value=(user_id, "viewer", True)),
            ),
        ):
            principal = await resolve_principal(request)

        live.assert_called_once_with("uid-1")
        assert principal.user_id == user_id

    async def test_a_still_unverified_email_is_a_403(self):
        request = _FakeRequest(headers={"Authorization": "Bearer good"})

        with (
            patch("src.governance.rbac.get_settings", return_value=self._settings()),
            patch(
                "src.auth.firebase.get_verifier",
                _verifier_returning(_identity(email_verified=False)),
            ),
            patch("src.governance.rbac._live_email_verified", return_value=False),
            pytest.raises(HTTPException) as exc_info,
        ):
            await resolve_principal(request)

        assert exc_info.value.status_code == 403

    async def test_a_deactivated_account_is_refused(self):
        request = _FakeRequest(headers={"Authorization": "Bearer good"})

        with (
            patch("src.governance.rbac.get_settings", return_value=self._settings()),
            patch("src.auth.firebase.get_verifier", _verifier_returning(_identity())),
            patch(
                "src.auth.provisioning.provision_user",
                new=AsyncMock(return_value=(uuid.uuid4(), "viewer", False)),
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            await resolve_principal(request)

        assert exc_info.value.status_code == 403


class TestLiveEmailVerification:
    def test_reads_the_current_state_from_firebase(self):
        verifier = MagicMock()
        firebase_auth = MagicMock()
        firebase_auth.get_user.return_value = SimpleNamespace(email_verified=True)

        with (
            patch("src.auth.firebase.get_verifier", return_value=verifier),
            patch.dict("sys.modules", {}),
            patch("firebase_admin.auth.get_user", firebase_auth.get_user),
        ):
            assert _live_email_verified("uid-1") is True

    def test_fails_closed_when_firebase_is_unreachable(self):
        """A Firebase outage must not become a way past the check."""
        with patch("src.auth.firebase.get_verifier", side_effect=RuntimeError("down")):
            assert _live_email_verified("uid-1") is False


class TestGuards:
    def _principal(self, role="viewer", is_active=True):
        return Principal(
            user_id=DEFAULT_USER_ID,
            role=role,
            clearance=get_policy().clearance_for_role(role),
            is_active=is_active,
        )

    async def test_get_principal_passes_an_active_identity_through(self):
        principal = self._principal()

        assert await get_principal(principal) is principal

    async def test_get_principal_refuses_an_inactive_account(self):
        with pytest.raises(HTTPException) as exc_info:
            await get_principal(self._principal(is_active=False))

        assert exc_info.value.status_code == 403

    async def test_require_role_admits_a_listed_role(self):
        guard = require_role(Role.ANALYST, Role.ADMIN)
        principal = self._principal(role="analyst")

        assert await guard(principal) is principal

    async def test_require_role_is_an_allow_list_not_a_ranking(self):
        """A hierarchy quietly grants privileges nobody reviewed the day a new
        role is inserted in the middle."""
        guard = require_role(Role.ANALYST)

        with pytest.raises(HTTPException) as exc_info:
            await guard(self._principal(role="admin"))

        assert exc_info.value.status_code == 403
        assert "analyst" in exc_info.value.detail
