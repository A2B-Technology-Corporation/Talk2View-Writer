/**
 * Per-test Playwright fixtures.
 *
 * `mockEngine` and `appPage` are bound per-test so a flaky test can't
 * leak state into the next. Each test gets:
 *
 *   - A fresh MockEngine bound to a random localhost port.
 *   - A Page with the pywebview shim pre-installed (via
 *     `addInitScript`) and the bundle navigated to.
 *
 * Use them via the typed `test` export:
 *
 *   import { test, expect } from '../fixtures/test-fixtures';
 *
 *   test('smoke', async ({ appPage, mockEngine }) => { ... });
 */
import { test as base, expect } from '@playwright/test';
import { mkdir } from 'fs/promises';
import { resolve, join } from 'path';
import { MockEngine } from './mock-engine';
import { installPywebviewShim } from './pywebview-shim';

type Fixtures = {
  mockEngine: MockEngine;
  appPage: import('@playwright/test').Page;
};

// Screenshot every test (pass OR fail) into a stable per-spec path so
// CI artifacts always include a visual record. See ADR-0031 and
// memory feedback_engineering_standard — the user wants screenshots
// available for review regardless of test outcome, not only on
// failure.
const ALWAYS_SCREENSHOT_DIR = resolve(__dirname, '../screenshots');

const BUNDLE_DIR = resolve(__dirname, '../../../src/web/dist');

export const test = base.extend<Fixtures>({
  mockEngine: async ({}, use) => {
    const engine = new MockEngine();
    await engine.start({ staticRoot: BUNDLE_DIR });
    try {
      await use(engine);
    } finally {
      await engine.stop();
    }
  },

  appPage: async ({ page, mockEngine }, use) => {
    // 1. Pre-inject the pywebview shim BEFORE any script runs so the
    //    bundle's first call to `window.pywebview.api.log(...)` (in
    //    `installHostLogging`) hits the shim rather than a missing
    //    object.
    await page.addInitScript(installPywebviewShim);

    // 2. Override the bundle's hard-coded BASE_URL via a global so
    //    the SDK fetches from the mock engine. The bundle reads
    //    `window.__T2V_BASE_URL_OVERRIDE` at boot if set; see
    //    `src/web/src/App.tsx`.
    const baseUrl = mockEngine.url();
    await page.addInitScript((url) => {
      (window as unknown as { __T2V_BASE_URL_OVERRIDE: string }).__T2V_BASE_URL_OVERRIDE =
        url;
    }, baseUrl);

    // 3. Pre-seed a fake auth session in the keys the SDK actually
    //    reads (talk2view_access_token, talk2view_user, ...) so the
    //    SDK's auth check passes and the composer renders. Tests that
    //    want to exercise login can clear these keys before navigation.
    await page.addInitScript(() => {
      localStorage.setItem('talk2view_access_token', 'mock-pre-seeded');
      localStorage.setItem('talk2view_refresh_token', 'mock-refresh');
      localStorage.setItem(
        'talk2view_user',
        JSON.stringify({ id: 'mock-user-id', email: 'tester@example.com' }),
      );
    });

    // 4. Navigate to the bundle served by the mock engine on the same
    //    origin (so the page's own fetch + the SDK's fetch reach the
    //    engine without CORS headaches).
    await page.goto(`${baseUrl}/index.html`);

    await use(page);

    // Always-on screenshot after the test body finishes — pass or
    // fail. Each spec produces a deterministic filename under
    // tests/e2e/screenshots/ so the CI artifact upload yields a
    // browsable directory tree indexed by spec title.
    const info = test.info();
    try {
      await mkdir(ALWAYS_SCREENSHOT_DIR, { recursive: true });
      const safeName = info.titlePath.join('--').replace(/[^a-z0-9._-]/gi, '_');
      const file = join(
        ALWAYS_SCREENSHOT_DIR,
        `${safeName}--${info.project.name}--${info.status}.png`,
      );
      await page.screenshot({ path: file, fullPage: true });
    } catch (err) {
      // Screenshot failure shouldn't fail the test it's reporting on —
      // pywebview-shim already captures bundle errors via __t2vTestLogs.
      // eslint-disable-next-line no-console
      console.warn('always-on screenshot failed', err);
    }
  },
});

export { expect };
