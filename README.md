# OIDC demo apps

Two local apps that demonstrate Hawcx's OIDC `/authorize` flow end-to-end:

- **`hawcx_oidc_demo_rp/`** — "Acme Notes" (Flask + Authlib relying party). The customer app.
- **`hawcx_oidc_login_ui/`** — hosted login UI (Vite + React, drives `@hawcx/react`). The thin UI users get bounced to during sign-in.

Both run on your laptop and talk to the deployed Hawcx OP at **`https://dev-demo-api.hawcx.com`**. You don't deploy any Hawcx infrastructure — it's already on `hx-dev-demo-eks`.

---

## The flow (what your browser does)

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
   <HawcxSignUpSignIn> runs: email → OTP → ZKP device enrollment
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

---

## Quick start (use the existing demo project)

If you just want to see the demo work — fastest path, ~5 minutes:

```bash
# 1. Clone everything into one parent dir (paths must be siblings)
mkdir hawcx-oidc-demo && cd hawcx-oidc-demo
git clone https://github.com/Karthik-hawcx/OIDC-demo-apps.git
git clone https://github.com/hawcx/hawcx_web_demo.git
cd hawcx_web_demo && git checkout OIDC && cd ..

# 2. Install thin UI
cd OIDC-demo-apps/hawcx_oidc_login_ui
npm install --registry https://registry.npmjs.org/
npx playwright install chromium

# 3. Install demo RP
cd ../hawcx_oidc_demo_rp
python3 -m venv .venv
source .venv/bin/activate
pip install --index-url https://pypi.org/simple/ -r requirements.txt

# 4. Configure RP — use the existing risk-demo-3 project on dev-demo
cat > .env <<'EOF'
HAWCX_OIDC_ISSUER=https://dev-demo-api.hawcx.com
HAWCX_CLIENT_ID=2551b799-00c8-470a-b8c2-1b5f1f5fa8b0
HAWCX_CONFIG_ID=aRHegUPgVnqxUagHes14bi0FnlrN3U2v
REDIRECT_URI=http://localhost:5555/callback
PORT=5555
EOF

# 5. Run the headed e2e test (auto-spawns both apps)
cd ../hawcx_oidc_login_ui
npm run test:e2e
```

A Chromium window opens, drives the full flow, lands on Acme Notes with a verified id_token. ~10 seconds with slowMo.

The values above point at **risk-demo-3** on dev-demo, which already has the required config (audience set, `http://localhost:5555/callback` in the redirect allowlist, `device_enrollment: required` so ZKP runs). Everyone using these values shares the same project — fine for demos, not for production.

If you want your own project, jump to [Setting up your own Hawcx project](#setting-up-your-own-hawcx-project).

---

## Prerequisites

- **Node 20+** (for Vite + Playwright)
- **Python 3.11+** (for Flask)
- **Git** with access to both `Karthik-hawcx/OIDC-demo-apps` and `hawcx/hawcx_web_demo`
- (Optional, for own-project mode) a Hawcx account that can log into `https://admin-console-dev-demo.hawcx.com`

### Required directory layout

The thin UI's `package.json` references the Hawcx SDK via `file:../hawcx_web_demo/frontend/sdk/{core,react}`. All three repos must be siblings:

```
hawcx-oidc-demo/                     ← any parent dir
├── OIDC-demo-apps/                  ← this repo
│   ├── hawcx_oidc_demo_rp/
│   └── hawcx_oidc_login_ui/
└── hawcx_web_demo/                  ← SDK source on branch OIDC
    └── frontend/sdk/
        ├── core/
        └── react/
```

If the layout is off, `npm install` will fail resolving `@hawcx/core` / `@hawcx/react`.

---

## Setting up your own Hawcx project

Skip this if you used the quick-start values. Do it if you want a project under your own org.

### 1. Create the project

1. Open the dev-demo admin console: **<https://admin-console-dev-demo.hawcx.com>**
2. Sign in (uses Hawcx auth — you'll go through the same email-OTP flow as a regular user)
3. Navigate to your customer/org → **Create project** (or open an existing one)
4. Note these two values from the project's detail page:
   - **Project ID** (a UUID like `2551b799-...`) — this is your `client_id`
   - **Config ID** (a short alphanumeric token like `aRHegUPg...`) — this is your `config_id` / Kong `x-config-id`

### 2. Add the redirect URI

The OP exact-string matches `redirect_uri` at `/authorize` time (RFC 6749 §3.1.2 — no normalization, no trailing-slash tolerance). The local RP's callback URL must be on the allowlist or the flow fails with `invalid_request — redirect_uri is not registered for this client`.

1. On the project detail page, find the **Redirect URIs** editor (textarea labeled something like "OIDC redirect URIs")
2. Add a new line containing exactly:
   ```
   http://localhost:5555/callback
   ```
3. Click **Save**. The "Saved" toast confirms the PATCH succeeded.

### 3. Verify the project's `oauth_config`

Newly-created projects auto-populate `audience`, `issuer`, and `kms_key_arn`. **Legacy projects (provisioned before audience auto-population shipped) may be missing `audience`** — the flow will then fail at `/oauth2/token` with `500 Tenant OAuth not configured`. If you hit that, ask Karthik to PATCH `oauth_config.audience` to your project's UUID (the same value as `client_id`).

### 4. (Recommended) Enable ZKP enrollment

In the project's flow configuration:
- `flow_configurations.signin.device_enrollment: "required"` — exercises Ed25519 device enrollment in the demo
- `flow_configurations.signin.primary_methods: ["email_otp"]` — start with email OTP before enrollment

---

## App setup (detailed)

### Step 1: Clone everything

```bash
mkdir hawcx-oidc-demo && cd hawcx-oidc-demo
git clone https://github.com/Karthik-hawcx/OIDC-demo-apps.git
git clone https://github.com/hawcx/hawcx_web_demo.git
cd hawcx_web_demo && git checkout OIDC && cd ..
```

The `OIDC` branch of `hawcx_web_demo` contains commit `c46ae3d feat(sdk): forward opSessionId from AuthConfig into start action body` — without this, the SDK can't thread `op_session_id` through, and the OP can't bind the auth code to the parked `/authorize` session.

> **Note for collaborators:** if `origin/OIDC` doesn't exist on `hawcx_web_demo` yet, ask Karthik to push it (`git push origin OIDC`).

### Step 2: Install the thin login UI

```bash
cd OIDC-demo-apps/hawcx_oidc_login_ui
npm install --registry https://registry.npmjs.org/
npx playwright install chromium
```

`--registry https://registry.npmjs.org/` bypasses the company's private CodeArtifact registry, which won't have Playwright or other public packages.

### Step 3: Install the demo RP

```bash
cd ../hawcx_oidc_demo_rp
python3 -m venv .venv
source .venv/bin/activate
pip install --index-url https://pypi.org/simple/ -r requirements.txt
```

Same registry-override reason — `--index-url https://pypi.org/simple/` ensures Authlib comes from public PyPI.

### Step 4: Configure `.env`

```bash
cp .env.example .env
```

Edit `.env`. Either use the shared demo project (recommended for a quick start):

```bash
HAWCX_OIDC_ISSUER=https://dev-demo-api.hawcx.com
HAWCX_CLIENT_ID=2551b799-00c8-470a-b8c2-1b5f1f5fa8b0
HAWCX_CONFIG_ID=aRHegUPgVnqxUagHes14bi0FnlrN3U2v
REDIRECT_URI=http://localhost:5555/callback
PORT=5555
```

Or use your own project's values from [Setting up your own Hawcx project](#setting-up-your-own-hawcx-project):

```bash
HAWCX_OIDC_ISSUER=https://dev-demo-api.hawcx.com
HAWCX_CLIENT_ID=<your project UUID>
HAWCX_CONFIG_ID=<your project's x-config-id>
REDIRECT_URI=http://localhost:5555/callback
PORT=5555
```

`HAWCX_SDK_CONFIG_ID` defaults to `HAWCX_CONFIG_ID` (the same project drives both the OIDC flow and the SDK's `/v1/auth` calls) — leave it unset. `FLASK_SECRET` defaults to a fresh random key per restart — leave it unset for the demo.

---

## Running the demo

### Option A — automated headed e2e test

The fastest way to see it work:

```bash
cd hawcx_oidc_login_ui
npm run test:e2e
```

What happens:
- Playwright auto-spawns the Flask RP on `:5555` and Vite dev server on `:5173`
- Opens a Chromium window (slowMo 250ms — watchable)
- Drives email + OTP + ZKP enrollment, asserts `id_token verified` lands on Acme Notes
- Test uses a fresh email each run (`e2e-pw-<timestamp>@hawcx.com`)
- Test reads OTPs from `step.debug.otp_code` in the `/v1/auth` response (no inbox needed; works because dev-demo's `hx_auth` has `ENABLE_DEBUG_ENDPOINTS=true`)

If it passes (`1 passed`), the whole stack works. Trace artifacts live in `test-results/oidc-flow-OIDC-code-flow-with-ZKP-device-enrollment-chromium/`:

```bash
npx playwright show-trace test-results/oidc-flow-OIDC-code-flow-with-ZKP-device-enrollment-chromium/trace.zip
```

### Option B — drive it yourself in two terminals

**Terminal 1 — demo RP:**
```bash
cd hawcx_oidc_demo_rp && source .venv/bin/activate
python app.py
# Acme Notes serving on http://localhost:5555
```

**Terminal 2 — thin login UI:**
```bash
cd hawcx_oidc_login_ui
npm run dev
# Vite dev server on http://localhost:5173/login/
```

Open <http://localhost:5555> in a browser.

### What you'll see (sample flow)

1. **`http://localhost:5555`** — Acme Notes landing page, hero "Your team's collective brain", green "Sign in with Hawcx →" button.
2. Click the button. Browser navigates to `https://dev-demo-api.hawcx.com/authorize?...` (you'll see it in the URL bar for an instant), then 302s to **`http://localhost:5173/?op=<43-char>&client_id=2551b799-...&sdk_config_id=aRHegUPg...`**.
3. The thin UI loads — small white card, "Sign in to continue", an email input, the op_session_id in the meta strip at the bottom.
4. Type an email (any address; new emails trigger the new-user path). Click Continue.
5. Email OTP step. If using the real email flow, check your inbox. If using `Option A` (automated test), the test reads the OTP directly from the `/v1/auth` debug response — your inbox doesn't get hit.
6. ZKP Ed25519 enrollment runs transparently — `@hawcx/core` generates a keypair via WebCrypto, does device attestation, stores credentials in IndexedDB. Brief spinner, no UI to interact with.
7. Browser jumps back to **`http://localhost:5555/callback?code=...&state=...`**, then to **`http://localhost:5555/`**.
8. You're signed in. Acme Notes shows a "Welcome back, <email>" dashboard with a panel marked "✓ id_token verified" and an expandable JSON dump of the id_token claims (sub, aud, exp, nonce, amr=["swk","mfa","otp"], auth_time, etc.).

### Repeat runs on the same browser

The SDK persists the device key in IndexedDB. If you sign in again with the **same email in the same browser profile**, you'll skip enrollment (the device is already trusted) and only need to enter the OTP. To force the new-user / new-device path again:

- Use a different email, **or**
- Open the browser's DevTools → Application → IndexedDB → delete the `hawcx` database, **or**
- Use a fresh incognito window

---

## Constraints to know about

- **Laptop-only.** The dev-demo OP's `LOGIN_URL` is set to `http://localhost:5173/`, so `/authorize` 302s the browser to your local machine. Only someone running the thin UI locally on port 5173 will see anything. Not deployable for remote users without flipping `LOGIN_URL` on the OP and standing up a real deployment of the thin UI.
- **Exact ports required.** RP must be on `5555` (matches the registered `redirect_uri`); thin UI must be on `5173` (matches the OP's `LOGIN_URL`).
- **Shared project caveat.** Anyone using the quick-start `client_id` / `config_id` (risk-demo-3) is signing in against the same Hawcx project — all device enrollments land in the same dev-demo tenant's database. Fine for ad-hoc demos, not for evaluation.

---

## Links

- **This repo:** <https://github.com/Karthik-hawcx/OIDC-demo-apps>
- **Hawcx OP (dev-demo):** <https://dev-demo-api.hawcx.com>
  - Discovery: <https://dev-demo-api.hawcx.com/.well-known/openid-configuration>
  - JWKS: <https://dev-demo-api.hawcx.com/.well-known/jwks.json>
- **Admin console (dev-demo):** <https://admin-console-dev-demo.hawcx.com>
- **Source repos / branches that power this demo (server-side, already deployed):**
  - `hawcx/hawcx_core_oauth` @ `OIDC` — `/authorize`, `/authorize/resume`, mint-code op_session binding
  - `hawcx/hx_auth` @ `OIDC` — threads `op_session_id` through to mint-code
  - `hawcx/hx_tenant_config` @ `OIDC` — stores per-project `oauth_config` (redirect_uris, audience, issuer)
  - `hawcx/hawcx_admin_console` @ `OIDC` — UI to manage `redirect_uris` per project
  - `hawcx/hawcx_web_demo` @ `OIDC` — Hawcx SDK source (`@hawcx/core`, `@hawcx/react` with `AuthConfig.opSessionId`)

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `npm install` fails resolving `@hawcx/core` / `@hawcx/react` | `hawcx_web_demo` not cloned as a sibling, or not on `OIDC` branch | Clone `hawcx_web_demo` next to `OIDC-demo-apps`, `git checkout OIDC` |
| `npm install` fails on Playwright / public packages with 401 | npm pointing at private CodeArtifact registry | Add `--registry https://registry.npmjs.org/` to the install command, or `npm config set registry https://registry.npmjs.org/` |
| OP returns HTML `invalid_request — redirect_uri is not registered for this client` | Your project's allowlist doesn't include `http://localhost:5555/callback` | Add it via the admin console: <https://admin-console-dev-demo.hawcx.com> → your project → Redirect URIs |
| `/callback` returns `400 Token endpoint returned 500: Tenant OAuth not configured` | Your project's `oauth_config.audience` is missing | Easiest: use a freshly-created project (admin console auto-sets audience). Or ask Karthik to PATCH the project's `oauth_config.audience` to its UUID. |
| Browser 302s to `localhost:5173` and shows "This site can't be reached" | Thin UI isn't running | `cd hawcx_oidc_login_ui && npm run dev` |
| OP discovery doc 404s | Network/DNS — `dev-demo-api.hawcx.com` not resolving | Verify reachability: `curl https://dev-demo-api.hawcx.com/.well-known/openid-configuration` |
| E2E test passes but you can't sign in manually | Manual mode hits real OTP delivery; check your inbox for the OTP email | Use a real email address you control |
| E2E test times out at the OTP step | `ENABLE_DEBUG_ENDPOINTS` not set on dev-demo `hx_auth` | Have Karthik flip the flag on dev-demo, or run the flow manually with a real inbox |

---

## What this demonstrates

A stock OIDC relying party — using nothing more than [Authlib](https://docs.authlib.org/) — can sign users in through Hawcx with **passwordless device-bound credentials (ZKP Ed25519)** transparently. The RP code in `hawcx_oidc_demo_rp/app.py` has no Hawcx-specific logic in the auth path; it speaks plain OIDC. All Hawcx-specific behavior happens inside the hosted login UI (`hawcx_oidc_login_ui/`), which the RP never has to integrate with.
