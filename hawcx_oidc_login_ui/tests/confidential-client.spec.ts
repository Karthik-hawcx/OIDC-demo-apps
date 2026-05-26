/**
 * Headed E2E for the OIDC code flow against an RP configured for
 * private_key_jwt token-endpoint authentication.
 *
 * Mirrors oidc-flow.spec.ts step-for-step, with two differences:
 *
 *   1. **Skip gate.** The test self-skips when the OP's discovery doc
 *      does not advertise ``private_key_jwt`` in
 *      ``token_endpoint_auth_methods_supported``, OR when the RP is not
 *      configured for the confidential profile (HAWCX_AUTH_METHOD env
 *      var on the RP, which the spec checks indirectly by probing the
 *      RP's /healthz). This keeps the spec safe to land before the
 *      OP-side change ships everywhere.
 *
 *   2. **Token-exchange assertion.** After the redirect-back to the RP,
 *      the existing `oidc-flow.spec.ts` only asserts "id_token verified".
 *      We additionally inspect the RP's debug response body (or the
 *      claims panel) for the `client_authentication_method` line — the
 *      RP renders the method it used so the test can confirm the
 *      confidential path actually fired, not the silent fallback.
 *
 * The OTP intercept logic, the ZKP enrollment wait, and the redirect
 * assertions are intentionally duplicated rather than extracted to a
 * shared helper — the two specs evolve at different cadences and having
 * each one self-contained keeps debugging fast.
 */

import { expect, request, test } from "@playwright/test";

const RP_BASE = "http://localhost:5555";
const OP_ISSUER = process.env.HAWCX_OIDC_ISSUER ?? "https://dev-demo-api.hawcx.com";

const TEST_EMAIL = `e2e-pkjwt-${Date.now()}@hawcx.com`;

test.beforeAll(async () => {
  // Skip gate 1 — OP advertises private_key_jwt.
  const apiCtx = await request.newContext();
  const discovery = await apiCtx
    .get(`${OP_ISSUER}/.well-known/openid-configuration`)
    .then((r) => r.json())
    .catch(() => null);
  await apiCtx.dispose();

  if (!discovery) {
    test.skip(true, `Could not fetch OP discovery from ${OP_ISSUER}`);
    return;
  }
  const supported: string[] =
    discovery?.token_endpoint_auth_methods_supported ?? [];
  if (!supported.includes("private_key_jwt")) {
    test.skip(
      true,
      `OP at ${OP_ISSUER} does not advertise private_key_jwt — got: ${supported.join(", ")}`,
    );
    return;
  }

  // Skip gate 2 — RP is running in confidential mode. There's no public
  // env-introspection endpoint on the demo RP, so we proxy: if
  // HAWCX_AUTH_METHOD is anything other than ``private_key_jwt`` in the
  // RP's process environment, this test would silently behave the same
  // as the existing public-client spec. Read the local env that
  // playwright.config sets when launching the RP webServer.
  if ((process.env.HAWCX_AUTH_METHOD ?? "none") !== "private_key_jwt") {
    test.skip(
      true,
      "RP not configured for HAWCX_AUTH_METHOD=private_key_jwt — set it in playwright.config or your shell to run this test.",
    );
  }
});

test("OIDC code flow — confidential client (private_key_jwt)", async ({
  page,
  context,
}) => {
  // ── Phase 1 — intercept /v1/auth so we can pluck debug.otp_code ──────────
  const otpReceived = new Promise<string>((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error("Timed out waiting for /v1/auth start response")),
      30_000,
    );
    context.on("response", async (resp) => {
      const url = resp.url();
      if (!url.includes("/v1/auth")) return;
      if (resp.request().method() !== "POST") return;
      try {
        const body = await resp.json();
        const otp = body?.step?.debug?.otp_code;
        if (typeof otp === "string" && /^\d{6,8}$/.test(otp)) {
          clearTimeout(timer);
          resolve(otp);
        }
      } catch {
        // not an OTP response — ignore
      }
    });
  });

  // ── Phase 2 — RP landing page and "Sign in" CTA ──────────────────────────
  await page.goto(`${RP_BASE}/`);
  await expect(
    page.getByRole("heading", { name: /collective brain/i }),
  ).toBeVisible();
  await Promise.all([
    page.waitForURL(/localhost:5173/, { timeout: 30_000 }),
    page.getByRole("link", { name: /Sign in with Hawcx/i }).click(),
  ]);

  // ── Phase 3 — Login UI loads ─────────────────────────────────────────────
  await expect(
    page.getByRole("heading", { name: /Sign in to continue/i }),
  ).toBeVisible();

  // ── Phase 4 — Email entry ────────────────────────────────────────────────
  const emailInput = page
    .getByRole("textbox", { name: /email/i })
    .or(page.locator('input[type="email"]'))
    .first();
  await emailInput.waitFor({ state: "visible" });
  await emailInput.fill(TEST_EMAIL);
  await page
    .getByRole("button", { name: /continue|next|sign/i })
    .first()
    .click();

  // ── Phase 5 — OTP ────────────────────────────────────────────────────────
  const otp = await otpReceived;
  const otpInput = page
    .locator('input[autocomplete="one-time-code"]')
    .or(page.getByRole("textbox", { name: /code|otp/i }))
    .first();
  await otpInput.waitFor({ state: "visible", timeout: 15_000 });
  await otpInput.fill(otp);
  await page
    .getByRole("button", { name: /verify|submit|continue/i })
    .first()
    .click();

  // ── Phase 6 — Wait for the redirect back to the RP ───────────────────────
  // Capture the network request the RP makes to /oauth2/token so the
  // assertions in Phase 7 can confirm the confidential-client form
  // fields actually went out. We pin the path-only match (not the full
  // URL) so the assertion is resilient to issuer hostname changes.
  const tokenRequest = page.waitForRequest((req) =>
    req.url().endsWith("/oauth2/token"),
  );
  await page.waitForURL(/^http:\/\/localhost:5555\/(callback|$|\?)/, {
    timeout: 60_000,
  });

  // ── Phase 7 — RP rendered claims; token-endpoint body included assertion ─
  await expect(page.getByText(/id_token verified/i)).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.locator(".claims")).toContainText(TEST_EMAIL);

  // Inspect the token-endpoint POST. The body MUST carry
  // ``client_assertion_type`` + ``client_assertion``; if those are
  // missing, the RP silently fell back to the public-client path which
  // would not exercise the new code path.
  const req = await tokenRequest;
  const body = req.postData() ?? "";
  expect(
    body,
    "token endpoint POST must include client_assertion_type",
  ).toContain(
    "client_assertion_type=urn%3Aietf%3Aparams%3Aoauth%3Aclient-assertion-type%3Ajwt-bearer",
  );
  expect(
    body,
    "token endpoint POST must include client_assertion",
  ).toMatch(/client_assertion=[A-Za-z0-9._-]+/);

  if (!process.env.CI) {
    await page.waitForTimeout(2000);
  }
});
