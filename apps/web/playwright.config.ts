import { defineConfig, devices } from "@playwright/test";

// Browser tests run against the real stack (FastAPI + worker + Next.js production build) on a
// dedicated database prepared by scripts/e2e-prepare.sh (`npm run e2e` runs it first, before the
// servers start). Only free demo providers are used.
const env = {
  LQ_DATABASE_URL: process.env.LQ_E2E_DATABASE_URL || "postgresql+psycopg://lq:lq@127.0.0.1:55432/lq_e2e",
  LQ_STORAGE_BACKEND: "local",
  LQ_STORAGE_LOCAL_DIR: `${__dirname}/../../var/e2e-storage`,
  LQ_ENV: "test",
  LQ_ENV_FILE: "none",
  LQ_WORKER_POLL_SEC: "0.5",
  LQ_LOGIN_MAX_FAILURES: "50",
  OPENAI_API_KEY: "",
};
const iphone = { ...devices["iPhone 14"], browserName: "chromium" as const, defaultBrowserType: "chromium" as const };

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  use: { baseURL: "http://127.0.0.1:3100", trace: "off", serviceWorkers: "allow" },
  projects: [
    { name: "phone", use: iphone },
    { name: "desktop", use: { ...devices["Desktop Chrome"] }, testMatch: /smoke\.spec\.ts/ },
  ],
  webServer: [
    // API and generation worker start together (the worker has no port of its own to wait for)
    { command: "sh -c 'uv run lq worker & uv run uvicorn lq.api.app:app --host 127.0.0.1 --port 8100'", cwd: "../../services/core", env, url: "http://127.0.0.1:8100/api/health", reuseExistingServer: false, timeout: 60_000 },
    { command: "npx next start -p 3100", env: { API_INTERNAL_URL: "http://127.0.0.1:8100" }, url: "http://127.0.0.1:3100/login", reuseExistingServer: false, timeout: 60_000 },
  ],
});
