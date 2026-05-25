/**
 * Playwright config for Talk2View-Writer E2E.
 *
 * Specs live in tests/e2e/specs/. Each spec uses the test fixtures in
 * tests/e2e/fixtures/ to spin up a per-test mock engine and a
 * Playwright page pre-wired with a pywebview API shim.
 *
 * See ADR-0031 for why we drive the bundle in Chromium rather than the
 * real pywebview window.
 */
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e/specs',
  // 30s default; SSE streams shouldn't take longer than that in tests
  // (the mock engine emits final chunks within 100 ms).
  timeout: 30_000,
  expect: { timeout: 5_000 },

  // Run specs in parallel within a file but each file in its own
  // worker so the per-test mock engine + page-init shim have isolated
  // state.
  fullyParallel: true,
  workers: process.env.CI ? 2 : undefined,

  // Treat any test.only as a hard failure in CI — saves us from
  // landing a one-off debug run as a regression.
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,

  // Reporters: list during local runs for readable output; HTML +
  // JUnit + line in CI so workflow logs stay readable AND the GH
  // artifact upload picks up a browsable HTML report.
  reporter: process.env.CI
    ? [
        ['html', { outputFolder: 'tests/e2e/playwright-report', open: 'never' }],
        ['junit', { outputFile: 'tests/e2e/junit-results.xml' }],
        ['line'],
      ]
    : [['list']],

  // Artifacts: keep screenshots/videos/traces on failure for Claude /
  // a reviewer to inspect after the workflow finishes. Names are
  // stable per-test (Playwright defaults) so workflow artifacts are
  // browseable by spec name.
  //
  // Successful-run screenshots are taken explicitly via
  // `appPage.screenshot({ path: ... })` from the fixture's afterEach
  // hook so a passing run still uploads a visual record — see the
  // user's "100M MAU gold standard, screenshots for Claude to review"
  // posture (memory: feedback_engineering_standard).
  use: {
    baseURL: 'http://127.0.0.1:0', // overridden per-test by the staticServer fixture
    actionTimeout: 5_000,
    navigationTimeout: 10_000,
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    trace: 'retain-on-failure',
  },

  outputDir: 'tests/e2e/test-results',

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    // WebKit covers Playwright's Apple-WebKit engine — closest available
    // proxy for WKWebView (macOS pywebview backend). It is *not* the same
    // engine as WebKitGTK (Linux pywebview backend). See ADR-0031.
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },
  ],
});
