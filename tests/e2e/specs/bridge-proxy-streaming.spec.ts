/**
 * Streaming tests for the bridge-proxy + live shim.
 *
 * The bridge_server's protocol is polled: ``proxy_stream_open`` returns
 * a stream_id; each ``proxy_stream_next(stream_id)`` returns one event
 * (headers / chunk / done / error / timeout). The proxy translates
 * this into SSE for the browser: the shim opens an EventSource per
 * stream and the proxy long-polls the bridge in a loop, pushing each
 * event as a `data:` frame. The shim queues events; the bundle's
 * existing ``proxy_stream_next`` consumer pops one at a time.
 *
 * Architecture C, scaffold step 4 (streaming).
 */
import { test, expect } from '@playwright/test';
import { MockBridge, BridgeRequest } from '../fixtures/mock-bridge';
import { BridgeProxy } from '../fixtures/bridge-proxy';
import { installLivePywebviewShim } from '../fixtures/live-pywebview-shim';

test.describe('bridge-proxy streaming ↔ mock-bridge', () => {
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

  test('proxy_stream_open + proxy_stream_next walks a scripted SSE sequence', async ({
    page,
  }) => {
    // Mock bridge scripts an open + a sequence of events that the
    // bundle's typical SSE consumer would see for a chat completion.
    const eventsToServe: Array<{ type: string; [k: string]: unknown }> = [
      { type: 'headers', status: 200, statusText: 'OK', headers: { 'content-type': 'text/event-stream' } },
      { type: 'chunk', data: 'data: {"delta":"Hello"}\n\n' },
      { type: 'chunk', data: 'data: {"delta":" world"}\n\n' },
      { type: 'chunk', data: 'data: [DONE]\n\n' },
      { type: 'done' },
    ];
    let nextIdx = 0;
    mock.respondWith((req: BridgeRequest) => {
      if (req.method === 'proxy_stream_open') {
        return { result: { stream_id: 'mock-stream-1' } };
      }
      if (req.method === 'proxy_stream_next') {
        const ev = eventsToServe[nextIdx++] ?? { type: 'done' };
        return { result: ev };
      }
      return { error: { type: 'UnknownMethod', message: req.method } };
    });

    await page.addInitScript(installLivePywebviewShim, { proxyUrl: proxy.url() });
    await page.goto(proxy.url() + '/');

    const events = await page.evaluate(async () => {
      const { stream_id } = await window.pywebview!.api.proxy_stream_open(
        'https://engine.example/v1/sessions/x/messages',
        'POST',
        { 'content-type': 'application/json' },
        '{"messages":[]}',
      );
      const out: unknown[] = [];
      for (let i = 0; i < 10; i++) {
        const ev = await window.pywebview!.api.proxy_stream_next(stream_id);
        out.push(ev);
        if (ev.type === 'done') break;
      }
      return out;
    });

    expect(events.map((e: { type: string }) => e.type)).toEqual([
      'headers',
      'chunk',
      'chunk',
      'chunk',
      'done',
    ]);
    // First chunk's data is preserved verbatim through the SSE channel.
    expect((events[1] as { data: string }).data).toBe('data: {"delta":"Hello"}\n\n');
  });

  test('error event from the bridge surfaces as type=error to the shim', async ({
    page,
  }) => {
    const eventsToServe: Array<{ type: string; [k: string]: unknown }> = [
      { type: 'error', message: 'engine went away' },
      { type: 'done' },
    ];
    let nextIdx = 0;
    mock.respondWith((req: BridgeRequest) => {
      if (req.method === 'proxy_stream_open') {
        return { result: { stream_id: 'mock-stream-err' } };
      }
      if (req.method === 'proxy_stream_next') {
        return { result: eventsToServe[nextIdx++] ?? { type: 'done' } };
      }
      return { error: { type: 'UnknownMethod', message: req.method } };
    });

    await page.addInitScript(installLivePywebviewShim, { proxyUrl: proxy.url() });
    await page.goto(proxy.url() + '/');

    const seen = await page.evaluate(async () => {
      const { stream_id } = await window.pywebview!.api.proxy_stream_open(
        'https://engine.example/x',
        'POST',
        {},
        null,
      );
      const events: unknown[] = [];
      for (let i = 0; i < 5; i++) {
        const ev = await window.pywebview!.api.proxy_stream_next(stream_id);
        events.push(ev);
        if (ev.type === 'done' || ev.type === 'error') break;
      }
      return events;
    });

    expect((seen[0] as { type: string }).type).toBe('error');
    expect((seen[0] as { message: string }).message).toMatch(/engine went away/);
  });
});
