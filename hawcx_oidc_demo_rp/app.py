"""Hawcx OIDC demo relying party.

A minimal Flask app that signs a user in via Hawcx's OIDC /authorize flow,
exchanges the auth code at /oauth2/token, verifies the id_token against
Hawcx's published JWKS, and renders the resulting claims.

What this demo proves:
    1. Hawcx's /authorize endpoint behaves like a standards-compliant OIDC OP
       to a stock RP library (authlib): PKCE works, state/nonce round-trip
       through, redirect_uri exact-match enforcement engages.
    2. The id_token from /oauth2/token verifies cleanly against the discovery
       document + JWKS — no Hawcx-specific tooling required to validate.
    3. Claims (sub, email, email_verified, amr, auth_time, etc.) arrive in
       the standards-mandated locations and shapes.

What's NOT standards-compliant (and the demo papers over):
    Hawcx's /oauth2/token sits behind Kong's key-auth plugin and identifies
    the tenant from the `x-config-id` header (Kong API key), NOT from a
    `client_id` form field. A stock OIDC library would never send that
    header, so the demo injects it explicitly. In a fully spec-compliant
    deployment the token endpoint would be public (no Kong key) and the
    client_id form field would identify the tenant.

Configuration: see .env.example. Run with `python app.py`.
"""

from __future__ import annotations

import os
import secrets
from typing import Any

import requests
from authlib.integrations.requests_client import OAuth2Session
from authlib.jose import JsonWebKey, jwt
from authlib.jose.errors import JoseError
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from dotenv import load_dotenv
from flask import Flask, redirect, render_template_string, request, session, url_for

load_dotenv()


def _required_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(
            f"Missing required env var: {name}. See .env.example."
        )
    return v


# --- Config ----------------------------------------------------------------
# OIDC_ISSUER must byte-match the `iss` claim in the id_token and the issuer
# field in the discovery doc. Trailing slashes matter — keep it clean.
OIDC_ISSUER = _required_env("HAWCX_OIDC_ISSUER").rstrip("/")
# CLIENT_ID is the Hawcx project UUID (the value Kong's consumer key resolves
# to under the hood). It's also the `aud` claim in the id_token.
CLIENT_ID = _required_env("HAWCX_CLIENT_ID")
# CONFIG_ID is the Kong x-config-id header value — a per-project SDK key.
# Used because Hawcx's /oauth2/token is behind key-auth (see module docstring).
CONFIG_ID = _required_env("HAWCX_CONFIG_ID")
REDIRECT_URI = os.environ.get("REDIRECT_URI", "http://localhost:5000/callback")
APP_PORT = int(os.environ.get("PORT", "5000"))
# SDK config id passed to /authorize as a query param so Hawcx's hosted login
# UI knows which Kong key its JS should use when calling /v1/auth. The OP
# forwards it to /login; if the OP later persists it on oauth_config, this
# becomes unnecessary. Defaults to the same CONFIG_ID — the demo uses one
# project for both the RP role and the login-UI's hx_auth calls.
SDK_CONFIG_ID = os.environ.get("HAWCX_SDK_CONFIG_ID", CONFIG_ID)


app = Flask(__name__)
# In production this would come from a secret manager. The demo regenerates
# on every restart, which logs out any existing sessions — fine for a demo.
app.secret_key = os.environ.get("FLASK_SECRET", secrets.token_urlsafe(32))


# --- One-shot discovery + JWKS load ----------------------------------------
# Fetched at startup to keep request paths fast. A production RP would:
#   - retry on transient failures here
#   - re-fetch JWKS on a JWT `kid` miss (key rotation)
# Both are out of scope for a demo.

_discovery = requests.get(
    f"{OIDC_ISSUER}/.well-known/openid-configuration", timeout=10
).json()
_jwks = JsonWebKey.import_key_set(
    requests.get(_discovery["jwks_uri"], timeout=10).json()
)

# Sanity-check at boot rather than at first sign-in.
assert (
    _discovery.get("authorization_endpoint")
    and _discovery.get("token_endpoint")
), "Discovery doc missing required endpoints — wrong issuer?"


# --- Routes ----------------------------------------------------------------


######################################################################
# Templates
#
# The visual style here is deliberately a "real product" — emerald
# accent, full-page product layout, branded header — distinct from the
# Hawcx login UI's blue-on-white centered card. Stops audiences during
# demos asking "wait, am I still on the same site?"
#
# Fictional brand: "Acme Notes" — a notes-taking SaaS. Familiar product
# category, makes a Welcome-back-here-are-your-notes dashboard obvious
# without needing real backing data.
######################################################################

# Shared CSS for both pages — defined once, injected via Jinja so the
# headers/footers stay byte-identical.
_SHARED_CSS = """
:root {
  --bg: #fafaf9;
  --surface: #ffffff;
  --border: #e7e5e4;
  --ink: #18181b;
  --ink-muted: #71717a;
  --ink-faint: #a1a1aa;
  --brand: #059669;
  --brand-dark: #047857;
  --brand-tint: #ecfdf5;
  --brand-ink: #064e3b;
  --danger-bg: #fef2f2;
  --danger-ink: #991b1b;
  --shadow-sm: 0 1px 2px rgba(0,0,0,.04);
  --shadow-md: 0 1px 3px rgba(0,0,0,.04), 0 4px 12px rgba(0,0,0,.04);
  --shadow-lg: 0 1px 3px rgba(0,0,0,.04), 0 24px 48px rgba(0,0,0,.06);
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--bg);
  color: var(--ink);
  font-size: 15px;
  line-height: 1.5;
  min-height: 100vh;
}
a { color: var(--brand-dark); text-decoration: none; }
a:hover { text-decoration: underline; }
code { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px; }

/* ── Top nav ── */
.nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 32px;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
}
.brand { display: flex; align-items: center; gap: 10px; font-weight: 600; font-size: 16px; }
.brand-mark {
  width: 28px; height: 28px;
  border-radius: 7px;
  background: linear-gradient(135deg, var(--brand) 0%, var(--brand-dark) 100%);
  color: #fff;
  display: inline-flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 14px;
}
.nav-right { display: flex; align-items: center; gap: 16px; }
.nav-link { color: var(--ink-muted); font-size: 14px; font-weight: 500; }
.nav-link:hover { color: var(--ink); text-decoration: none; }

/* ── Buttons ── */
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  padding: 9px 18px;
  border: none; border-radius: 8px;
  font-family: inherit; font-size: 14px; font-weight: 500;
  cursor: pointer; text-decoration: none;
  transition: background .15s, transform .05s;
}
.btn:hover { text-decoration: none; }
.btn:active { transform: translateY(1px); }
.btn-primary { background: var(--brand); color: #fff; }
.btn-primary:hover { background: var(--brand-dark); }
.btn-ghost { background: transparent; color: var(--ink); border: 1px solid var(--border); }
.btn-ghost:hover { background: var(--bg); color: var(--ink); }
.btn-lg { padding: 12px 24px; font-size: 15px; }

/* ── Generic surfaces ── */
.container { max-width: 1080px; margin: 0 auto; padding: 0 32px; }
.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: var(--shadow-sm);
}
.panel-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 18px 24px;
  border-bottom: 1px solid var(--border);
}
.panel-header h2 {
  margin: 0; font-size: 15px; font-weight: 600;
}
.panel-body { padding: 20px 24px; }

/* ── Hero (landing) ── */
.hero {
  padding: 96px 32px 64px;
  text-align: center;
}
.hero h1 {
  margin: 0 0 16px;
  font-size: 44px;
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.1;
}
.hero h1 .accent {
  background: linear-gradient(135deg, var(--brand) 0%, var(--brand-dark) 100%);
  -webkit-background-clip: text; background-clip: text;
  color: transparent;
}
.hero p {
  margin: 0 auto 32px;
  max-width: 540px;
  color: var(--ink-muted);
  font-size: 17px;
}
.hero .cta-row { display: flex; gap: 12px; justify-content: center; }

/* ── Features ── */
.features { padding: 0 32px 80px; }
.feature-grid {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px;
  max-width: 1080px; margin: 0 auto;
}
.feature {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 24px;
}
.feature-icon {
  width: 38px; height: 38px; border-radius: 8px;
  background: var(--brand-tint); color: var(--brand-dark);
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 18px; margin-bottom: 14px;
}
.feature h3 { margin: 0 0 6px; font-size: 15px; }
.feature p { margin: 0; color: var(--ink-muted); font-size: 13px; line-height: 1.5; }

/* ── Footer ── */
footer {
  text-align: center;
  padding: 32px;
  border-top: 1px solid var(--border);
  color: var(--ink-faint);
  font-size: 12px;
}
footer .secured { display: inline-flex; align-items: center; gap: 6px; }
footer .secured .lock { font-size: 11px; }

/* ── Dashboard ── */
.dashboard { padding: 32px 32px 80px; }
.dashboard h1 {
  margin: 0 0 4px; font-size: 24px; font-weight: 600; letter-spacing: -0.01em;
}
.dashboard .sub { margin: 0 0 28px; color: var(--ink-muted); font-size: 14px; }
.grid-2 { display: grid; grid-template-columns: 1fr 360px; gap: 20px; }
@media (max-width: 880px) { .grid-2 { grid-template-columns: 1fr; } }

/* ── User chip in nav ── */
.user-chip {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 6px 12px 6px 6px;
  border: 1px solid var(--border); border-radius: 999px;
  font-size: 13px; color: var(--ink);
}
.avatar {
  width: 26px; height: 26px; border-radius: 50%;
  background: linear-gradient(135deg, #34d399, #059669);
  display: inline-flex; align-items: center; justify-content: center;
  color: #fff; font-weight: 600; font-size: 12px;
}

/* ── Notes list (fictional) ── */
.note-item {
  display: flex; align-items: center; gap: 14px;
  padding: 14px 24px;
  border-top: 1px solid var(--border);
}
.note-item:first-child { border-top: none; }
.note-icon {
  width: 32px; height: 32px; border-radius: 8px;
  background: var(--brand-tint);
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 14px;
}
.note-title { flex: 1; font-size: 14px; font-weight: 500; }
.note-time { color: var(--ink-faint); font-size: 12px; font-family: ui-monospace, monospace; }

/* ── Auth panel + claim dump ── */
.verified-pill {
  display: inline-flex; align-items: center; gap: 6px;
  background: var(--brand-tint);
  color: var(--brand-ink);
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 11px; font-weight: 500;
}
.auth-fact { display: flex; gap: 10px; padding: 10px 0; font-size: 13px; color: var(--ink-muted); }
.auth-fact strong { color: var(--ink); font-weight: 500; }
.auth-fact .check { color: var(--brand); font-weight: 700; }
.claims-toggle {
  display: inline-flex; align-items: center; gap: 6px;
  background: var(--bg); border: 1px solid var(--border);
  padding: 8px 12px; border-radius: 8px;
  font-size: 12px; color: var(--ink); cursor: pointer;
  width: 100%; justify-content: space-between;
  font-family: inherit;
}
.claims-toggle:hover { background: #f5f5f4; }
.claims-toggle .chev { transition: transform .15s; }
.claims[open] .claims-toggle .chev { transform: rotate(90deg); }
.claims-dump {
  background: #0c0a09; color: #e7e5e4;
  padding: 16px;
  border-radius: 8px;
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  font-size: 11.5px; line-height: 1.7;
  overflow-x: auto;
  margin-top: 12px;
}
.claims-dump .k { color: #6ee7b7; }
.claims-dump .v { color: #fde68a; }

.config-strip {
  display: flex; flex-wrap: wrap; gap: 8px 24px;
  padding: 14px 24px;
  border-top: 1px solid var(--border);
  font-family: ui-monospace, monospace; font-size: 11px; color: var(--ink-faint);
}
.config-strip strong { color: var(--ink-muted); font-weight: 500; }
"""


# Reusable nav strip — emoji "logo" + brand text, optional right-side slot.
_NAV_LANDING = """
<nav class="nav">
  <div class="brand"><span class="brand-mark">A</span> Acme Notes</div>
  <div class="nav-right">
    <a class="nav-link" href="#">Pricing</a>
    <a class="nav-link" href="#">Docs</a>
    <a class="btn btn-primary" href="{{ url_for('login') }}">Sign in</a>
  </div>
</nav>
"""


_NAV_DASHBOARD = """
<nav class="nav">
  <div class="brand"><span class="brand-mark">A</span> Acme Notes</div>
  <div class="nav-right">
    <a class="nav-link" href="#">My notes</a>
    <a class="nav-link" href="#">Workspaces</a>
    <span class="user-chip">
      <span class="avatar">{{ (claims.get('email') or claims['sub'])[:1] | upper }}</span>
      {{ claims.get('email') or claims['sub'] }}
    </span>
    <a class="btn btn-ghost" href="{{ url_for('logout') }}">Sign out</a>
  </div>
</nav>
"""


LANDING_HTML = (
    """<!doctype html>
<html><head><meta charset="utf-8">
<title>Acme Notes · Your team's collective brain</title>
<style>""" + _SHARED_CSS + """</style></head><body>
"""
    + _NAV_LANDING
    + """
<section class="hero">
  <h1>Your team's <span class="accent">collective brain</span></h1>
  <p>Notes that sync. Search that actually works. Sign in to pick up where you
     left off — your device is your password.</p>
  <div class="cta-row">
    <a class="btn btn-primary btn-lg" href="{{ url_for('login') }}">Sign in with Hawcx →</a>
    <a class="btn btn-ghost btn-lg" href="#features">Learn more</a>
  </div>
</section>

<section class="features" id="features">
  <div class="feature-grid">
    <div class="feature">
      <div class="feature-icon">✍️</div>
      <h3>Capture anything</h3>
      <p>Markdown, screenshots, voice memos — all in one searchable inbox.</p>
    </div>
    <div class="feature">
      <div class="feature-icon">🔍</div>
      <h3>Search that works</h3>
      <p>Full-text + semantic search across notes, attachments, and links.</p>
    </div>
    <div class="feature">
      <div class="feature-icon">🛡️</div>
      <h3>Passwordless by default</h3>
      <p>Device-bound credentials via Hawcx — no passwords, no SMS codes,
         nothing to phish.</p>
    </div>
  </div>
</section>

<footer>
  <div class="secured">
    <span class="lock">🔒</span>
    Secured by <a href="https://hawcx.com">Hawcx</a> · OIDC RP demo ·
    <code>{{ issuer }}</code>
  </div>
</footer>
</body></html>"""
)


SIGNED_IN_HTML = (
    """<!doctype html>
<html><head><meta charset="utf-8">
<title>My notes · Acme Notes</title>
<style>""" + _SHARED_CSS + """</style></head><body>
"""
    + _NAV_DASHBOARD
    + """
<main class="container dashboard">
  <h1>Welcome back, {{ (claims.get('email') or claims['sub']).split('@')[0] }}.</h1>
  <p class="sub">You haven't opened Acme Notes in a while. Here's where you left off.</p>

  <div class="grid-2">
    <!-- ── Left column: fake notes list ──────────────────── -->
    <div class="panel">
      <div class="panel-header">
        <h2>Recent notes</h2>
        <a class="btn btn-primary" href="#">+ New note</a>
      </div>
      <div>
        <div class="note-item">
          <div class="note-icon">📝</div>
          <div class="note-title">Q3 architecture review notes</div>
          <div class="note-time">2h ago</div>
        </div>
        <div class="note-item">
          <div class="note-icon">📝</div>
          <div class="note-title">Brainstorm: onboarding rewrite</div>
          <div class="note-time">yesterday</div>
        </div>
        <div class="note-item">
          <div class="note-icon">📝</div>
          <div class="note-title">Customer call — Sequinox</div>
          <div class="note-time">3 days ago</div>
        </div>
        <div class="note-item">
          <div class="note-icon">📝</div>
          <div class="note-title">Reading list (offsite)</div>
          <div class="note-time">1 week ago</div>
        </div>
      </div>
    </div>

    <!-- ── Right column: account / proof-of-auth panel ──── -->
    <div class="panel">
      <div class="panel-header">
        <h2>Account</h2>
        <span class="verified-pill">✓ id_token verified</span>
      </div>
      <div class="panel-body">
        <div class="auth-fact">
          <span class="check">✓</span>
          <span><strong>Signed in</strong> as {{ claims.get('email') or claims['sub'] }}</span>
        </div>
        <div class="auth-fact">
          <span class="check">✓</span>
          <span><strong>Device-bound credential</strong> (amr: <code>{{ claims.get('amr', ['?']) | join(', ') }}</code>)</span>
        </div>
        <div class="auth-fact">
          <span class="check">✓</span>
          <span><strong>JWT verified</strong> against the published JWKS</span>
        </div>

        <details class="claims" style="margin-top: 14px;">
          <summary class="claims-toggle">
            <span>Show full id_token claims</span>
            <span class="chev">›</span>
          </summary>
          <div class="claims-dump">
            {% for k, v in claims.items() -%}
            <div><span class="k">{{ k }}</span>: <span class="v">{{ v | tojson }}</span></div>
            {%- endfor %}
          </div>
        </details>
      </div>
      <div class="config-strip">
        <span><strong>iss:</strong> {{ issuer }}</span>
        <span><strong>jwks:</strong> <a href="{{ jwks_uri }}">view</a></span>
      </div>
    </div>
  </div>
</main>

<footer>
  <div class="secured">
    <span class="lock">🔒</span>
    Acme Notes · Authenticated via Hawcx OIDC
  </div>
</footer>
</body></html>"""
)


@app.route("/")
def index():
    if "id_token_claims" in session:
        return render_template_string(
            SIGNED_IN_HTML,
            claims=session["id_token_claims"],
            issuer=OIDC_ISSUER,
            jwks_uri=_discovery["jwks_uri"],
        )
    return render_template_string(
        LANDING_HTML,
        issuer=OIDC_ISSUER,
        client_id=CLIENT_ID,
        redirect_uri=REDIRECT_URI,
    )


@app.route("/login")
def login():
    """Kick off the OIDC code flow.

    Generates PKCE verifier+challenge and nonce ourselves rather than
    relying on Authlib's internal PKCE management — the verifier is the
    one thing we MUST round-trip via the Flask session, so making it
    explicit keeps the code path obvious.
    """
    # PKCE per RFC 7636. A 32-byte random verifier base64url-encodes to 43
    # chars (well above the spec's 43-char minimum and below the 128-char
    # maximum). create_s256_code_challenge applies SHA-256 + base64url.
    verifier = secrets.token_urlsafe(32)
    challenge = create_s256_code_challenge(verifier)

    # OIDC nonce — round-tripped through the id_token's `nonce` claim and
    # verified in /callback. Defends against id_token replay.
    nonce = secrets.token_urlsafe(16)

    oauth = OAuth2Session(
        client_id=CLIENT_ID,
        redirect_uri=REDIRECT_URI,
        scope="openid profile email",
    )
    url, state = oauth.create_authorization_url(
        _discovery["authorization_endpoint"],
        code_challenge=challenge,
        code_challenge_method="S256",
        nonce=nonce,
        # Hawcx-specific: the hosted login UI's JS needs the Kong x-config-id
        # to call /v1/auth. Forwarded as a query param the OP threads through
        # to /login. Drop this once oauth_config.sdk_config_id ships.
        sdk_config_id=SDK_CONFIG_ID,
    )

    session["oauth_state"] = state
    session["oauth_nonce"] = nonce
    session["pkce_verifier"] = verifier
    return redirect(url)


@app.route("/callback")
def callback():
    """Receive ?code & ?state, exchange for id_token, verify, render.

    Order of validation is important:
        1. state — defends against CSRF on the redirect
        2. error params — surface OP-side rejections before token exchange
        3. token exchange — needs Kong's x-config-id header, plus PKCE
        4. id_token signature — JWKS verification
        5. id_token claims — iss / aud / exp / nbf / nonce
    """
    expected_state = session.pop("oauth_state", None)
    expected_nonce = session.pop("oauth_nonce", None)
    verifier = session.pop("pkce_verifier", None)

    received_state = request.args.get("state")
    if not expected_state or received_state != expected_state:
        return _err("State mismatch — possible CSRF or a stale session"), 400

    if "error" in request.args:
        desc = request.args.get("error_description", "")
        return _err(f"Authorization error: {request.args['error']} — {desc}"), 400

    code = request.args.get("code")
    if not code or not verifier:
        return _err("Missing code or PKCE verifier"), 400

    # --- Token exchange ----------------------------------------------------
    # Hawcx's /oauth2/token follows the public-client OIDC profile per
    # RFC 6749 §3.2.1 + OIDC Core §3.1.3.2: client_id is required as a
    # form field (it identifies the RP and is what gets stamped into the
    # id_token's aud claim). No client secret — public-client + PKCE is
    # the auth mechanism. The legacy Kong x-config-id header is no longer
    # required at this endpoint.
    try:
        token_resp = requests.post(
            _discovery["token_endpoint"],
            data={
                "code": code,
                "code_verifier": verifier,
                "client_id": CLIENT_ID,
            },
            timeout=15,
        )
    except requests.RequestException as e:
        return _err(f"Network error reaching token endpoint: {e}"), 502

    if not token_resp.ok:
        body = token_resp.text[:500]
        return _err(
            f"Token endpoint returned {token_resp.status_code}: {body}"
        ), 400

    token = token_resp.json()
    id_token = token.get("id_token")
    if not id_token:
        return _err("Token response missing id_token"), 502

    # --- id_token verification --------------------------------------------
    # authlib verifies signature using the JWKS we cached at startup. The
    # claims_options dict tells it which claim values are required and what
    # they must equal — anything mismatched raises a JoseError.
    try:
        claims = jwt.decode(
            id_token,
            _jwks,
            claims_options={
                "iss": {"essential": True, "value": OIDC_ISSUER},
                "aud": {"essential": True, "value": CLIENT_ID},
                "exp": {"essential": True},
                "nonce": {"essential": True, "value": expected_nonce},
            },
        )
        claims.validate()
    except JoseError as e:
        return _err(f"id_token verification failed: {e}"), 400

    # Strip non-serializable fields for the session dict and render.
    session["id_token_claims"] = dict(claims)
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    """Local logout only — Hawcx doesn't expose an OIDC end_session endpoint."""
    session.clear()
    return redirect(url_for("index"))


def _err(message: str) -> str:
    """Error page that matches the Acme Notes brand chrome."""
    return render_template_string(
        """<!doctype html><html><head><meta charset="utf-8">
        <title>Sign-in failed · Acme Notes</title>
        <style>""" + _SHARED_CSS + """
        .err-wrap { padding: 80px 32px; display: flex; justify-content: center; }
        .err-card { max-width: 520px; width: 100%; }
        .err-card .panel-body { padding: 28px 24px; }
        .err-title { display: flex; align-items: center; gap: 10px;
          font-size: 17px; font-weight: 600; color: var(--danger-ink); margin: 0 0 6px; }
        .err-msg { color: var(--ink-muted); font-size: 14px; margin: 0 0 20px; line-height: 1.55; }
        </style></head><body>""" + _NAV_LANDING + """
        <div class="err-wrap">
          <div class="panel err-card">
            <div class="panel-body">
              <h1 class="err-title">⚠️ Couldn't sign you in</h1>
              <p class="err-msg">{{ message }}</p>
              <a class="btn btn-primary" href="{{ url_for('index') }}">← Back to Acme Notes</a>
            </div>
          </div>
        </div></body></html>""",
        message=message,
    )


if __name__ == "__main__":
    # `threaded=True` so the dev server doesn't deadlock on the discovery
    # fetch at startup if the OIDC OP is slow.
    app.run(host="0.0.0.0", port=APP_PORT, debug=True, threaded=True)
