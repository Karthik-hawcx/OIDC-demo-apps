# Hawcx OIDC demo RP

A minimal Flask + [Authlib](https://docs.authlib.org/) relying party. Drives
Hawcx's OIDC `/authorize` flow end-to-end against a stock OIDC library so
you can demo "Hawcx works with any standards-compliant OIDC consumer."

```
┌───────────────────┐    1. /login                ┌─────────────────────┐
│   Demo RP app     │ ──redirects──> /authorize ──>   Hawcx OIDC OP    │
│  (Flask, port     │                              │  (dev-demo-api.    │
│   5000)           │ <─302+code─── /callback <──  │   hawcx.com)        │
│                   │                              │   • /authorize     │
│  2. POST          │ ──code+verifier──>           │   • /login (UI)    │
│     /oauth2/token │                              │   • /oauth2/token  │
│                   │ <──id_token (JWT)────        │   • /.well-known/* │
│                   │                              │                    │
│  3. verify JWT    │ ──fetch JWKS──>              │   /.well-known/    │
│     via JWKS      │                              │   jwks.json        │
│                   │ <─keys────                   │                    │
│  4. render claims │                              │                    │
└───────────────────┘                              └─────────────────────┘
```

## What this demo proves

1. **Hawcx's `/authorize` is callable from a stock OIDC RP library.** PKCE
   (S256), state, and nonce round-trip through. The `redirect_uri`
   allowlist enforces correctly when misconfigured.
2. **Hawcx's id_token verifies cleanly via JWKS** without Hawcx-specific
   tooling — Authlib's `jwt.decode` does the work end-to-end (signature,
   `iss`, `aud`, `exp`, `nonce`).
3. **The claim set lands in the right shapes:** RFC 8176 `amr`,
   `auth_time`, `email_verified`, `phone_number`, etc. — all the recent
   OIDC Core 1.0 hardening visible to a stock consumer.

## What this demo glosses over

Hawcx's `/oauth2/token` sits behind Kong's `key-auth` plugin and identifies
the tenant from the `x-config-id` header (Kong API key), **not** from a
`client_id` form field. A standards-compliant token endpoint would be
public and self-identify via `client_id`. The RP injects the header
explicitly — `app.py` has a comment marking the gap. If you fix that on
the Hawcx side, this RP becomes 100% spec-only by deleting one line.

## Setup

### 1. Install

```bash
cd hawcx_oidc_demo_rp
python3 -m venv .venv
source .venv/bin/activate
# Use public PyPI explicitly — `pip` is often configured for the
# company CodeArtifact registry which won't have Authlib.
pip install --index-url https://pypi.org/simple/ -r requirements.txt
```

Port choice: the demo defaults to **5555** because macOS AirPlay Receiver
claims 5000 and various other dev tools claim 5050. If you want a
different port, change `PORT` and `REDIRECT_URI` in `.env` *and* the
project's `redirect_uris` allowlist on the OP side.

### 2. Configure

```bash
cp .env.example .env
# Edit .env: HAWCX_CLIENT_ID, HAWCX_CONFIG_ID, etc.
```

You need a Hawcx project on the `dev-demo` environment with:
- `redirect_uris` allowlist containing `http://localhost:5555/callback`
- A Kong x-config-id (per-project SDK key)
- `device_enrollment` set to `disabled` in `flow_configurations.signin`
  (otherwise auth requires ZKP Ed25519 enrollment which this demo doesn't
  drive — same constraint as the hosted login UI for now)

The defaults in `.env.example` point at the `risk-demo-3` project on
`dev-demo` (UUID `2551b799-…`) which the admin console also uses for its
own auth. If you want to use a different project, update both `CLIENT_ID`
and `CONFIG_ID` in lockstep — they must come from the same project.

### 3. Allowlist the redirect URI

Use the OAuth tab in the Hawcx admin console
(`https://admin-console-dev-demo.hawcx.com` → project → Settings → OAuth)
or curl:

```bash
curl -X PATCH "https://dev-demo-api.hawcx.com/internal/tenant-config/projects/$HAWCX_CLIENT_ID" \
  -H "Content-Type: application/json" \
  -d '{"oauth_config": {"redirect_uris": ["http://localhost:5555/callback"]}}'
```

### 4. Disable device enrollment on that project (workaround)

The hosted login UI is plain-JS and can't drive ZKP Ed25519 device
enrollment yet, and the flow won't reach token exchange while it's
required. Disable it on the demo project:

```bash
curl -X PATCH "https://dev-demo-api.hawcx.com/internal/tenant-config/projects/$HAWCX_CLIENT_ID" \
  -H "Content-Type: application/json" \
  -d '{"flow_configurations": {"signin": {"primary_methods": ["email_otp"], "device_enrollment": "disabled", "enrollment_methods": [], "mfa_policy": "optional", "mfa_methods": [], "unknown_user": "create", "stepup_policy": "skip", "mfa_bypass_methods": [], "skip_mfa_on_trusted_device": false, "skip_primary_on_trusted_device": true}}}'
```

⚠️ `hawcx_tenant_client` in `hx_auth` pods caches `ProjectContext` for
up to 1 hour. To make the policy change take effect immediately:

```bash
aws eks update-kubeconfig --name hx-dev-demo-eks --region us-east-2
kubectl rollout restart deployment/hx-auth -n hx-auth
```

### 5. Run

```bash
python app.py
# Open http://localhost:5555
```

## What you should see

1. Landing page → click "Sign in with Hawcx"
2. Browser redirects to `https://dev-demo-api.hawcx.com/authorize?…`
3. OP redirects to its hosted login UI at `/login?op=…`
4. Enter email → enter the debug OTP shown on the page (dev-demo runs in
   debug mode and prints the OTP)
5. Browser bounces back to `http://localhost:5555/callback?code=…&state=…`
6. The RP exchanges the code at `/oauth2/token`, verifies the id_token
   against the JWKS, and renders the claims:

```
sub:            61ab0688-c4ff-44c1-a0b2-239e3f1a294b
aud:            2551b799-00c8-470a-b8c2-1b5f1f5fa8b0
iss:            https://dev-demo-api.hawcx.com
amr:            ["swk"]
acr:            "urn:hawcx:pwdless:v1"
auth_time:      1778…
email:          your-email@hawcx.com
email_verified: true
nonce:          (round-tripped)
```

If verification fails, the error page reports which check tripped
(signature, iss, aud, exp, or nonce).

## File layout

```
hawcx_oidc_demo_rp/
├── app.py             # All logic. Flask routes + Authlib + JWKS verification.
├── requirements.txt
├── .env.example       # Copy to .env and fill in.
├── .gitignore
└── README.md          # This file.
```

## Troubleshooting

| Symptom | Cause |
|---|---|
| `400 redirect_uri is not registered for this client` from `/authorize` | `redirect_uris` not allowlisted on the project (step 3). |
| OIDC flow gets stuck on `setup_device` after OTP | `device_enrollment: required` on the project + cache (step 4). |
| `Token endpoint returned 401: No API key found in request` | `HAWCX_CONFIG_ID` missing or wrong. |
| `id_token verification failed: invalid_token` (nonce mismatch) | Session cookie lost between `/login` and `/callback` (e.g. cross-domain redirect, browser blocking SameSite). Use a normal Chrome window, not incognito-with-cookies-blocked. |
| `iss` claim mismatch | `HAWCX_OIDC_ISSUER` has a trailing slash or wrong host. Must byte-match the discovery doc's `issuer`. |
