# Authentication (Firebase)

Closes risk **R-G03**. Before this, the platform had real authorization —
clearance filtering, role gates, audit attribution — resting on an unverified
`X-User-Id` header. Every control was only as strong as the network in front
of the API.

## Why Firebase

For a system like this one it is the right trade. Google and email sign-in,
password reset, email verification and JWT issuance are all things that are
weeks of security-sensitive work to write and a permanent liability to
maintain. Delegating them shrinks the backend's job to *verify a signed
token*, which is small and well-defined.

The obvious counter-argument — vendor lock-in — is weak here: the coupling is
confined to `src/auth/firebase.py` and a `Principal`. Swapping to Auth0,
Cognito or a self-hosted OIDC provider means one new verifier class.

## Flow

```
Browser / Streamlit
   │  Firebase SDK sign-in (Google, email+password)
   ▼
Firebase issues an ID token   (JWT, RS256, ~1h lifetime)
   │  Authorization: Bearer <token>
   ▼
FastAPI  →  resolve_principal()
   │        ├─ verify signature against Google's rotating public keys
   │        ├─ verify expiry, issuer, audience
   │        ├─ enforce email verification
   │        └─ enforce domain allow-list
   ▼
provision_user()  →  find or create the local user row
   │
   ▼
Principal(user_id, role, clearance, auth_provider="firebase")
   │
   ├─ RBAC gates on mutating routes
   ├─ clearance bounds which classifications retrieval may return
   └─ audit entries attribute actions to a real person
```

Verification is delegated to `firebase_admin.auth.verify_id_token` rather
than hand-rolled with a JWT library. That call handles signature, expiry,
issuer, audience **and key rotation** — the last being the part hand-rolled
implementations reliably get wrong.

## Two decisions worth understanding

**New users get the least-privileged role.** Authenticating proves *who
someone is*; it says nothing about *what they should read*. Conflating the
two is how "sign in with Google" quietly becomes "anyone with a Google
account is an analyst". Promotion is a separate, audited admin action, and an
existing user's role is never overwritten by a login — so a promotion is not
silently undone the next morning.

**A domain allow-list is usually required.** Without
`FIREBASE_ALLOWED_DOMAINS`, "sign in with Google" means literally anyone with
a Google account. For an internal corpus that is almost never the intent.
Unverified email addresses are rejected for the same reason: without
verification, anyone can claim any address, which makes the domain allow-list
meaningless.

## Setup

### 1. Firebase console

Create a project → Authentication → enable **Google** and/or
**Email/Password** → Project settings → Service accounts → *Generate new
private key*.

### 2. Backend

Save the JSON to `config/firebase-service-account.json` (git-ignored):

```env
FIREBASE_ENABLED=true
FIREBASE_SERVICE_ACCOUNT=config/firebase-service-account.json
FIREBASE_REQUIRE_AUTH=true
FIREBASE_ALLOWED_DOMAINS=yourcompany.com
FIREBASE_DEFAULT_ROLE=viewer
```

On platforms that offer only environment variables, supply the credential as
three fields instead:

```env
FIREBASE_PROJECT_ID=your-project-id
FIREBASE_CLIENT_EMAIL=firebase-adminsdk-xxxxx@your-project.iam.gserviceaccount.com
FIREBASE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\nMIIEv...\n-----END PRIVATE KEY-----\n"
```

The `\n` escapes are required — environment variables cannot carry literal
newlines and the raw PEM would be rejected as malformed. The backend
un-escapes them.

```bash
uv add firebase-admin
make migrate          # 0003 adds users.firebase_uid
```

### 3. Frontend

```env
VITE_FIREBASE_API_KEY=
VITE_FIREBASE_AUTH_DOMAIN=
VITE_FIREBASE_PROJECT_ID=
VITE_FIREBASE_STORAGE_BUCKET=
VITE_FIREBASE_MESSAGING_SENDER_ID=
VITE_FIREBASE_APP_ID=
```

```js
const token = await auth.currentUser.getIdToken();
await fetch("/api/v1/chat", {
  method: "POST",
  headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
  body: JSON.stringify({ query }),
});
```

Refresh the token before each request — `getIdToken()` handles that itself,
so call it per request rather than caching the string.

## Frontend keys are not secrets

`VITE_FIREBASE_*` values identify a project. They are visible in any
browser's network tab and authorise nothing on their own. What protects your
data is Firebase Security Rules plus this backend's token verification — not
the secrecy of that key.

They still belong in the frontend's `.env` rather than hardcoded, for the
ordinary reason that a project id should not require a code change.

**The service account is entirely different.** Its private key signs and
validates tokens for every user; anyone holding it can mint a token for any
identity. Treat it as a password to the whole project.

## Roles and clearance

| Role | Clearance | Can |
|---|---|---|
| `viewer` | public | ask questions, read public documents |
| `analyst` | internal | upload, run evaluations |
| `steward` | confidential | delete, reclassify, promote feedback, read audit |
| `admin` | restricted | settings, kill switches, retention |

Clearance is derived from role by policy (`GOVERNANCE_ROLE_CLEARANCE`), not
stored per user — so re-classifying what a role may read is one config change
and one audit entry, rather than a migration across every user row.

Promote a user by updating `users.role` (there is no self-service path, by
design).

## Development mode

With `FIREBASE_ENABLED=false`, identity comes from the `X-User-Id` header and
proves nothing. `Principal.auth_provider` records `"none"` in that case, so
audit entries from development are distinguishable from verified attribution
rather than being mistaken for it.

**Any deployment reachable by anyone untrusted must set
`FIREBASE_ENABLED=true`.**

## If a credential leaks

1. Firebase console → Service accounts → revoke the key immediately.
2. Generate a replacement and deploy it.
3. `firebase_admin.auth.revoke_refresh_tokens(uid)` for affected users.
4. Rotating the key does not rewrite git history — if it was committed, the
   history needs purging too.

## What is still not done

- **No token revocation check per request.** `check_revoked=False` avoids a
  Firebase round-trip on every call; with a ~1h token lifetime the exposure
  window after a revocation is bounded by that lifetime.
- **No rate limiting per identity.** The settings exist (`RATE_LIMIT_*`) but
  nothing reads them.
- **Conversations are not clearance-filtered.** A message may quote
  `confidential` passages while the message row carries no classification of
  its own.
