/**
 * Playwright globalTeardown — Windows-only forced exit safety net.
 *
 * Investigation #36: on Windows runners, Playwright's worker
 * teardown intermittently leaves a child process unable to exit
 * cleanly. After all tests complete, ``worker.stop()`` never
 * returns; Playwright force-kills after 300s and reports the kill
 * as a test-run-level error → job goes red even with 0 test
 * failures.
 *
 * --workers=1 helped most runs but the latest CI surfaced
 * ``worker-1 process did not exit`` — i.e. the lone worker hangs
 * regardless of the worker count.
 *
 * Mitigation: schedule a force-exit 5 s after globalTeardown runs.
 * If the runner exits naturally first, the timer never fires
 * (Linux + macOS path). If it hangs (Windows path), the timer
 * fires and force-exits using whatever ``process.exitCode``
 * Playwright already set based on test outcomes — so this NEVER
 * hides a real test failure.
 *
 * Belt-and-braces only on Windows. Linux + macOS rely on
 * Playwright's normal exit so any new lingering-handle bugs surface
 * loudly.
 */
async function globalTeardown(): Promise<void> {
  if (process.platform !== 'win32') return;

  // process.exitCode is set by Playwright before globalTeardown runs
  // based on test pass/fail. Honour it — never coerce to 0.
  setTimeout(() => {
    // eslint-disable-next-line no-console
    console.log(
      'globalTeardown: forcing process.exit on win32 — Playwright runner ' +
        'hung past 5 s after globalTeardown (Investigation #36)',
    );
    process.exit(process.exitCode ?? 0);
  }, 5_000);
}

export default globalTeardown;
