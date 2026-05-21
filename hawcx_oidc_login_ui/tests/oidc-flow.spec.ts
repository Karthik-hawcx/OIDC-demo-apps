/**
 * Headed end-to-end test of the OIDC authorization code flow against the
 * live dev-demo OP, exercising the full hop chain that includes ZKP
 * Ed25519 device enrollment (the thing the plain-JS stub couldn't do).
 *
 *  Browser starts at the demo RP    (localhost:5555)
 *  RP redirects to /authorize       (dev-demo-api.hawcx.com)
 *  OP redirects to the login UI     (localhost:5173, this app)
 *  SDK drives /v1/auth (proxied through Vite -> dev-demo)
 *  SDK transparently does ZKP enrollment via WebCrypto
 *  SDK reaches the "completed" step → app navigates to /authorize/resume
 *  OP 302s back to the RP's redirect_uri with code + state
 *  RP exchanges code at /oauth2/token, verifies the id_token via JWKS
 *  RP renders the claims — assertion target
 *
 * The dev-demo project (risk-demo-3) is configured with
 * device_enrollment=required, so this test FAILS if anything in the SDK
 * + login UI path can't drive ZKP.
 *
 * Watching live: `npm run test:e2e` (the config defaults to headed + slowMo).
 */

import { expect, test } from "@playwright/test";

// Use a fresh email per run so the SDK enters the "new user" flow every
// time, which is what triggers ZKP setup_device. Stable across the test
// itself (used once at start, referenced in assertions).
const TEST_EMAIL = `e2e-pw-${Date.now()}@hawcx.com`;

test("OIDC code flow with ZKP device enrollment", async ({ page, context }) => {
  // ── Phase 1 — intercept /v1/auth so we can pluck debug.otp_code ──────────
  //
  // dev-demo runs hx_auth in test mode (ENABLE_DEBUG_ENDPOINTS=true). The
  // start response includes the OTP under step.debug.otp_code. We listen
  // for the first /v1/auth response and resolve a promise with the OTP so
  // the OTP-entry step can read it. Without this hook the test would need
  // a real inbox.
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
        // /v1/auth response that isn't an enter_code step — ignore.
      }
    });
  });

  // ── Phase 2 — RP landing page ─────────────────────────────────────────────
  await page.goto("http://localhost:5555/");
  // The demo RP is branded as "Acme Notes" — its h1 reads "Your team's
  // collective brain". Asserting that confirms (a) we hit the right URL
  // and (b) the page actually rendered before we click anything.
  await expect(
    page.getByRole("heading", { name: /collective brain/i }),
  ).toBeVisible();

  // Click the hero CTA "Sign in with Hawcx →". The nav also has a plain
  // "Sign in" link; the hero CTA is the one with the explicit "with Hawcx"
  // copy, which makes the selector unambiguous regardless of layout tweaks.
  await Promise.all([
    page.waitForURL(/localhost:5173/, { timeout: 30_000 }),
    page.getByRole("link", { name: /Sign in with Hawcx/i }).click(),
  ]);

  // ── Phase 3 — Login UI loads with the correct embedded params ────────────
  await expect(
    page.getByRole("heading", { name: /Sign in to continue/i }),
  ).toBeVisible();

  // op_session_id is rendered in the meta strip at the bottom of the card.
  // 43 chars of base64url is the only thing we need to assert; the exact
  // value varies per run.
  await expect(page.locator(".meta")).toContainText(/op_session_id:/);

  // ── Phase 4 — Identifier step (HawcxSignUpSignIn email input) ────────────
  //
  // The component renders its own email input with a placeholder. We don't
  // know the exact selector across SDK versions, so we use semantic queries
  // first and fall back to common shapes.
  const emailInput = page
    .getByRole("textbox", { name: /email/i })
    .or(page.locator('input[type="email"]'))
    .first();
  await emailInput.waitFor({ state: "visible" });
  await emailInput.fill(TEST_EMAIL);

  const continueBtn = page
    .getByRole("button", { name: /continue|next|sign/i })
    .first();
  await continueBtn.click();

  // ── Phase 5 — OTP step — wait for /v1/auth response with debug OTP ───────
  const otp = await otpReceived;
  test.info().annotations.push({ type: "otp", description: otp });

  // The HawcxSignUpSignIn OTP input is typically autocomplete=one-time-code
  // or a single textbox labeled "code". Either works.
  const otpInput = page
    .locator('input[autocomplete="one-time-code"]')
    .or(page.getByRole("textbox", { name: /code|otp/i }))
    .first();
  await otpInput.waitFor({ state: "visible", timeout: 15_000 });
  await otpInput.fill(otp);

  const verifyBtn = page
    .getByRole("button", { name: /verify|submit|continue/i })
    .first();
  await verifyBtn.click();

  // ── Phase 6 — ZKP device enrollment happens transparently inside the SDK.
  //
  // @hawcx/core generates an Ed25519 keypair via WebCrypto, performs the
  // attestation handshake with hx_auth, stores credentials in IndexedDB.
  // The UI shows a spinner during this; we just wait until we land back
  // on the demo RP. The RP's /callback handler does a server-side
  // redirect to /, so the URL we end up at is localhost:5555/ — too fast
  // to reliably catch the /callback step with waitForURL. Matching the
  // RP origin (with optional /callback or root path) covers both.
  await page.waitForURL(/^http:\/\/localhost:5555\/(callback|$|\?)/, {
    timeout: 60_000,
  });

  // ── Phase 7 — RP's callback exchanges the code and renders claims ────────
  //
  // Wait for the claims panel to appear. The demo RP labels the success
  // state with "id_token verified". Asserting the email roundtripped is
  // a strong end-to-end signal — it means:
  //   * The SDK reached "completed"
  //   * mint-code bound the code to the parked op_session
  //   * The OP overrode PKCE/nonce with the RP's values (the OIDC
  //     correctness fix from this branch)
  //   * /authorize/resume found the bound code and 302'd
  //   * The RP's authlib exchanged the code with PKCE verifier (matched!)
  //   * authlib verified the id_token against the JWKS (signature ok,
  //     iss/aud/exp/nonce all matched)
  await expect(page.getByText(/id_token verified/i)).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.locator(".claims")).toContainText(TEST_EMAIL);

  // Pause so the user can inspect the rendered claims before the window
  // closes. Comment out for CI / fast iteration.
  if (!process.env.CI) {
    await page.waitForTimeout(3000);
  }
});
