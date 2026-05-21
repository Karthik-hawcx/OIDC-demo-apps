# hawcx_oidc_login_ui

Hosted login UI for Hawcx's OIDC `/authorize` hand-off.

Replaces the previous plain-HTML stub at `hawcx_core_oauth/lib/login_page.py`
with a real React app that consumes `@hawcx/react`'s
`HawcxSignUpSignIn`. The SDK drives the full Hawcx auth flow —
including ZKP Ed25519 device enrollment, MFA, and OTP — so this UI works
against the same project policies that the existing SDK customers use.

## Flow

```
RP                  OP (hawcx_core_oauth)             this app
─────────────────   ───────────────────────────────   ─────────────────────
GET /authorize  ─>  validate, park op_session
                    302 /login?op=…&client_id=…
                    &sdk_config_id=…           ─────>  index.html boots
                                                       App reads URL params
                                                       <HawcxProvider opSessionId=op …>
                                                         <HawcxSignUpSignIn>
                                                           POST /v1/auth (apiKey, opSessionId, …)
                                                       hx_auth runs the flow,
                                                       on completion calls
                                                       /internal/mint-code
                                                       with opSessionId — OP
                                                       binds code to op_session.
                                                       onSuccess() ─> window.location =
                                                         /authorize/resume?op=…&client_id=…
                    302 to RP's redirect_uri
                    ?code=…&state=…           <───
```

## Setup

```bash
cd hawcx_oidc_login_ui
npm install --registry https://registry.npmjs.org/
# (your pip/npm may default to the company CodeArtifact registry —
#  use --registry npmjs.org for the public packages)
```

## Run (dev)

```bash
npm run dev
# Opens http://localhost:5173. Pass the same query params /authorize
# emits, e.g.:
# http://localhost:5173/?op=<op_session_id>&client_id=<project_uuid>&sdk_config_id=<config_id>
```

`vite.config.ts` proxies `/v1`, `/authorize`, and `/.well-known` to
`VITE_PROXY_TARGET` (default `https://dev-demo-api.hawcx.com`), so the
SDK's `/v1/auth` calls and final `/authorize/resume` redirect both reach
the real OP from your laptop.

## Build (production)

```bash
npm run build
# Emits dist/ with base path /login. hawcx_core_oauth mounts dist/
# at /login so when the OP redirects /authorize -> /login?op=…, the
# Vite-built index.html + assets load correctly.
```

## Deploy

Built `dist/` is consumed by `hawcx_core_oauth` — see
`hawcx_core_oauth/lib/login_page.py` (or its replacement) for the
StaticFiles mount.

## What the OP needs to provide

The SDK calls `/v1/auth` with the `x-config-id` header derived from
`sdk_config_id` (the URL param). `hx_auth` returns Protocol v2 steps the
React component renders directly. On `completed`, the OP has *already*
bound the code to the op_session, so this app just navigates to
`/authorize/resume` — no further OP calls.

## End-to-end test (headed)

Drives the full flow against the live dev-demo OP — RP redirect →
`/authorize` → this app → `@hawcx/react` SDK → ZKP Ed25519 enrollment →
`/authorize/resume` → RP callback → id_token verification. The dev-demo
project is configured with `device_enrollment: required`, so the test
passing means ZKP works end-to-end through this UI.

```bash
# First-run setup (already done if you ran `npm install` above):
npx playwright install chromium

# Run it — defaults to headed mode + slowMo so you can watch
npm run test:e2e

# Or step through the test in the Playwright UI:
npm run test:e2e:ui
```

The Playwright config auto-spawns:
- The demo RP (`../hawcx_oidc_demo_rp/app.py`) on `:5555`
- This app's Vite dev server on `:5173`

Both must be free, and the demo RP's `.venv` must exist (created by
`pip install -r requirements.txt` per its own README).

OTP retrieval works because dev-demo runs `hx_auth` with debug endpoints
enabled — `/v1/auth` responses include `step.debug.otp_code`. The test
intercepts the response and reads it programmatically. No inbox involved.

## SDK dependency

Uses local-path `file:` references to `../hawcx_web_demo/frontend/sdk/core`
and `../hawcx_web_demo/frontend/sdk/react`. Bumps require nothing —
running `npm install` re-resolves. To publish this app standalone, swap
those `file:` deps for the published `@hawcx/core` / `@hawcx/react`
versions (≥ the version that has `AuthConfig.opSessionId`).
