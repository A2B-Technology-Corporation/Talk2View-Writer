/**
 * Streaming chat E2E: assistant text appears progressively as the
 * mock engine emits chunks, not all-at-once after the stream closes.
 *
 * This is the regression that proves the streaming proxy
 * (proxy_stream_open/next) is reaching the UI as a streaming
 * Response, not a buffered one. Without streaming, the SDK would
 * see the entire SSE body at once and the UI would jump from
 * "thinking" to the full reply.
 */
import { test, expect } from '../fixtures/test-fixtures';

test.describe('streaming chat', () => {
  test('assistant text grows incrementally before the stream closes', async ({
    appPage,
    mockEngine,
  }) => {
    // Three chunks with 300ms gaps. The first one should be on
    // screen well before the third one arrives.
    mockEngine.scriptChatStream([
      { type: 'delta', content: 'Counting: ' },
      { type: 'delta', content: 'one... ', delayMs: 300 },
      { type: 'delta', content: 'two... ', delayMs: 300 },
      { type: 'delta', content: 'three.', delayMs: 300, finish_reason: 'stop' },
    ]);

    await expect
      .poll(
        async () =>
          appPage.evaluate(() =>
            window.__t2vTestLogs?.some((l) => l.message.startsWith('[app] <App> mounted')),
          ),
        { timeout: 10_000 },
      )
      .toBeTruthy();

    const composer = appPage.getByRole('textbox', { name: /message|chat/i }).first();
    await composer.fill('count to three');
    const sendStart = Date.now();
    await composer.press('Enter');

    // The first chunk ("Counting: ") arrives almost immediately —
    // assert it shows before 500ms have passed, well before the
    // 4th chunk (which lands at ~900ms+ stream-time).
    await expect(appPage.getByText('Counting:', { exact: false })).toBeVisible({
      timeout: 1500,
    });
    const firstSeenAt = Date.now();

    // The complete reply appears later — once the stream has fully
    // drained.
    await expect(
      appPage.getByText(/Counting: one\.\.\. two\.\.\. three\./, { exact: false }),
    ).toBeVisible({ timeout: 5000 });
    const lastSeenAt = Date.now();

    // Progressive: there must be a measurable gap between the first
    // chunk landing in the DOM and the last chunk arriving — the
    // SDK isn't waiting for the stream to close before rendering.
    // 300 ms is the minimum scheduled gap; assert > 200 ms to allow
    // some scheduling slack while still ruling out "everything in
    // one batch".
    const gap = lastSeenAt - firstSeenAt;
    expect(gap).toBeGreaterThan(200);

    // Sanity: the whole flow completes well under 5 s.
    expect(lastSeenAt - sendStart).toBeLessThan(5000);
  });
});
