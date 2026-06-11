/**
 * Regression: a streaming proxy error on the FIRST event must reject the
 * fetch, not hang the chat forever.
 *
 * The bridge server removes a stream from its registry the moment it
 * hands out a terminal event (proxy_stream_next pops on both 'error' and
 * 'done', bridge_server.py). The bundle's _proxyStream first-event error
 * path used to "drain the trailing done" with
 * `while ((await proxy_stream_next(id)).type !== 'done') {}` — but after
 * the pop, every poll returns an unknown-stream 'error', never 'done', so
 * the loop spun forever and the SDK fetch never settled.
 *
 * This spec installs a custom window.pywebview.api that replicates the
 * real server (error first, then unknown-stream errors forever) and
 * asserts the streaming fetch rejects within a bounded time. Under the
 * bug it would TIMEOUT.
 */
import { test, expect } from '../fixtures/test-fixtures';

test.skip(
  process.platform === 'win32',
  "bundle streaming path mirrors the AF_UNIX bridge; covered on POSIX runners",
);

test.describe('streaming error does not hang', () => {
  test('first-event stream error rejects the fetch instead of looping forever', async ({
    page,
    mockEngine,
  }) => {
    // Custom API that faithfully mirrors the Python bridge: each stream
    // returns 'error' on its first poll, then unknown-stream errors
    // forever (the registry entry is gone) — it NEVER yields 'done'.
    await page.addInitScript(() => {
      const counts = new Map<string, number>();
      let seq = 0;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (window as any).pywebview = {
        api: {
          async log() {},
          async ping(message: string) {
            return { echo: message };
          },
          async list_tools() {
            return [];
          },
          async invoke_tool() {
            return '{}';
          },
          async open_external() {
            return { opened: true };
          },
          async proxy_fetch() {
            return { status: 200, statusText: 'OK', headers: {}, body: '{}' };
          },
          async proxy_stream_open() {
            const id = `s-${++seq}`;
            counts.set(id, 0);
            return { stream_id: id };
          },
          async proxy_stream_next(id: string) {
            const n = (counts.get(id) ?? 0) + 1;
            counts.set(id, n);
            if (n === 1) return { type: 'error', message: 'engine boom' };
            return { type: 'error', message: `unknown stream_id ${id}` };
          },
        },
      };
    });

    await page.goto(`${mockEngine.url()}/index.html`);

    const outcome = await page.evaluate(async () => {
      const fetchP = fetch(
        'https://engine.talk2view.com/v1/sessions/x/messages',
        { method: 'POST', headers: { Accept: 'text/event-stream' }, body: '{}' },
      )
        .then(() => 'resolved')
        .catch((e: unknown) => `rejected:${(e as Error).message}`);
      const timeout = new Promise<string>((r) =>
        setTimeout(() => r('TIMEOUT'), 4000),
      );
      return Promise.race([fetchP, timeout]);
    });

    // Under the old drain loop this would never settle -> TIMEOUT.
    expect(outcome).not.toBe('TIMEOUT');
    expect(outcome).toContain('rejected');
    expect(outcome).toContain('engine boom');
  });
});
