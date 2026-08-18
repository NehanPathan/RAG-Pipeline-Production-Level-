"""Firebase authentication for the Streamlit UI.

Streamlit renders server-side, so the Firebase JavaScript SDK is not
available — there is no browser context to run it in. Instead this talks to
the Firebase Auth **REST API** directly, which supports the same operations
(sign-in, sign-up, email verification, password reset, token refresh) and
returns the same ID tokens the backend already verifies.

    Streamlit  ──POST identitytoolkit.googleapis.com──▶  Firebase
        │                                                    │
        │◀──────────── idToken + refreshToken ───────────────┘
        │
        └── Authorization: Bearer <idToken> ──▶ FastAPI ──▶ verify ──▶ Principal

The Web API key used here is **not a secret**. It identifies the Firebase
project and is visible in any browser that talks to Firebase. What protects
data is the backend's token verification plus Firebase Security Rules — never
the secrecy of this key. (The *service account* on the backend is the
opposite: it signs tokens for every user and must never be exposed.)

Tokens live in `st.session_state`, which is per-browser-session and
server-side, so the token never lands in a cookie or in page HTML.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass

import requests
import streamlit as st

WEB_API_KEY = os.getenv("FIREBASE_WEB_API_KEY", "").strip()
AUTH_ENABLED = os.getenv("FIREBASE_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
ALLOWED_DOMAINS = [
    d.strip().lower()
    for d in os.getenv("FIREBASE_ALLOWED_DOMAINS", "").split(",")
    if d.strip()
]

_IDENTITY = "https://identitytoolkit.googleapis.com/v1/accounts"
_SECURE_TOKEN = "https://securetoken.googleapis.com/v1/token"
_TIMEOUT = 20

# Refresh this far before expiry. A token that expires mid-request produces a
# confusing 401 on an action the user just took; refreshing early makes that
# impossible without adding a refresh call to every request.
_REFRESH_MARGIN_SECONDS = 300

SESSION_KEY = "firebase_auth"

# Firebase returns machine codes; these are what a person can act on.
_ERROR_MESSAGES = {
    "EMAIL_NOT_FOUND": "No account with that email address.",
    "INVALID_PASSWORD": "Incorrect password.",
    "INVALID_LOGIN_CREDENTIALS": "Email or password is incorrect.",
    "USER_DISABLED": "This account has been disabled by an administrator.",
    "EMAIL_EXISTS": "An account with that email already exists — sign in instead.",
    # Firebase returns two different codes for the same misconfiguration
    # depending on the endpoint, and neither says what to actually do.
    "OPERATION_NOT_ALLOWED": (
        "Email/password sign-in is not enabled for this Firebase project. "
        "Firebase console → Authentication → Sign-in method → enable "
        "**Email/Password**."
    ),
    "PASSWORD_LOGIN_DISABLED": (
        "Email/password sign-in is not enabled for this Firebase project. "
        "Firebase console → Authentication → Sign-in method → enable "
        "**Email/Password**."
    ),
    "ADMIN_ONLY_OPERATION": (
        "Self-service sign-up is disabled for this project. An administrator "
        "must create the account, or re-enable sign-up under Authentication → "
        "Settings → User actions."
    ),
    "API key not valid. Please pass a valid API key.": (
        "FIREBASE_WEB_API_KEY is wrong for this project. Copy it from Firebase "
        "console → Project settings → General → Web API Key."
    ),
    "TOO_MANY_ATTEMPTS_TRY_LATER": "Too many attempts. Wait a few minutes and try again.",
    "WEAK_PASSWORD : Password should be at least 6 characters": (
        "Password must be at least 6 characters."
    ),
    "INVALID_EMAIL": "That email address is not valid.",
    "MISSING_PASSWORD": "Enter a password.",
    "TOKEN_EXPIRED": "Your session expired. Sign in again.",
    "INVALID_REFRESH_TOKEN": "Your session is no longer valid. Sign in again.",
}


class AuthError(Exception):
    """A sign-in problem worth showing the user verbatim."""


@dataclass
class AuthSession:
    id_token: str
    refresh_token: str
    email: str
    local_id: str
    expires_at: float
    email_verified: bool = False
    display_name: str = ""

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at

    @property
    def needs_refresh(self) -> bool:
        return time.time() >= (self.expires_at - _REFRESH_MARGIN_SECONDS)

    @property
    def expires_in_minutes(self) -> int:
        return max(0, int((self.expires_at - time.time()) / 60))


# ------------------------------------------------------------------ REST


def _post(url: str, payload: dict) -> dict:
    if not WEB_API_KEY:
        raise AuthError(
            "FIREBASE_WEB_API_KEY is not set. Get it from the Firebase console → "
            "Project settings → General → Web API Key, and add it to .env."
        )
    try:
        response = requests.post(f"{url}?key={WEB_API_KEY}", json=payload, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise AuthError(f"Could not reach Firebase: {exc}") from exc

    data = response.json() if response.content else {}
    if response.status_code >= 400:
        code = str(data.get("error", {}).get("message", "UNKNOWN_ERROR"))
        raise AuthError(_ERROR_MESSAGES.get(code, f"Firebase rejected the request: {code}"))
    return data


def _check_domain(email: str) -> None:
    """Fail fast on a disallowed domain.

    The backend enforces this too — that is the control. Checking here as well
    turns an opaque 401 on the first API call into a clear message at the
    point of sign-in.
    """
    if not ALLOWED_DOMAINS:
        return
    domain = email.rsplit("@", 1)[-1].lower()
    if domain not in ALLOWED_DOMAINS:
        raise AuthError(
            f"Sign-in is restricted to: {', '.join(ALLOWED_DOMAINS)}. "
            f"'{domain}' is not permitted."
        )


def sign_in(email: str, password: str) -> AuthSession:
    _check_domain(email)
    data = _post(
        f"{_IDENTITY}:signInWithPassword",
        {"email": email, "password": password, "returnSecureToken": True},
    )
    session = AuthSession(
        id_token=data["idToken"],
        refresh_token=data["refreshToken"],
        email=data.get("email", email),
        local_id=data.get("localId", ""),
        expires_at=time.time() + int(data.get("expiresIn", 3600)),
        display_name=data.get("displayName", ""),
    )
    return _sync_verification(session)


def sign_up(email: str, password: str) -> AuthSession:
    _check_domain(email)
    data = _post(
        f"{_IDENTITY}:signUp",
        {"email": email, "password": password, "returnSecureToken": True},
    )
    session = AuthSession(
        id_token=data["idToken"],
        refresh_token=data["refreshToken"],
        email=data.get("email", email),
        local_id=data.get("localId", ""),
        expires_at=time.time() + int(data.get("expiresIn", 3600)),
    )
    # The backend rejects unverified emails, so sending this immediately is
    # not a nicety — without it the new account cannot use the product.
    send_verification_email(session.id_token)
    return session


def send_verification_email(id_token: str) -> None:
    _post(f"{_IDENTITY}:sendOobCode", {"requestType": "VERIFY_EMAIL", "idToken": id_token})


def send_password_reset(email: str) -> None:
    _post(f"{_IDENTITY}:sendOobCode", {"requestType": "PASSWORD_RESET", "email": email})


def _is_verified(id_token: str) -> bool:
    """Live verification state, read from Firebase rather than the token."""
    try:
        data = _post(f"{_IDENTITY}:lookup", {"idToken": id_token})
        users = data.get("users", [])
        return bool(users and users[0].get("emailVerified", False))
    except AuthError:
        return False


def _token_claims(id_token: str) -> dict:
    """Decode the ID token payload *without* verifying it.

    Safe here because this is only used to detect a stale claim, never to
    make an access decision — the backend does the real cryptographic
    verification. Decoding locally avoids an extra round-trip.
    """
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)  # restore base64url padding
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def _sync_verification(session: AuthSession) -> AuthSession:
    """Make the token's `email_verified` claim match reality.

    The bug this fixes: a Firebase ID token is a snapshot. Clicking the
    verification link updates the *account*, but any token minted beforehand
    still carries `email_verified: false` for its full hour. The backend
    reads that claim and returns 403 — before it ever provisions the user —
    so the UI would show a signed-in session that could not call anything.

    Refreshing mints a new token carrying the updated claim.
    """
    session.email_verified = _is_verified(session.id_token)
    if session.email_verified and not _token_claims(session.id_token).get("email_verified"):
        try:
            session = refresh(session)
        except AuthError:
            # Keep the live answer; the next refresh cycle will catch up.
            pass
    return session


def refresh(session: AuthSession) -> AuthSession:
    data = _post(
        _SECURE_TOKEN, {"grant_type": "refresh_token", "refresh_token": session.refresh_token}
    )
    session.id_token = data["id_token"]
    session.refresh_token = data["refresh_token"]
    session.expires_at = time.time() + int(data.get("expires_in", 3600))
    return session


# --------------------------------------------------------------- session


def current_session() -> AuthSession | None:
    """The signed-in session, refreshed if it is close to expiring.

    Returns None (rather than raising) when the refresh fails, so an expired
    session lands the user back on the sign-in form instead of on a traceback.
    """
    session: AuthSession | None = st.session_state.get(SESSION_KEY)
    if session is None:
        return None

    if session.needs_refresh:
        try:
            session = refresh(session)
            st.session_state[SESSION_KEY] = session
        except AuthError:
            st.session_state.pop(SESSION_KEY, None)
            return None

    # Self-heal a stale verification claim. A session created before the user
    # clicked the verification link holds a token saying
    # `email_verified: false`, and because the session itself records the
    # *live* answer (true) the re-check screen never appears -- leaving the
    # user signed in and 403'd on every call with nothing to click.
    # Detecting the mismatch is a local base64 decode, so this costs nothing
    # on the normal path.
    if session.email_verified and not _token_claims(session.id_token).get("email_verified"):
        try:
            session = refresh(session)
            st.session_state[SESSION_KEY] = session
        except AuthError:
            pass

    return session


def sign_out() -> None:
    st.session_state.pop(SESSION_KEY, None)


def auth_headers() -> dict[str, str]:
    """Headers for a backend call. Empty when auth is off."""
    session = current_session()
    if session:
        return {"Authorization": f"Bearer {session.id_token}"}
    return {}


def is_authenticated() -> bool:
    return not AUTH_ENABLED or current_session() is not None


# ------------------------------------------------------------------- UI


def require_auth() -> AuthSession | None:
    """Gate a page. Call this as the first statement in every page file.

    Streamlit's multipage nav lists every page regardless of state, so a
    signed-out user can click straight into any of them. A gate in `app.py`
    alone would protect nothing; it has to be per-page.

    Renders the sign-in form and halts the script when not authenticated.
    """
    if not AUTH_ENABLED:
        st.sidebar.info("🔓 Auth disabled (dev mode)")
        return None

    session = current_session()
    if session is None:
        _render_login()
        st.stop()

    if not session.email_verified:
        _render_verification_required(session)
        st.stop()

    _render_sidebar_user(session)
    return session


def _render_sidebar_user(session: AuthSession) -> None:
    with st.sidebar:
        st.markdown("---")
        st.caption("Signed in as")
        st.write(f"**{session.display_name or session.email}**")
        st.caption(f"Session expires in {session.expires_in_minutes} min")
        if st.button("Sign out", use_container_width=True):
            sign_out()
            st.rerun()


def _render_verification_required(session: AuthSession) -> None:
    st.warning(f"Verify your email address to continue: **{session.email}**")
    st.caption(
        "The backend rejects unverified accounts — without verification anyone "
        "could claim any address, which would make the domain allow-list "
        "meaningless."
    )
    col1, col2 = st.columns(2)
    if col1.button("Resend verification email", use_container_width=True):
        try:
            send_verification_email(session.id_token)
            st.success("Sent. Check your inbox, then press *I've verified*.")
        except AuthError as exc:
            st.error(str(exc))
    if col2.button("I've verified — check again", type="primary", use_container_width=True):
        # Must re-mint the token, not just re-read the account: the existing
        # token still claims email_verified=false, and the backend trusts the
        # claim rather than making its own lookup.
        session = _sync_verification(session)
        st.session_state[SESSION_KEY] = session
        if session.email_verified:
            st.rerun()
        else:
            st.error("Still unverified. Click the link in the email first.")
    if st.button("Sign out"):
        sign_out()
        st.rerun()


def _render_login() -> None:
    st.title("🔐 Sign in")

    if not WEB_API_KEY:
        st.error(
            "**FIREBASE_WEB_API_KEY is not set.**\n\n"
            "Firebase console → ⚙️ Project settings → General → **Web API Key**, "
            "then add it to `.env` as `FIREBASE_WEB_API_KEY=...` and restart.\n\n"
            "This key is not a secret — it identifies the project and is visible "
            "in any browser that talks to Firebase."
        )
        st.stop()

    if ALLOWED_DOMAINS:
        st.caption(f"Restricted to: {', '.join(ALLOWED_DOMAINS)}")

    tab_in, tab_up, tab_reset = st.tabs(["Sign in", "Create account", "Reset password"])

    with tab_in:
        with st.form("sign_in"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Sign in", type="primary", use_container_width=True):
                try:
                    st.session_state[SESSION_KEY] = sign_in(email.strip(), password)
                    st.rerun()
                except AuthError as exc:
                    st.error(str(exc))

    with tab_up:
        with st.form("sign_up"):
            email = st.text_input("Email", key="su_email")
            password = st.text_input("Password (min 6 characters)", type="password", key="su_pw")
            confirm = st.text_input("Confirm password", type="password", key="su_pw2")
            if st.form_submit_button("Create account", use_container_width=True):
                if password != confirm:
                    st.error("Passwords do not match.")
                else:
                    try:
                        st.session_state[SESSION_KEY] = sign_up(email.strip(), password)
                        st.success("Account created — check your email for the verification link.")
                        st.rerun()
                    except AuthError as exc:
                        st.error(str(exc))
        st.caption(
            "New accounts start at the **viewer** role (public documents only). "
            "Authenticating proves who you are, not what you may read — an "
            "administrator grants higher clearance separately."
        )

    with tab_reset:
        with st.form("reset"):
            email = st.text_input("Email", key="reset_email")
            if st.form_submit_button("Send reset link", use_container_width=True):
                try:
                    send_password_reset(email.strip())
                    st.success("If that account exists, a reset link is on its way.")
                except AuthError as exc:
                    st.error(str(exc))
