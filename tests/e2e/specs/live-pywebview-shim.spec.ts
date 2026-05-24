/**
 * Unit tests for the live `installLivePywebviewShim`.
 *
 * The live shim proxies every `window.pywebview.api.*` call to the
 * Node bridge-proxy via HTTP. These tests drive the shim with the
 * proxy connected to a `MockBridge` so we can assert end-to-end
 * routing without needing a real soffice.
 *
 * Architecture C, scaffold step 3.
 */
import { test, expect } from '@playwright/test';
import { MockBridge } from '../fixtures/mock-bridge';
import { BridgeProxy } from '../fixtures/bridge-proxy';
import { installLivePywebviewShim } from '../fixtures/live-pywebview-shim';

test.describe('live-pywebview-shim ↔ bridge-proxy ↔ mock-bridge', () => {
  let mock: MockBridge;
  let proxy: BridgeProxy;

  test.beforeEach(async () => {
    mock = new MockBridge();
    const socketPath = await mock.start();
    proxy = new BridgeProxy({ socketPath });
    await proxy.start();
  });

  test.afterEach(async () => {
    await proxy?.stop();
    await mock?.stop();
  });

  test('invoke_tool returns the bridge result to the page', async ({ page }) => {
    mock.respondWith((req) => {
      expect(req.method).toBe('invoke_tool');
      expect(req.params).toMatchObject({
        name: 'get_document',
        args: { count: 10 },
      });
      return {
        result: JSON.stringify({ paragraphs: [], total_paragraphs: 0 }),
      };
    });

    await page.addInitScript(installLivePywebviewShim, { proxyUrl: proxy.url() });
    await page.goto(proxy.url() + '/');

    const result = await page.evaluate(async () => {
      return await window.pywebview!.api.invoke_tool('get_document', { count: 10 });
    });

    expect(typeof result).toBe('string');
    expect(JSON.parse(result as string)).toMatchObject({ total_paragraphs: 0 });
    expect(mock.requests).toHaveLength(1);
  });

  test('invoke_tool surfaces bridge errors as a thrown Error in the page', async ({
    page,
  }) => {
    mock.respondWith(() => ({
      error: { type: 'ValueError', message: 'bad tool' },
    }));

    await page.addInitScript(installLivePywebviewShim, { proxyUrl: proxy.url() });
    await page.goto(proxy.url() + '/');

    const errorMsg = await page.evaluate(async () => {
      try {
        await window.pywebview!.api.invoke_tool('oops', {});
        return null;
      } catch (err) {
        return (err as Error).message;
      }
    });

    expect(errorMsg).toMatch(/bad tool/);
  });

  test('list_tools returns the allowlist to the page', async ({ page }) => {
    mock.respondWith(() => ({
      result: [
        'get_document',
        'get_selection',
        'insert_content',
        'format_text',
        'format_paragraph',
        'search_document',
        'manage_preferences',
      ],
    }));

    await page.addInitScript(installLivePywebviewShim, { proxyUrl: proxy.url() });
    await page.goto(proxy.url() + '/');

    const tools = await page.evaluate(async () => {
      return await window.pywebview!.api.list_tools();
    });

    expect(tools).toHaveLength(7);
    expect(tools).toContain('insert_content');
  });

  test('log is fire-and-forget — the bridge sees it, the call resolves', async ({
    page,
  }) => {
    mock.respondWith(() => ({ result: null }));

    await page.addInitScript(installLivePywebviewShim, { proxyUrl: proxy.url() });
    await page.goto(proxy.url() + '/');

    await page.evaluate(async () => {
      await window.pywebview!.api.log('info', '[chat:user] hi', { foo: 'bar' });
    });

    await expect.poll(() => mock.requests.length).toBe(1);
    expect(mock.requests[0]).toMatchObject({
      method: 'log',
      params: { level: 'info', message: '[chat:user] hi', context: { foo: 'bar' } },
    });
  });

  test('every invoke_tool call is also captured for spec-level inspection', async ({
    page,
  }) => {
    // The live shim, like the mock shim, records calls on
    // window.__t2vToolCalls so specs can later assert on the tool
    // sequence the engine drove without re-listening to the bridge.
    mock.respondWith(() => ({ result: '{}' }));

    await page.addInitScript(installLivePywebviewShim, { proxyUrl: proxy.url() });
    await page.goto(proxy.url() + '/');

    await page.evaluate(async () => {
      await window.pywebview!.api.invoke_tool('get_document', {});
      await window.pywebview!.api.invoke_tool('insert_content', { text: 'x' });
    });

    const calls = await page.evaluate(() => window.__t2vToolCalls);
    expect(calls).toEqual([
      { name: 'get_document', args: {} },
      { name: 'insert_content', args: { text: 'x' } },
    ]);
  });
});
