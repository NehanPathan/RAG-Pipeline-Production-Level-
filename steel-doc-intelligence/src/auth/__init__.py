"""Authentication — verifying *who* is calling.

This closes risk R-G03. Before this package, the platform had real
authorization (clearance filtering, role gates, audit attribution) resting on
an unverified `X-User-Id` header — every control was only as strong as the
network in front of the API.

Firebase is the right trade for this system: it supplies Google and email
sign-in, password reset, email verification and JWT issuance, all of which
would otherwise be weeks of security-sensitive code to write and a permanent
liability to maintain. The backend's job shrinks to verifying a signed token,
which is a small, well-defined problem.

    Client (React / Streamlit)
      └─ Firebase SDK sign-in
          └─ ID token (JWT, RS256, 1h)
              └─ Authorization: Bearer <token>
                  └─ FastAPI verifies signature, expiry, audience, issuer
                      └─ Principal (user_id, role, clearance)
                          └─ RBAC → clearance-filtered retrieval → audit

Secrets never live in code. The backend needs a service-account credential,
supplied either as a file path outside version control or as three
environment variables. See `docs/governance/AUTHENTICATION.md`.
"""

from src.auth.firebase import (
    FirebaseAuthError,
    FirebaseVerifier,
    get_verifier,
    reset_verifier,
)

__all__ = [
    "FirebaseAuthError",
    "FirebaseVerifier",
    "get_verifier",
    "reset_verifier",
]
