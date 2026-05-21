import { defineConfig, devices } from "@playwright/test";

/**
 * Headed E2E config for the OIDC redirect flow.
 *
 * Boots two local servers automatically:
 *   1. Demo RP   (Flask, port 5555)  — the OIDC relying party
 *   2. Login UI  (Vite,  port 5173)  — this app
 *
 * Both talk to the real dev-demo OP at https://dev-demo-api.hawcx.com.
 * The OP has been configured (chart/values-hx-dev-demo.yaml) with
 * LOGIN_URL=http://localhost:5173/, so its /authorize redirects the
 * browser to this app on the developer's laptop.
 *
 * Headed mode is the default so a human can watch. The slowMo lets you
 * actually see each step instead of a blink-and-miss-it run.
 */
export default defineConfig({
  testDir: "./tests",
  // Fail fast on bad selectors / mismatched URLs; the OIDC flow has a lot
  // of network round-trips and we don't want to wait 30s on every miss.
  timeout: 120_000, // total per-test budget — flow takes ~30s, leaves headroom
  expect: { timeout: 15_000 },
  fullyParallel: false, // network state on dev-demo is shared; serial is safer
  workers: 1,
  reporter: [["list"]],

  use: {
    headless: false,
    // 250ms between actions — enough to follow visually without dragging
    // the test to a crawl. Tweak via `npx playwright test --headed`.
    launchOptions: { slowMo: 250 },
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    // Trace every test by default. View with `npx playwright show-trace`.
    trace: "on",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },

  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1100, height: 800 },
      },
    },
  ],

  webServer: [
    {
      // Demo RP (Flask). Uses its own .venv that hawcx_oidc_demo_rp set up.
      // `reuseExistingServer` lets you have a server already running for
      // faster iteration (e.g., during debugging).
      command:
        "../hawcx_oidc_demo_rp/.venv/bin/python ../hawcx_oidc_demo_rp/app.py",
      cwd: "../hawcx_oidc_demo_rp",
      port: 5555,
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      // Login UI (this app, Vite dev server). Same reuse-if-present escape
      // hatch for faster local iteration.
      command: "npm run dev",
      cwd: ".",
      port: 5173,
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
});
