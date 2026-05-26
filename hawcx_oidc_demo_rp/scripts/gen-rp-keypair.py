#!/usr/bin/env python3
"""Generate an Ed25519 keypair for HAWCX_AUTH_METHOD=private_key_jwt.

Usage::

    python scripts/gen-rp-keypair.py [client_name]

What it does
------------
1. Generates a fresh Ed25519 private key (cryptography library).
2. Writes the private key in PKCS#8 PEM form to ``keys/private.pem``
   (creates the directory if missing; refuses to overwrite an existing
   file — delete or move it first to rotate).
3. Prints the corresponding public JWK to stdout — paste this into the
   OP admin console (Project → Settings → OAuth → Generate / Add JWK).

The script does not contact the OP. Registration is a separate manual
step on purpose: we want the operator to confirm in the admin console
that the public key landed on the right project before the RP starts
signing assertions with the matching private key.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import date
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate an Ed25519 RP signing keypair for private_key_jwt."
    )
    parser.add_argument(
        "client_name",
        nargs="?",
        default="rp",
        help="Used as a prefix for the generated kid. Defaults to 'rp'.",
    )
    parser.add_argument(
        "--out",
        default="keys/private.pem",
        help="Path to write the PEM-encoded private key (default: keys/private.pem).",
    )
    args = parser.parse_args()

    out_path = Path(args.out)
    if out_path.exists():
        print(
            f"refusing to overwrite existing private key at {out_path} — move or "
            f"delete it first if you want to rotate.",
            file=sys.stderr,
        )
        return 2

    out_path.parent.mkdir(parents=True, exist_ok=True)

    private_key = Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    # 0600 — RP-private. Same intent as ssh keys: not world-readable.
    out_path.write_bytes(pem)
    try:
        os.chmod(out_path, 0o600)
    except OSError:
        # Best-effort on filesystems that don't support chmod (Windows).
        pass

    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    kid = f"{args.client_name}-{date.today():%Y-%m}"
    public_jwk = {
        "kty": "OKP",
        "crv": "Ed25519",
        "kid": kid,
        "alg": "EdDSA",
        "use": "sig",
        "x": _b64url(public_bytes),
    }

    print(f"Wrote private key (PKCS#8 PEM) to {out_path}")
    print()
    print("Public JWK — paste into the OP admin console:")
    print()
    print(json.dumps(public_jwk, indent=2))
    print()
    print(f"Then set HAWCX_PRIVATE_KEY_PATH={out_path} and HAWCX_PRIVATE_KEY_KID={kid} in .env.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
