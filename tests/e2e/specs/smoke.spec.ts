/**
 * Smoke E2E: the chat UI loads, the user can send a message, the
 * assistant reply renders.
 *
 * This is the gate test — if it fails, something has gone wrong with
 * the bundle, the SDK, the mock engine, or the pywebview shim. Spec
 * authors writing finer-grained tests should add to this file with
 * additional `test(...)` blocks rather than spawning new spec files
 * for trivial coverage.
 */
import { test, expect } from '../fixtures/test-fixtures';

test.describe('chat smoke', () => {
  test('bundle loads, composer is interactive, scripted reply renders', async ({
    appPage,
    mockEngine,
  }) => {
    // Script the assistant reply BEFORE the SDK sends its chat
    // completion request, so the SSE stream the SDK reads is the one
    // we asserted on.
    mockEngine.scriptChatStream([
      { type: 'delta', content: 'Hello! How can I help you today?' },
      { type: 'delta', finish_reason: 'stop' },
    ]);

    // The bundle's <App> calls logToHost('info', '[app] <App> mounted')
    // on mount. Use that as the bootstrap signal — once observed, we
    // know React has hydrated and the SDK provider is wired.
    await expect
      .poll(
        async () => {
          const logs = await appPage.evaluate(() => window.__t2vTestLogs);
          return logs?.some((l) => l.message.startsWith('[app] <App> mounted'));
        },
        { timeout: 10_000, message: 'Bundle never logged [app] <App> mounted' },
      )
      .toBeTruthy();

    // Composer should be visible. The SDK's ChatPanel exposes the
    // textarea via `role="textbox"`; if the SDK changes the role we
    // update the assertion + add a contract test.
    const composer = appPage.getByRole('textbox', { name: /message|chat/i }).first();
    await expect(composer).toBeVisible({ timeout: 10_000 });

    await composer.fill('hi');
    // The SDK binds Enter to send by default. Pressing Enter is more
    // realistic than clicking the send button (whose icon-only label
    // changes across SDK versions).
    await composer.press('Enter');

    // The assistant message should render the scripted content.
    await expect(appPage.getByText(/hello! how can i help you today/i)).toBeVisible({
      timeout: 10_000,
    });

    // Engine should have seen exactly one chat-completion request.
    const chatRequests = mockEngine.requests.filter((r) =>
      /^\/v1\/sessions\/[^/]+\/messages$/.test(r.path),
    );
    expect(chatRequests).toHaveLength(1);
  });

  test('INFO logs do not leak the user email (PII) to the host log', async ({
    appPage,
  }) => {
    // The appPage fixture pre-seeds a session for tester@example.com.
    // Wait for the auth-state log to be emitted.
    await expect
      .poll(
        async () => {
          const logs = await appPage.evaluate(() => window.__t2vTestLogs);
          return logs?.some((l) => l.message.startsWith('[auth] state'));
        },
        { timeout: 10_000, message: 'never logged [auth] state' },
      )
      .toBeTruthy();

    const logs = (await appPage.evaluate(() => window.__t2vTestLogs)) ?? [];
    const infoLogs = logs.filter((l) => l.level === 'info');

    // No INFO log message or context may contain the email address.
    for (const l of infoLogs) {
      expect(l.message).not.toContain('tester@example.com');
      expect(JSON.stringify(l.context ?? null)).not.toContain('tester@example.com');
    }

    // The auth-state log records presence, not the address.
    const authLog = infoLogs.find((l) => l.message.startsWith('[auth] state'));
    expect(authLog).toBeTruthy();
    const ctx = (authLog?.context ?? {}) as Record<string, unknown>;
    expect(ctx).toHaveProperty('has_email');
    expect(ctx).not.toHaveProperty('email');

    // remember_email logs presence only, never the address.
    const rememberLog = infoLogs.find((l) =>
      l.message.startsWith('[remember_email]'),
    );
    if (rememberLog) {
      expect(rememberLog.message).not.toContain('@');
    }
  });
});
