import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  reporter: "line",
  use: {
    // Keep the browser host aligned with routerUrl() and ROUTER_WEBAUTHN_ORIGIN.
    // Mixing 127.0.0.1 and localhost turns the admin session cookie into a
    // cross-site cookie, so authenticated browser QA would fail for the wrong
    // reason even though the token login itself succeeded.
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop-chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile-chromium", use: { ...devices["Pixel 7"] } },
  ],
});
