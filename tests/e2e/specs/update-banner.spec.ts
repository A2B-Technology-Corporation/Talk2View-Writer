/**
 * E2E: the in-app "update available" banner.
 *
 * Exercises src/web/src/UpdateBanner.tsx + version.ts against the real
 * bundle. We don't use the auto-navigating `appPage` fixture here
 * because we must stub the GitHub Releases API BEFORE the bundle's
 * first mount (the banner checks on mount) — so we replicate the
 * fixture's setup and route `api.github.com` before `goto`. That also
 * keeps the suite off the real network.
 */
import { test, expect } from '../fixtures/test-fixtures';
import type { Page } from '@playwright/test';
import { installPywebviewShim } from '../fixtures/pywebview-shim';
import type { MockEngine } from '../fixtures/mock-engine';

const RELEASES_RE = /api\.github\.com\/repos\/.+\/releases\/latest/;

async function bootApp(
  page: Page,
  mockEngine: MockEngine,
  latestTag: string,
): Promise<void> {
  await page.addInitScript(installPywebviewShim);
  const baseUrl = mockEngine.url();
  await page.addInitScript((url) => {
    (window as unknown as { __T2V_BASE_URL_OVERRIDE: string }).__T2V_BASE_URL_OVERRIDE = url;
  }, baseUrl);
  await page.addInitScript(() => {
    localStorage.setItem('talk2view_access_token', 'mock-pre-seeded');
    localStorage.setItem('talk2view_refresh_token', 'mock-refresh');
    localStorage.setItem(
      'talk2view_user',
      JSON.stringify({ id: 'mock-user-id', email: 'tester@example.com' }),
    );
  });
  // Stub the GitHub Releases API before the bundle mounts.
  await page.route(RELEASES_RE, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ tag_name: latestTag }),
    }),
  );
  await page.goto(`${baseUrl}/index.html`);
}

test.describe('update banner', () => {
  test('shows when a newer release exists', async ({ page, mockEngine }) => {
    await bootApp(page, mockEngine, 'v99.0.0');
    const banner = page.getByTestId('update-banner');
    await expect(banner).toBeVisible({ timeout: 10_000 });
    await expect(banner).toContainText('v99.0.0');
  });

  test('Releases button opens the release page via the host (not window.open)', async ({
    page,
    mockEngine,
  }) => {
    await bootApp(page, mockEngine, 'v99.0.0');
    await expect(page.getByTestId('update-banner')).toBeVisible({ timeout: 10_000 });
    await page.getByRole('button', { name: 'Releases' }).click();
    // Goes through the pywebview host bridge (open_external), NOT JS
    // window.open which is a no-op in the WebKitGTK webview.
    await expect
      .poll(() => page.evaluate(() => window.__t2vExternalOpens ?? []), { timeout: 5_000 })
      .toContain('https://github.com/A2B-Technology-Corporation/Talk2View-Writer/releases/latest');
  });

  test('stays hidden when the latest release is not newer', async ({ page, mockEngine }) => {
    let checked = false;
    // Track that the banner actually performed its check (so a passing
    // assertion isn't just a race where the fetch hadn't run yet).
    await page.route(RELEASES_RE, (route) => {
      checked = true;
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ tag_name: 'v0.0.1' }),
      });
    });
    const baseUrl = mockEngine.url();
    await page.addInitScript(installPywebviewShim);
    await page.addInitScript((url) => {
      (window as unknown as { __T2V_BASE_URL_OVERRIDE: string }).__T2V_BASE_URL_OVERRIDE = url;
    }, baseUrl);
    await page.addInitScript(() => {
      localStorage.setItem('talk2view_access_token', 'mock-pre-seeded');
      localStorage.setItem('talk2view_refresh_token', 'mock-refresh');
      localStorage.setItem(
        'talk2view_user',
        JSON.stringify({ id: 'mock-user-id', email: 'tester@example.com' }),
      );
    });
    await page.goto(`${baseUrl}/index.html`);
    await expect.poll(() => checked, { timeout: 10_000 }).toBeTruthy();
    await expect(page.getByTestId('update-banner')).toHaveCount(0);
  });

  test('dismissal persists across reload', async ({ page, mockEngine }) => {
    await bootApp(page, mockEngine, 'v99.0.0');
    const banner = page.getByTestId('update-banner');
    await expect(banner).toBeVisible({ timeout: 10_000 });

    await page.getByRole('button', { name: 'Dismiss update notice' }).click();
    await expect(banner).toHaveCount(0);

    // localStorage remembers the dismissed version; the route + init
    // scripts re-apply on reload, so the same newer tag is offered again
    // but must stay suppressed.
    await page.reload();
    await expect.poll(async () => {
      const logs = await page.evaluate(() => window.__t2vTestLogs);
      return logs?.some((l) => l.message.startsWith('[app] <App> mounted'));
    }, { timeout: 10_000 }).toBeTruthy();
    await expect(page.getByTestId('update-banner')).toHaveCount(0);
  });
});
