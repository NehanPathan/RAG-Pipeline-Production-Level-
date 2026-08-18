# `config/` — secret material, never committed

Everything in this directory except this file and `.gitkeep` is git-ignored
(see `.gitignore`). It exists so credentials have an obvious home that is
already excluded, rather than being dropped next to the code and noticed
during review — or not.

## Firebase service account

The backend verifies Firebase ID tokens using a service-account credential.

1. Firebase console → Project settings → **Service accounts** → *Generate new
   private key*.
2. Save the downloaded JSON here as `firebase-service-account.json`.
3. Point the backend at it:

   ```env
   FIREBASE_ENABLED=true
   FIREBASE_SERVICE_ACCOUNT=config/firebase-service-account.json
   ```

**Treat this file as a password to every account in the project.** Its
private key signs and validates tokens for all users; anyone holding it can
mint a token for any identity. It must never be committed, pasted into an
issue, or shared over chat.

## Alternative: environment variables

Container platforms and PaaS hosts often provide only environment variables.
The same credential can be supplied as three fields, which is what
`FirebaseVerifier._load_credentials()` falls back to when no file exists:

```env
FIREBASE_PROJECT_ID=your-project-id
FIREBASE_CLIENT_EMAIL=firebase-adminsdk-xxxxx@your-project.iam.gserviceaccount.com
FIREBASE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\nMIIEv...\n-----END PRIVATE KEY-----\n"
```

The `\n` escapes are required — environment variables cannot carry literal
newlines, and the raw PEM would be rejected as malformed. The backend
un-escapes them (`Settings.firebase_private_key_normalized`).

## Frontend keys are different

The `VITE_FIREBASE_*` values a browser client needs are **not secrets**.
Firebase web API keys identify a project; they do not authorise anything on
their own, and they are visible in any browser's network tab regardless. What
protects your data is Firebase Security Rules plus this backend's token
verification — not the secrecy of that key.

They still belong in the frontend's own `.env` rather than hardcoded, for the
ordinary reason that a project id should not require a code change.

## If a credential leaks

1. Firebase console → Service accounts → revoke the leaked key immediately.
2. Generate a replacement and deploy it.
3. `firebase_admin.auth.revoke_refresh_tokens(uid)` for affected users to
   invalidate sessions issued under the old key.
4. Rotating the key does not rewrite git history — if it was committed, the
   history needs purging too.
