import { defineConfig, devices } from "@playwright/test";

// Used only by scripts/live-verify.sh: the app on the verification database with real generated audio.
const env = {
  LQ_DATABASE_URL: process.env.LQ_DATABASE_URL || "postgresql+psycopg://lq:lq@127.0.0.1:55432/lq_verify",
  LQ_STORAGE_BACKEND: "local",
  LQ_STORAGE_LOCAL_DIR: process.env.LQ_STORAGE_LOCAL_DIR || `${__dirname}/../../var/verify-storage`,
  LQ_ENV_FILE: "none",
  OPENAI_API_KEY: "", // the app check itself never calls a paid API
};

export default defineConfig({
  testDir: "./e2e-live",
  workers: 1,
  timeout: 180_000,
  expect: { timeout: 20_000 },
  reporter: [["list"]],
  use: { baseURL: "http://127.0.0.1:3200", trace: "off" },
  projects: [{ name: "phone", use: { ...devices["iPhone 14"], browserName: "chromium" as const, defaultBrowserType: "chromium" as const } }],
  webServer: [
    { command: "uv run uvicorn lq.api.app:app --host 127.0.0.1 --port 8200", cwd: "../../services/core", env, url: "http://127.0.0.1:8200/api/health", reuseExistingServer: false, timeout: 60_000 },
    { command: "npx next start -p 3200", env: { API_INTERNAL_URL: "http://127.0.0.1:8200" }, url: "http://127.0.0.1:3200/login", reuseExistingServer: false, timeout: 60_000 },
  ],
});
