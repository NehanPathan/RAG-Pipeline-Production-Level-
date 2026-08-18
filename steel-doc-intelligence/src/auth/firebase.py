from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config import Settings, get_settings
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import auth_verifications

logger = get_logger(__name__)


class FirebaseAuthError(Exception):
    """Token verification failed.

    `reason` is a short machine-readable code used as a metric label, so the
    cardinality stays bounded and "tokens are expiring" is distinguishable
    from "tokens are forged" on a dashboard.
    """

    def __init__(self, message: str, reason: str = "invalid") -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class FirebaseIdentity:
    """The verified claims this system actually uses.

    A deliberately narrow subset of the token. Firebase tokens carry a lot
    more; copying only what is needed keeps it obvious what the system trusts.
    """

    uid: str
    email: str
    email_verified: bool
    name: str = ""
    picture: str = ""
    provider: str = "firebase"
    claims: dict[str, Any] = None  # type: ignore[assignment]

    @property
    def domain(self) -> str:
        return self.email.rsplit("@", 1)[-1].lower() if "@" in self.email else ""


class FirebaseVerifier:
    """Verifies Firebase ID tokens using the Admin SDK.

    Verification is delegated to `firebase_admin.auth.verify_id_token` rather
    than hand-rolled with a JWT library. That call checks the RS256
    signature against Google's rotating public keys, the expiry, the issuer
    and the audience — and gets the key rotation right, which is the part
    hand-rolled implementations reliably get wrong.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._app = None
        self._unavailable_reason: str | None = None

    @property
    def enabled(self) -> bool:
        return self._settings.firebase_enabled

    def _ensure_app(self):
        """Initialise the Admin SDK once, lazily.

        Lazy because the import and credential load are only needed when
        Firebase is actually enabled — a deployment running without it should
        not require the package to be installed.
        """
        if self._app is not None:
            return self._app
        if self._unavailable_reason:
            raise FirebaseAuthError(self._unavailable_reason, reason="unavailable")

        try:
            import firebase_admin
            from firebase_admin import credentials
        except ImportError as exc:
            self._unavailable_reason = (
                "firebase-admin is not installed but FIREBASE_ENABLED is true. "
                "Install it or disable Firebase."
            )
            raise FirebaseAuthError(self._unavailable_reason, reason="unavailable") from exc

        try:
            cred = credentials.Certificate(self._load_credentials())
            # A named app avoids clashing with any default app the host
            # process may already have initialised.
            self._app = firebase_admin.initialize_app(cred, name="prod-rag")
        except ValueError:
            # Already initialised — happens on reload in development.
            self._app = firebase_admin.get_app(name="prod-rag")
        except Exception as exc:
            self._unavailable_reason = f"Firebase credentials could not be loaded: {exc}"
            logger.error("firebase_init_failed", error=str(exc))
            raise FirebaseAuthError(self._unavailable_reason, reason="unavailable") from exc

        logger.info("firebase_initialised", project=self._settings.firebase_project_id or "(from file)")
        return self._app

    def _load_credentials(self) -> dict:
        """Service-account credentials, from a file or discrete env vars.

        Both forms are supported because deployments differ: a container
        platform mounts a secret file, while a PaaS usually only offers
        environment variables. Neither form ever appears in the repository.
        """
        settings = self._settings

        path = Path(settings.firebase_service_account)
        if settings.firebase_service_account and path.exists():
            logger.info("firebase_credentials_from_file", path=str(path))
            return json.loads(path.read_text(encoding="utf-8"))

        if settings.firebase_project_id and settings.firebase_client_email:
            if not settings.firebase_private_key:
                raise FirebaseAuthError(
                    "FIREBASE_PRIVATE_KEY is empty. The service account needs all three of "
                    "FIREBASE_PROJECT_ID, FIREBASE_CLIENT_EMAIL and FIREBASE_PRIVATE_KEY.",
                    reason="unavailable",
                )
            logger.info("firebase_credentials_from_env", project=settings.firebase_project_id)
            return {
                "type": "service_account",
                "project_id": settings.firebase_project_id,
                "client_email": settings.firebase_client_email,
                # Escaped newlines are the standard way a PEM key survives an
                # environment variable; unescaping here saves every deployment
                # from discovering it via an opaque crypto error.
                "private_key": settings.firebase_private_key_normalized,
                "token_uri": "https://oauth2.googleapis.com/token",
            }

        raise FirebaseAuthError(
            f"No Firebase credentials. Either place a service-account JSON at "
            f"{settings.firebase_service_account!r} (git-ignored) or set "
            f"FIREBASE_PROJECT_ID, FIREBASE_CLIENT_EMAIL and FIREBASE_PRIVATE_KEY.",
            reason="unavailable",
        )

    def verify(self, token: str) -> FirebaseIdentity:
        """Verify an ID token and return its identity, or raise.

        Every failure path increments a metric with a distinct reason, so an
        attack (many `invalid`) is distinguishable from a client bug (many
        `expired`) without reading logs.
        """
        if not token:
            auth_verifications.labels(provider="firebase", result="missing").inc()
            raise FirebaseAuthError("No token supplied.", reason="missing")

        self._ensure_app()
        from firebase_admin import auth as firebase_auth

        try:
            # check_revoked hits Firebase on every call and would add a
            # round-trip to each request; with a 1h token lifetime the
            # exposure window is bounded and the latency is not worth it.
            claims = firebase_auth.verify_id_token(token, app=self._app, check_revoked=False)
        except firebase_auth.ExpiredIdTokenError as exc:
            auth_verifications.labels(provider="firebase", result="expired").inc()
            raise FirebaseAuthError("Token has expired.", reason="expired") from exc
        except firebase_auth.RevokedIdTokenError as exc:
            auth_verifications.labels(provider="firebase", result="revoked").inc()
            raise FirebaseAuthError("Token has been revoked.", reason="revoked") from exc
        except Exception as exc:
            auth_verifications.labels(provider="firebase", result="invalid").inc()
            raise FirebaseAuthError(f"Token verification failed: {exc}", reason="invalid") from exc

        identity = FirebaseIdentity(
            uid=str(claims.get("uid") or claims.get("user_id") or ""),
            email=str(claims.get("email") or "").lower(),
            email_verified=bool(claims.get("email_verified", False)),
            name=str(claims.get("name") or ""),
            picture=str(claims.get("picture") or ""),
            claims=claims,
        )

        self._enforce_domain(identity)
        auth_verifications.labels(provider="firebase", result="ok").inc()
        return identity

    def _enforce_domain(self, identity: FirebaseIdentity) -> None:
        """Restrict sign-in to allow-listed email domains, when configured.

        Without this, "sign in with Google" means *anyone with a Google
        account*, which is almost never the intent for an internal corpus.
        """
        allowed = self._settings.firebase_allowed_domains_list
        if not allowed:
            return
        if identity.domain not in allowed:
            auth_verifications.labels(provider="firebase", result="domain_denied").inc()
            logger.warning(
                "firebase_domain_denied", domain=identity.domain, allowed=allowed
            )
            raise FirebaseAuthError(
                f"Sign-in is restricted to {allowed}.", reason="domain_denied"
            )


_verifier: FirebaseVerifier | None = None


def get_verifier() -> FirebaseVerifier:
    global _verifier
    if _verifier is None:
        _verifier = FirebaseVerifier()
    return _verifier


def reset_verifier() -> None:
    """Test helper: rebuild from settings on next access."""
    global _verifier
    _verifier = None
