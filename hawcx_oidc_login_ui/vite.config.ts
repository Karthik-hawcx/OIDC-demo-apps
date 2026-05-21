import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// The OP redirects browsers to /login on its own origin (Kong routes /login
// to hawcx_core_oauth, which serves this SPA). When running `vite dev`
// locally, page origin is http://localhost:5173 — the SDK's /v1/auth calls
// can't go there. So we proxy /v1, /healthz, etc. through to whatever
// VITE_PROXY_TARGET points at (defaults to the dev-demo OP).
//
// Build output goes into dist/. The OP serves it at /login (and matches
// /login/* against the static asset paths emitted by Vite).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const proxyTarget =
    env.VITE_PROXY_TARGET || "https://dev-demo-api.hawcx.com";

  return {
    // Asset URLs are prefixed with this base path. The OP mounts the
    // built dist/ at /login on its own origin, so every asset request
    // (script, css, sourcemap) needs to be /login/assets/... not /assets/...
    // — otherwise the browser hits the OP root and 404s.
    base: "/login/",
    plugins: [react()],
    // Same trick the existing react-demo uses — resolve @hawcx/* to the
    // SDK source so HMR reflects in-tree SDK edits without a rebuild.
    resolve: {
      alias: {
        "@hawcx/core": path.resolve(
          __dirname,
          "../hawcx_web_demo/frontend/sdk/core/src",
        ),
        "@hawcx/react": path.resolve(
          __dirname,
          "../hawcx_web_demo/frontend/sdk/react/src",
        ),
      },
    },
    server: {
      host: true,
      port: 5173,
      open: false,
      proxy: {
        "/v1": {
          target: proxyTarget,
          changeOrigin: true,
          secure: true,
        },
        // /authorize, /authorize/resume, /oauth2/token also live on the OP.
        // The browser never POSTs /oauth2/token from this UI, but proxying
        // /authorize/resume keeps `window.location.href` redirects same-origin
        // during dev so the OP cookie scope works.
        "/authorize": {
          target: proxyTarget,
          changeOrigin: true,
          secure: true,
        },
        "/.well-known": {
          target: proxyTarget,
          changeOrigin: true,
          secure: true,
        },
      },
    },
    build: {
      outDir: "dist",
      emptyOutDir: true,
      sourcemap: true,
    },
  };
});
