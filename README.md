# OIDC demo apps

Two local apps that together demonstrate Hawcx's OIDC `/authorize` flow end-to-end:

- **`hawcx_oidc_demo_rp/`** — "Acme Notes" (Flask + Authlib relying party). The customer app.
- **`hawcx_oidc_login_ui/`** — hosted login UI (Vite + React, drives `@hawcx/react`). The thin UI users get bounced to.

Both run on your laptop and talk to the deployed Hawcx OP at **`https://dev-demo-api.hawcx.com`**. You don't deploy any Hawcx infrastructure — it's already on `hx-dev-demo-eks`.

## The flow (5 redirects, 3 hosts, 1 browser)

```
1. User at Acme Notes (localhost:5555)
   clicks "Sign in with Hawcx →"
        │
        ▼  302
2. Hawcx OP /authorize (dev-demo-api.hawcx.com)
   parks op_session in Redis, validates redirect_uri allowlist
        │
        ▼  302 to LOGIN_URL = http://localhost:5173/
3. Thin UI (localhost:5173)
   <HawcxSignUpSignIn> runs the full Hawcx flow: email → OTP → ZKP enrollment
   On completion, hx_auth → mint-code → binds auth code to op_session
        │
        ▼  window.location = /authorize/resume
4. Hawcx OP /authorize/resume (dev-demo-api.hawcx.com)
   reads bound code, atomic GET+DEL on op_session
        │
        ▼  302 to redirect_uri ?code=...&state=...
5. Acme Notes /callback (localhost:5555)
   POST /oauth2/token → verify id_token via JWKS → render "Welcome back"
```

## Prerequisites

- **Node 20+** (Vite, Playwright)
- **Python 3.11+** (Flask)
- **A Hawcx project on dev-demo** — see [Hawcx project setup](#hawcx-project-setup) below
- **The Hawcx SDK source** from `hawcx_web_demo` (`OIDC` branch) cloned as a sibling — see [SDK setup](#sdk-setup)

### Required directory layout

`package.json` references the SDK via relative file path, so all three must be siblings:

```
parent/
├── OIDC-demo-apps/                  ← this repo
│   ├── hawcx_oidc_demo_rp/
│   └── hawcx_oidc_login_ui/
└── hawcx_web_demo/                  ← SDK source
    └── frontend/sdk/
        ├── core/
        └── react/
```

## SDK setup

The thin UI consumes the Hawcx SDK from a sibling directory:

```bash
cd <parent dir of this repo>
git clone git@github.com:hawcx/hawcx_web_demo.git
cd hawcx_web_demo
git checkout OIDC
```

The `OIDC` branch contains commit `c46ae3d feat(sdk): forward opSessionId from AuthConfig into start action body` — without this, the SDK can't thread `op_session_id` into hx_auth and the bind step fails.

> **Note for collaborators:** if `origin/OIDC` doesn't exist in `hawcx_web_demo` yet, ask Karthik to push it (`git push origin OIDC`).

## Hawcx project setup

Create a project on dev-demo (via admin console) or use an existing one. The project's `oauth_config` must contain:

| Field | Value | How it gets set |
|---|---|---|
| `redirect_uris` | `["http://localhost:5555/callback"]` | Admin console → Project → Redirect URIs editor |
| `audience` | the project's own UUID | Auto-set for projects created via admin console (matches `client_id`). Legacy projects may need a manual PATCH — ask Karthik. |
| `issuer` | `https://dev-demo-api.hawcx.com` | Auto-set from the `OAUTH_ISSUER` env var at provisioning time |
| `kms_key_arn` | (auto) | Auto-set at create time |
| `id_token_ttl_seconds` | `3600` | Default; adjust as you like |

In `flow_configurations.signin` (for a demo that exercises ZKP):

- `device_enrollment: "required"` — forces Ed25519 device enrollment during the flow
- `primary_methods: ["email_otp"]` — start with email OTP

After the project exists, note these two values — you'll paste them into `.env`:

- **`client_id`** — the project UUID (also the `aud` in id_tokens)
- **`config_id`** — the Kong `x-config-id` (per-project SDK API key)

## App setup

### 1. Thin login UI

```bash
cd hawcx_oidc_login_ui
npm install --registry https://registry.npmjs.org/   # bypass private registry
npx playwright install chromium                        # for the e2e test
```

`--registry https://registry.npmjs.org/` is needed because `npm`/`pip` are often configured for the company CodeArtifact registry which won't have public packages like Playwright.

### 2. Demo RP (Acme Notes)

```bash
cd hawcx_oidc_demo_rp
python3 -m venv .venv
source .venv/bin/activate
pip install --index-url https://pypi.org/simple/ -r requirements.txt
cp .env.example .env
```

Edit `.env`:

```bash
HAWCX_OIDC_ISSUER=https://dev-demo-api.hawcx.com
HAWCX_CLIENT_ID=<your project UUID>
HAWCX_CONFIG_ID=<your project's x-config-id>
REDIRECT_URI=http://localhost:5555/callback
PORT=5555
# HAWCX_SDK_CONFIG_ID defaults to HAWCX_CONFIG_ID — leave unset
# FLASK_SECRET defaults to a fresh random key per restart — leave unset
```

## Running

### Option A — automated e2e test (headed Chromium, watchable)

```bash
cd hawcx_oidc_login_ui
npm run test:e2e
```

This auto-spawns both apps (Flask :5555 + Vite :5173), opens Chromium with `slowMo: 250ms`, and drives the full flow. Test uses a fresh email each run (`e2e-pw-<timestamp>@hawcx.com`) so it always hits the new-user → ZKP-enrollment path.

The test intercepts the `/v1/auth` response to read `step.debug.otp_code` — no inbox needed. Works because dev-demo runs `hx_auth` with `ENABLE_DEBUG_ENDPOINTS=true`.

Watch the trace if you want to see exactly what happened:
```bash
npx playwright show-trace test-results/oidc-flow-OIDC-code-flow-with-ZKP-device-enrollment-chromium/trace.zip
```

### Option B — two terminals, drive it yourself

Terminal 1:
```bash
cd hawcx_oidc_demo_rp && source .venv/bin/activate
python app.py                  # http://localhost:5555
```

Terminal 2:
```bash
cd hawcx_oidc_login_ui && npm run dev    # http://localhost:5173
```

Open <http://localhost:5555> in a browser. Click "Sign in with Hawcx →". Complete the email + OTP flow. You'll land back on Acme Notes with a verified id_token rendered.

## Constraints to know about

- **Laptop-only.** The dev-demo OP's `LOGIN_URL` is currently set to `http://localhost:5173/`, so `/authorize` 302s the browser to your local machine. Only someone running the thin UI locally on port 5173 will see anything. Not deployable as-is for remote users without flipping `LOGIN_URL`.
- **Exact ports required.** RP must be on `5555` (matches the registered `redirect_uri`); thin UI must be on `5173` (matches the OP's `LOGIN_URL`).
- **dev-demo `risk-demo-3` already has all the right config** (`audience` set, redirect_uri allowlisted). New projects get `audience` auto-populated — legacy projects without it will hit a 500 at `/oauth2/token` ("Tenant OAuth not configured").

## Links

- **This repo:** https://github.com/Karthik-hawcx/OIDC-demo-apps
- **Hawcx OP (dev-demo):** https://dev-demo-api.hawcx.com
  - Discovery: <https://dev-demo-api.hawcx.com/.well-known/openid-configuration>
  - JWKS: <https://dev-demo-api.hawcx.com/.well-known/jwks.json>
- **Source repos / branches that power this demo (all server-side, already deployed):**
  - `hawcx/hawcx_core_oauth` @ `OIDC` — `/authorize`, `/authorize/resume`, mint-code op_session binding
  - `hawcx/hx_auth` @ `OIDC` — threads `op_session_id` through to mint-code
  - `hawcx/hx_tenant_config` @ `OIDC` — stores per-project `oauth_config` (redirect_uris, audience, issuer)
  - `hawcx/hawcx_admin_console` @ `OIDC` — UI to manage `redirect_uris` per project
  - `hawcx/hawcx_web_demo` @ `OIDC` — Hawcx SDK source (`@hawcx/core`, `@hawcx/react` with `AuthConfig.opSessionId`)

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `npm install` fails resolving `@hawcx/core` or `@hawcx/react` | `hawcx_web_demo` not cloned as a sibling, or not on `OIDC` branch | Clone `hawcx_web_demo` next to `OIDC-demo-apps`, `git checkout OIDC` |
| OP returns HTML `invalid_request — redirect_uri is not registered` | RP's `redirect_uri` isn't in the project's allowlist | Admin console → your project → add `http://localhost:5555/callback` to redirect URIs |
| `/callback` returns 400 `Token endpoint returned 500: Tenant OAuth not configured` | `oauth_config.audience` (or `issuer`) missing on the project | Easiest: create a fresh project (admin console auto-sets audience). Or ask Karthik to PATCH the existing project's `oauth_config.audience` to the project UUID. |
| OP 302s to `localhost:5173` but nothing loads | Thin UI not running | `cd hawcx_oidc_login_ui && npm run dev` |
| Playwright test runs but times out at the OTP step | dev-demo `hx_auth` doesn't have debug endpoints enabled, so `step.debug.otp_code` is absent | Have Karthik flip `ENABLE_DEBUG_ENDPOINTS=true` on dev-demo `hx_auth`, or run the flow manually and read your inbox |

## What this demonstrates

That a stock OIDC relying party — using nothing more than [Authlib](https://docs.authlib.org/) — can sign users in through Hawcx with **passwordless device-bound credentials (ZKP Ed25519)** transparently. The RP code in `hawcx_oidc_demo_rp/app.py` has no Hawcx-specific logic in the auth path; it speaks plain OIDC. All the Hawcx magic happens inside the hosted login UI (`hawcx_oidc_login_ui/`), which the RP never sees.
