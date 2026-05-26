"""Service-level E2E for the demo RP's two token-endpoint auth flows.

Exercises the full OIDC code exchange against a live OP (dev-demo by
default), for BOTH client-authentication profiles the demo RP supports:

* public client + PKCE (``token_endpoint_auth_method=none``)
* private_key_jwt (RFC 7523, EdDSA assertion)

Plus the two negative cases that prove the OP pins the method per client:
an assertion presented for the public client is rejected, and a replayed
assertion is rejected.

Configuration source
--------------------
The test reads the SAME ``.env`` the demo RP uses (``hawcx_oidc_demo_rp/.env``)
so there's a single source of truth — no separate fixture cache. It needs:

    HAWCX_OIDC_ISSUER            # OP issuer / base URL
    HAWCX_CONFIG_ID             # Kong x-config-id for the project
    HAWCX_CLIENT_ID             # public client_id (== project tenant_id here)
    HAWCX_CONFIDENTIAL_CLIENT_ID # private_key_jwt client_id
    HAWCX_PRIVATE_KEY_PATH      # PEM for the confidential client (rel. to RP dir)
    HAWCX_PRIVATE_KEY_KID       # kid registered with the OP
    REDIRECT_URI                # allowlisted redirect

The private_key_jwt tests skip cleanly if the confidential profile isn't
configured (so the suite still runs on a public-only setup).

Running
-------
``/internal/mint-code`` is not exposed via Kong, so point the test at an
in-cluster OP for that one call via ``OP_INTERNAL_BASE`` (a port-forward to
the hawcx_core_oauth service). The public token endpoint stays on the issuer::

    kubectl -n hawcx-core-oauth port-forward svc/hawcx-core-oauth-service 18080:80 &
    OP_INTERNAL_BASE=http://localhost:18080 \
      python -m pytest tests/test_private_key_jwt_e2e.py -v
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from dotenv import dotenv_values

# RP root = parent of this tests/ dir. .env + keys/ live there.
RP_DIR = Path(__file__).resolve().parents[1]
_ENV = {**dotenv_values(RP_DIR / ".env"), **os.environ}


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    return _ENV.get(key, default)


def _require(key: str) -> str:
    v = _env(key)
    if not v:
        pytest.skip(f"{key} not set in .env — cannot run this flow")
    return v


def _pkce_pair() -> tuple[str, str]:
    """Return (verifier, S256 challenge) per RFC 7636."""
    verifier = secrets.token_urlsafe(32)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    return verifier, challenge


def _resolve_key_path() -> Optional[Path]:
    raw = _env("HAWCX_PRIVATE_KEY_PATH")
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = (RP_DIR / p).resolve()
    return p if p.is_file() else None


def _confidential_available() -> bool:
    return bool(
        _env("HAWCX_CONFIDENTIAL_CLIENT_ID")
        and _env("HAWCX_PRIVATE_KEY_KID")
        and _resolve_key_path()
    )


def _build_client_assertion(
    *,
    client_id: str,
    token_endpoint: str,
    private_key_path: Path,
    kid: str,
    lifetime_seconds: int = 60,
) -> str:
    """Sign an RFC 7523 client_assertion JWT with the RP's Ed25519 key."""
    with open(private_key_path, "rb") as f:
        private_key = serialization.load_pem_private_key(f.read(), password=None)
    now = int(time.time())
    claims = {
        "iss": client_id,
        "sub": client_id,
        "aud": token_endpoint,
        "iat": now,
        "exp": now + lifetime_seconds,
        "jti": secrets.token_urlsafe(16),
    }
    return pyjwt.encode(claims, private_key, algorithm="EdDSA", headers={"kid": kid})


async def _discovery() -> Dict[str, Any]:
    issuer = _require("HAWCX_OIDC_ISSUER").rstrip("/")
    async with httpx.AsyncClient() as http:
        return (await http.get(f"{issuer}/.well-known/openid-configuration")).json()


async def _mint_code(*, tenant_id: str, config_id: str, challenge: str, nonce: str, sub: str, aud: str) -> str:
    """Mint an auth code via the OP's internal test endpoint.

    /internal/mint-code isn't exposed through Kong, so this uses
    OP_INTERNAL_BASE (a port-forward) when set, else the public issuer.
    """
    issuer = _require("HAWCX_OIDC_ISSUER").rstrip("/")
    op_internal_base = os.environ.get("OP_INTERNAL_BASE", issuer)
    headers = {"X-Consumer-Id": tenant_id, "x-config-id": config_id}
    async with httpx.AsyncClient() as http:
        r = await http.post(
            f"{op_internal_base}/internal/mint-code",
            json={
                "tenant_id": tenant_id,
                "sub": sub,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "nonce": nonce,
                "ttl": 60,
                "aud": aud,
            },
            headers=headers,
            timeout=15,
        )
    assert r.status_code == 200, f"mint-code failed: {r.status_code} {r.text}"
    return r.json()["code"]


@pytest.mark.asyncio
async def test_public_client_pkce_flow():
    """Public-client + PKCE (method=none) issues a valid id_token."""
    issuer = _require("HAWCX_OIDC_ISSUER").rstrip("/")
    config_id = _require("HAWCX_CONFIG_ID")
    client_id = _require("HAWCX_CLIENT_ID")  # == project tenant_id in this demo
    redirect_uri = _env("REDIRECT_URI", "http://localhost:5555/callback")

    disco = await _discovery()
    verifier, challenge = _pkce_pair()
    nonce = secrets.token_urlsafe(16)
    sub = f"e2e-public-{secrets.token_urlsafe(8)}"
    code = await _mint_code(
        tenant_id=client_id, config_id=config_id,
        challenge=challenge, nonce=nonce, sub=sub, aud=client_id,
    )

    async with httpx.AsyncClient() as http:
        resp = await http.post(
            disco["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
            },
            timeout=15,
        )
    assert resp.status_code == 200, f"public exchange failed: {resp.status_code} {resp.text}"
    claims = pyjwt.decode(resp.json()["id_token"], options={"verify_signature": False})
    assert claims["iss"] == issuer
    assert claims["aud"] == client_id
    assert claims["sub"] == sub
    assert claims["nonce"] == nonce


@pytest.mark.asyncio
async def test_private_key_jwt_flow():
    """private_key_jwt issues an id_token whose aud == the confidential client_id."""
    if not _confidential_available():
        pytest.skip("Confidential profile not configured in .env")

    issuer = _require("HAWCX_OIDC_ISSUER").rstrip("/")
    config_id = _require("HAWCX_CONFIG_ID")
    tenant_id = _require("HAWCX_CLIENT_ID")  # mint-code keys off the project tenant_id
    client_id = _require("HAWCX_CONFIDENTIAL_CLIENT_ID")
    kid = _require("HAWCX_PRIVATE_KEY_KID")
    key_path = _resolve_key_path()
    redirect_uri = _env("REDIRECT_URI", "http://localhost:5555/callback")

    disco = await _discovery()
    assert "private_key_jwt" in disco.get("token_endpoint_auth_methods_supported", [])
    assert "EdDSA" in disco.get("token_endpoint_auth_signing_alg_values_supported", [])
    token_endpoint = disco["token_endpoint"]

    verifier, challenge = _pkce_pair()
    nonce = secrets.token_urlsafe(16)
    sub = f"e2e-pkjwt-{secrets.token_urlsafe(8)}"
    code = await _mint_code(
        tenant_id=tenant_id, config_id=config_id,
        challenge=challenge, nonce=nonce, sub=sub, aud=client_id,
    )

    assertion = _build_client_assertion(
        client_id=client_id, token_endpoint=token_endpoint,
        private_key_path=key_path, kid=kid,
    )
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_assertion_type": (
                    "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
                ),
                "client_assertion": assertion,
            },
            timeout=15,
        )
    assert resp.status_code == 200, f"pkjwt exchange failed: {resp.status_code} {resp.text}"
    claims = pyjwt.decode(resp.json()["id_token"], options={"verify_signature": False})
    assert claims["iss"] == issuer
    assert claims["aud"] == client_id, f"aud must equal confidential client_id, got {claims['aud']!r}"
    assert claims["sub"] == sub
    assert claims["nonce"] == nonce


@pytest.mark.asyncio
async def test_assertion_rejected_for_public_client():
    """Downgrade defense: an assertion claiming the public (none) client fails."""
    if not _confidential_available():
        pytest.skip("Confidential profile not configured in .env")

    public_client_id = _require("HAWCX_CLIENT_ID")
    kid = _require("HAWCX_PRIVATE_KEY_KID")
    key_path = _resolve_key_path()
    disco = await _discovery()
    token_endpoint = disco["token_endpoint"]

    # Sign an assertion whose iss = the PUBLIC client (registered as none).
    assertion = _build_client_assertion(
        client_id=public_client_id, token_endpoint=token_endpoint,
        private_key_path=key_path, kid=kid,
    )
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": "irrelevant-rejected-before-code-check",
                "code_verifier": "irrelevant",
                "client_id": public_client_id,
                "client_assertion_type": (
                    "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
                ),
                "client_assertion": assertion,
            },
            timeout=15,
        )
    assert resp.status_code in (400, 401), f"expected rejection, got {resp.status_code}: {resp.text}"
    assert resp.json().get("error") == "invalid_client"


@pytest.mark.asyncio
async def test_replayed_assertion_rejected():
    """The same jti reused within its lifetime is rejected the second time."""
    if not _confidential_available():
        pytest.skip("Confidential profile not configured in .env")

    config_id = _require("HAWCX_CONFIG_ID")
    tenant_id = _require("HAWCX_CLIENT_ID")
    client_id = _require("HAWCX_CONFIDENTIAL_CLIENT_ID")
    kid = _require("HAWCX_PRIVATE_KEY_KID")
    key_path = _resolve_key_path()
    redirect_uri = _env("REDIRECT_URI", "http://localhost:5555/callback")

    disco = await _discovery()
    token_endpoint = disco["token_endpoint"]

    # Two distinct codes; one reused assertion.
    v1, c1 = _pkce_pair()
    v2, c2 = _pkce_pair()
    code1 = await _mint_code(
        tenant_id=tenant_id, config_id=config_id,
        challenge=c1, nonce=secrets.token_urlsafe(16), sub="e2e-replay-1", aud=client_id,
    )
    code2 = await _mint_code(
        tenant_id=tenant_id, config_id=config_id,
        challenge=c2, nonce=secrets.token_urlsafe(16), sub="e2e-replay-2", aud=client_id,
    )
    assertion = _build_client_assertion(
        client_id=client_id, token_endpoint=token_endpoint,
        private_key_path=key_path, kid=kid, lifetime_seconds=120,
    )

    async def _exchange(code: str, verifier: str) -> httpx.Response:
        async with httpx.AsyncClient() as http:
            return await http.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": verifier,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "client_assertion_type": (
                        "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
                    ),
                    "client_assertion": assertion,
                },
                timeout=15,
            )

    first = await _exchange(code1, v1)
    second = await _exchange(code2, v2)
    assert first.status_code == 200, f"first exchange should succeed: {first.text}"
    assert second.status_code in (400, 401), f"replay should be rejected: {second.text}"
    desc = (second.json().get("error_description") or "").lower()
    assert "replay" in desc or "already used" in desc, f"unexpected: {second.json()!r}"
