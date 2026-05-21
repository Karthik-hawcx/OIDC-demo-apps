/**
 * Hosted OIDC login UI.
 *
 * The OP's /authorize redirects here with three query params:
 *   - op            : op_session_id (43-char base64url)
 *   - client_id     : Hawcx project UUID; also the OIDC `aud`
 *   - sdk_config_id : per-project Kong x-config-id the SDK calls /v1/auth with
 *
 * We read them, hand them to @hawcx/react's HawcxProvider, render the
 * stock HawcxSignUpSignIn component, and on completion navigate the
 * browser to /authorize/resume on the OP — which 302s back to the RP
 * with code + state.
 *
 * Everything ZKP-related (Ed25519 keypair generation, device attestation,
 * trusted-device storage in IndexedDB) is handled inside @hawcx/core.
 * This component never touches crypto directly.
 */

import { HawcxProvider, HawcxSignUpSignIn } from "@hawcx/react";
import type { AuthResult } from "@hawcx/core";
import { useMemo } from "react";

interface OidcParams {
  opSessionId: string;
  clientId: string;
  sdkConfigId: string;
}

// Validates the URL params at module level so the SPA fails fast and
// shows a readable error instead of a half-rendered card. Regex matches
// the patterns the OP enforces on its side.
const PARAM_PATTERN = /^[A-Za-z0-9._-]+$/;

function parseOidcParams(): { ok: true; params: OidcParams } | { ok: false; reason: string } {
  const qs = new URLSearchParams(window.location.search);
  const op = qs.get("op");
  const clientId = qs.get("client_id");
  const sdkConfigId = qs.get("sdk_config_id");

  const missing: string[] = [];
  if (!op) missing.push("op");
  if (!clientId) missing.push("client_id");
  if (!sdkConfigId) missing.push("sdk_config_id");
  if (missing.length > 0) {
    return {
      ok: false,
      reason: `Missing required query parameter${missing.length === 1 ? "" : "s"}: ${missing.join(", ")}. This page is meant to be reached via the OIDC /authorize redirect.`,
    };
  }

  for (const [name, value] of [
    ["op", op!],
    ["client_id", clientId!],
    ["sdk_config_id", sdkConfigId!],
  ] as const) {
    if (!PARAM_PATTERN.test(value)) {
      return {
        ok: false,
        reason: `Parameter ${name} contains disallowed characters.`,
      };
    }
  }

  return {
    ok: true,
    params: { opSessionId: op!, clientId: clientId!, sdkConfigId: sdkConfigId! },
  };
}

// The SDK calls /v1/auth on the same origin as this page. In production
// the OP serves this SPA from the same host as hx_auth (both behind Kong
// on dev-demo-api.hawcx.com), so window.location.origin is correct. In
// vite dev, the Vite proxy rewrites /v1 to VITE_PROXY_TARGET — see vite.config.ts.
function resolveApiBase(): string {
  return `${window.location.origin}/v1`;
}

function buildResumeUrl(opSessionId: string, clientId: string): string {
  const qs = new URLSearchParams({ op: opSessionId, client_id: clientId });
  return `/authorize/resume?${qs.toString()}`;
}

export default function App() {
  const parsed = useMemo(parseOidcParams, []);

  if (!parsed.ok) {
    return (
      <div className="card">
        <h1>Authorization error</h1>
        <p className="subtitle">{parsed.reason}</p>
      </div>
    );
  }

  const { opSessionId, clientId, sdkConfigId } = parsed.params;
  const apiBase = resolveApiBase();
  const resumeUrl = buildResumeUrl(opSessionId, clientId);

  const handleSuccess = (_result: AuthResult) => {
    // The SDK returned an authCode locally, but the OP has already bound
    // the same code to our op_session (via the opSessionId we passed to the
    // SDK -> hx_auth -> mint-code). We don't redeem the authCode here —
    // /authorize/resume hands it back to the RP via 302.
    window.location.href = resumeUrl;
  };

  return (
    <HawcxProvider
      config={{
        configId: sdkConfigId,
        apiBase,
        // The new field — threaded into every `start` action body.
        opSessionId,
      }}
    >
      <div className="card">
        <h1>Sign in to continue</h1>
        <p className="subtitle">
          Authorizing access to <code>{clientId}</code>
        </p>

        <HawcxSignUpSignIn onSuccess={handleSuccess} />

        <div className="meta">
          <strong>op_session_id:</strong> {opSessionId}
        </div>
      </div>
    </HawcxProvider>
  );
}
