import { defineConfig, devices } from "@playwright/test";

const PORT = 8799;
// The backend in --demo mode: fake LLM, embeddings and GitHub. No network, no keys, nothing real is created.
const UV = process.env.UV_BIN ?? "uv";

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  outputDir: "./test-results",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 } }],
  webServer: {
    command: `cd .. && ${UV} run python -m app.web --demo --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
