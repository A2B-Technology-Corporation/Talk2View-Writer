/**
 * Regression: bridge.ts's logToHost must drain log entries that
 * arrive after the bridge's first flush completes.
 *
 * 2026-05-22 — initial version of bridge.ts had a `_logFlushStarted`
 * boolean latched after the first drain, so any log added after the
 * first drain emptied the buffer sat in the buffer forever. The bug
 * was invisible against the real pywebview bridge because the real
 * bridge is slower than React's render cycle (the buffer never
 * emptied during the initial render burst). It surfaced under
 * Playwright where the shim is in-process and instant.
 *
 * This spec exercises the pump's re-entrant behaviour: late logs
 * fired after a forced quiescence must still reach the host.
 */
import { test, expect } from '../fixtures/test-fixtures';

test.describe('bridge logToHost — late logs', () => {
  test('drains a log added after the first flush has completed', async ({ appPage }) => {
    // Wait for the bundle to log at least three entries (the bridge
    // installed + index.tsx loaded + React mounted). This proves the
    // drain has run at least once.
    await expect
      .poll(
        async () =>
          appPage.evaluate(() => (window.__t2vTestLogs?.length ?? 0) >= 3),
        { timeout: 10_000 },
      )
      .toBeTruthy();

    // Idle the page for half a second so the pump definitely
    // finishes its current cycle. Then fire a fresh log directly
    // through the bundle's own logToHost (we import the same module
    // by reaching into the bundle's webpack runtime would be brittle;
    // instead exercise via the public `console.log` path which the
    // installed host hooks route into logToHost — same code path).
    await appPage.waitForTimeout(500);
    const initialCount = await appPage.evaluate(
      () => window.__t2vTestLogs?.length ?? 0,
    );

    await appPage.evaluate(() => {
      // eslint-disable-next-line no-console
      console.log('late-log probe — should reach the host');
    });

    // The late log must drain to the host within the bridge's
    // re-entrant pump.
    await expect
      .poll(
        async () =>
          appPage.evaluate(() =>
            window.__t2vTestLogs?.some((l) => l.message.includes('late-log probe')),
          ),
        { timeout: 5_000, message: 'late log was dropped by the bridge' },
      )
      .toBeTruthy();

    const finalCount = await appPage.evaluate(() => window.__t2vTestLogs?.length ?? 0);
    expect(finalCount).toBeGreaterThan(initialCount);
  });
});
