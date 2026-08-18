from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from firebase_admin import auth as firebase_auth

from src.auth.firebase import (
    FirebaseAuthError,
    FirebaseIdentity,
    FirebaseVerifier,
    get_verifier,
    reset_verifier,
)
from src.config import Settings


def _settings(**overrides) -> Settings:
    overrides.setdefault("firebase_enabled", True)
    # The default is a real relative path ("config/firebase-service-account.json").
    # Blanking it stops these tests from behaving differently on a machine that
    # happens to have a service-account file checked out.
    overrides.setdefault("firebase_service_account", "")
    return Settings(_env_file=None, **overrides)


def _verifier(**overrides) -> FirebaseVerifier:
    return FirebaseVerifier(settings=_settings(**overrides))


def _ready_verifier(**overrides) -> FirebaseVerifier:
    """A verifier with the Admin SDK app already in place, so `verify()` goes
    straight to token checking without initialising anything."""
    verifier = _verifier(**overrides)
    verifier._app = MagicMock()
    return verifier


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_verifier()
    yield
    reset_verifier()


class TestFirebaseIdentity:
    def test_domain_is_the_part_after_the_at_sign(self):
        identity = FirebaseIdentity(uid="u1", email="someone@Example.COM", email_verified=True)

        assert identity.domain == "example.com"

    def test_domain_is_empty_when_there_is_no_email(self):
        assert FirebaseIdentity(uid="u1", email="", email_verified=False).domain == ""


class TestCredentialLoading:
    def test_reads_the_service_account_file_when_one_exists(self, tmp_path):
        payload = {"type": "service_account", "project_id": "from-file"}
        path = tmp_path / "sa.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

        creds = _verifier(firebase_service_account=str(path))._load_credentials()

        assert creds == payload

    def test_falls_back_to_discrete_env_vars(self):
        creds = _verifier(
            firebase_project_id="proj",
            firebase_client_email="sa@proj.iam.gserviceaccount.com",
            firebase_private_key="-----BEGIN-----\\nline2\\n-----END-----",
        )._load_credentials()

        assert creds["type"] == "service_account"
        assert creds["project_id"] == "proj"
        # The escaped newlines an environment variable carries must come back
        # as real ones, or the PEM fails to parse with an opaque crypto error.
        assert creds["private_key"] == "-----BEGIN-----\nline2\n-----END-----"

    def test_a_half_configured_service_account_names_the_missing_variable(self):
        verifier = _verifier(
            firebase_project_id="proj",
            firebase_client_email="sa@proj.iam.gserviceaccount.com",
            firebase_private_key="",
        )

        with pytest.raises(FirebaseAuthError) as exc_info:
            verifier._load_credentials()

        assert exc_info.value.reason == "unavailable"
        assert "FIREBASE_PRIVATE_KEY" in str(exc_info.value)

    def test_no_credentials_at_all_is_unavailable_not_invalid(self):
        with pytest.raises(FirebaseAuthError) as exc_info:
            _verifier()._load_credentials()

        assert exc_info.value.reason == "unavailable"


class TestEnsureApp:
    def test_initialises_once_and_caches_the_app(self):
        verifier = _verifier(firebase_project_id="proj")
        app = MagicMock()

        with (
            patch.object(verifier, "_load_credentials", return_value={}),
            patch("firebase_admin.credentials.Certificate", return_value=MagicMock()),
            patch("firebase_admin.initialize_app", return_value=app) as init,
        ):
            assert verifier._ensure_app() is app
            assert verifier._ensure_app() is app

        init.assert_called_once()

    def test_reuses_the_existing_app_when_already_initialised(self):
        """A reload in development re-runs initialisation; `initialize_app`
        raises ValueError for the duplicate name and the existing app is
        adopted instead of failing the request."""
        verifier = _verifier(firebase_project_id="proj")
        existing = MagicMock()

        with (
            patch.object(verifier, "_load_credentials", return_value={}),
            patch("firebase_admin.credentials.Certificate", return_value=MagicMock()),
            patch("firebase_admin.initialize_app", side_effect=ValueError("already exists")),
            patch("firebase_admin.get_app", return_value=existing),
        ):
            assert verifier._ensure_app() is existing

    def test_a_credential_failure_is_remembered_and_not_retried(self):
        verifier = _verifier(firebase_project_id="proj")

        with (
            patch.object(verifier, "_load_credentials", return_value={}),
            patch("firebase_admin.credentials.Certificate", side_effect=RuntimeError("bad key")),
            pytest.raises(FirebaseAuthError) as first,
        ):
            verifier._ensure_app()

        assert first.value.reason == "unavailable"

        # Second call must not reach the SDK again: nothing is patched here, so
        # a retry would raise something other than FirebaseAuthError.
        with pytest.raises(FirebaseAuthError) as second:
            verifier._ensure_app()

        assert second.value.reason == "unavailable"


class TestVerify:
    def test_an_empty_token_is_missing_not_invalid(self):
        """The two are separated deliberately: a flood of `missing` is a client
        that forgot the header, a flood of `invalid` is someone forging tokens."""
        with pytest.raises(FirebaseAuthError) as exc_info:
            _ready_verifier().verify("")

        assert exc_info.value.reason == "missing"

    def test_returns_the_claims_this_system_uses(self):
        verifier = _ready_verifier()
        claims = {
            "uid": "firebase-uid-1",
            "email": "Someone@Example.com",
            "email_verified": True,
            "name": "Someone",
            "picture": "https://example.com/p.png",
        }

        with patch.object(firebase_auth, "verify_id_token", return_value=claims):
            identity = verifier.verify("a-token")

        assert identity.uid == "firebase-uid-1"
        # Lower-cased so the domain allow-list and the users table agree on one
        # spelling of an address.
        assert identity.email == "someone@example.com"
        assert identity.email_verified is True
        assert identity.name == "Someone"
        assert identity.claims == claims

    def test_accepts_user_id_as_an_alias_for_uid(self):
        with patch.object(
            firebase_auth, "verify_id_token", return_value={"user_id": "u-2", "email": "a@b.com"}
        ):
            identity = _ready_verifier().verify("a-token")

        assert identity.uid == "u-2"
        assert identity.email_verified is False

    @pytest.mark.parametrize(
        ("error", "expected_reason"),
        [
            (firebase_auth.ExpiredIdTokenError("expired", None), "expired"),
            (firebase_auth.RevokedIdTokenError("revoked"), "revoked"),
            (ValueError("malformed"), "invalid"),
        ],
    )
    def test_each_failure_mode_gets_its_own_reason(self, error, expected_reason):
        with (
            patch.object(firebase_auth, "verify_id_token", side_effect=error),
            pytest.raises(FirebaseAuthError) as exc_info,
        ):
            _ready_verifier().verify("a-token")

        assert exc_info.value.reason == expected_reason

    def test_does_not_check_revocation_on_every_request(self):
        """check_revoked would add a Firebase round-trip per request; with a 1h
        token lifetime the exposure window is bounded instead."""
        with patch.object(firebase_auth, "verify_id_token", return_value={"uid": "u"}) as verify:
            _ready_verifier().verify("a-token")

        assert verify.call_args.kwargs["check_revoked"] is False


class TestDomainAllowList:
    def test_an_unlisted_domain_is_refused(self):
        verifier = _ready_verifier(firebase_allowed_domains="corp.example")

        with (
            patch.object(
                firebase_auth, "verify_id_token", return_value={"uid": "u", "email": "a@gmail.com"}
            ),
            pytest.raises(FirebaseAuthError) as exc_info,
        ):
            verifier.verify("a-token")

        assert exc_info.value.reason == "domain_denied"

    def test_a_listed_domain_is_allowed(self):
        verifier = _ready_verifier(firebase_allowed_domains="corp.example, other.example")

        with patch.object(
            firebase_auth,
            "verify_id_token",
            return_value={"uid": "u", "email": "a@Corp.Example"},
        ):
            identity = verifier.verify("a-token")

        assert identity.domain == "corp.example"

    def test_an_empty_allow_list_admits_any_verified_email(self):
        with patch.object(
            firebase_auth, "verify_id_token", return_value={"uid": "u", "email": "a@anywhere.com"}
        ):
            identity = _ready_verifier().verify("a-token")

        assert identity.email == "a@anywhere.com"


class TestVerifierSingleton:
    def test_get_verifier_returns_the_same_instance(self):
        assert get_verifier() is get_verifier()

    def test_reset_verifier_forces_a_rebuild(self):
        first = get_verifier()
        reset_verifier()

        assert get_verifier() is not first

    def test_enabled_reflects_settings(self):
        assert _verifier(firebase_enabled=False).enabled is False
        assert _verifier(firebase_enabled=True).enabled is True
