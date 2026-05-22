/**
 * Bundle-budget regressions.
 *
 * The cold-start path must NOT pull Shiki (the syntax-highlighting
 * library that accounts for ~1.2 MB of WASM + JS). The SDK already
 * lazy-loads it inside ``CodeBlock`` via dynamic ``import('shiki/...')``,
 * which puts Shiki in a separate webpack chunk. This spec verifies
 * the lazy-load actually works at runtime — no chunk request matching
 * shiki/highlighter/onig before the first composer interaction.
 *
 * Also asserts the main ``bundle.js`` request stays under a size
 * budget. Together these protect against:
 *
 * - A future SDK upgrade that eagerly imports Shiki.
 * - A future webpack-config change that disables code splitting.
 * - An accidental ``import * from 'shiki'`` in App.tsx / bridge.ts.
 */
import { test, expect } from '../fixtures/test-fixtures';

const MAIN_BUNDLE_KB_BUDGET = 400;

test.describe('bundle budget', () => {
  test('main bundle is under the size budget', async ({ appPage }) => {
    const mainBundle = await appPage.evaluate(async () => {
      // Look for the bundle.js script tag and read its actual byte
      // length via a HEAD/GET. The bundle is served by the mock
      // engine's static-root handler on the same origin.
      const src = Array.from(document.scripts)
        .map((s) => s.src)
        .find((s) => /bundle\.js(?:\?.*)?$/.test(s));
      if (!src) return null;
      const resp = await fetch(src);
      const blob = await resp.blob();
      return { url: src, size: blob.size };
    });
    expect(mainBundle).not.toBeNull();
    const sizeKb = Math.ceil((mainBundle!.size ?? 0) / 1024);
    expect(
      sizeKb,
      `main bundle ${sizeKb}KB exceeds budget ${MAIN_BUNDLE_KB_BUDGET}KB — ` +
        'check for an eagerly-imported heavy dep (Shiki, monaco, ...).',
    ).toBeLessThanOrEqual(MAIN_BUNDLE_KB_BUDGET);
  });

  test('Shiki is NOT requested on cold start', async ({ page, mockEngine }) => {
    // Build the request log BEFORE navigation so we capture every
    // resource the bundle's boot path requests.
    const requested: string[] = [];
    page.on('request', (req) => {
      requested.push(req.url());
    });

    // Replay the fixture's init scripts manually here because the
    // ``appPage`` fixture would race with the listener registration.
    await page.addInitScript(() => {
      localStorage.setItem('talk2view_access_token', 'mock-pre-seeded');
      localStorage.setItem('talk2view_refresh_token', 'mock-refresh');
      localStorage.setItem(
        'talk2view_user',
        JSON.stringify({ id: 'mock-user-id', email: 'tester@example.com' }),
      );
    });
    const baseUrl = mockEngine.url();
    await page.addInitScript((url) => {
      (window as unknown as { __T2V_BASE_URL_OVERRIDE: string }).__T2V_BASE_URL_OVERRIDE =
        url;
    }, baseUrl);
    await page.goto(`${baseUrl}/index.html`);
    await page.waitForLoadState('networkidle');

    // Anything matching the names below would be Shiki's chunks.
    const shikiPatterns = [/shiki/i, /highlighter/i, /onigasm/i, /wasm/i];
    const shikiHits = requested.filter((u) =>
      shikiPatterns.some((p) => p.test(u)),
    );
    expect(
      shikiHits,
      `Shiki resources were requested on cold start: ${shikiHits.join(', ')}. ` +
        'Lazy-load is broken; check src/web/node_modules/@talk2view/sdk/dist/ui/components/CodeBlock.js.',
    ).toEqual([]);
  });
});
