/**
 * Unit tests for the Node bridge-proxy.
 *
 * The bridge-proxy is a small HTTP server that translates browser-side
 * calls (`window.pywebview.api.invoke_tool`, etc.) into the Python
 * ``BridgeServer``'s newline-delimited JSON-RPC protocol over the
 * Unix socket. These tests drive a ``MockBridge`` (Node-side fake of
 * the Python bridge) so we don't need a running soffice + extension.
 *
 * Architecture C, scaffold step 2 — see ADR-0036 (forthcoming).
 */
import { test, expect } from '@playwright/test';
import { MockBridge } from '../fixtures/mock-bridge';
import { BridgeProxy } from '../fixtures/bridge-proxy';

test.describe('bridge-proxy ↔ mock-bridge round-trip', () => {
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

  test('invoke_tool forwards name + args, returns the bridge result verbatim', async () => {
    mock.respondWith((req) => {
      // Mirror the BridgeServer's _invoke_tool: returns the tool's
      // string-JSON return value as the result field.
      expect(req.method).toBe('invoke_tool');
      expect(req.params).toEqual({ name: 'get_document', args: { count: 10 } });
      return {
        result: JSON.stringify({
          paragraphs: [{ index: 0, text: 'Hello', style: 'Standard' }],
          total_paragraphs: 1,
        }),
      };
    });

    const resp = await fetch(`${proxy.url()}/invoke_tool`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ name: 'get_document', args: { count: 10 } }),
    });

    expect(resp.status).toBe(200);
    const body = (await resp.json()) as { result: string };
    // body.result is a JSON-encoded string verbatim from the bridge
    // — parse it again to assert on the structure.
    const parsed = JSON.parse(body.result) as {
      paragraphs: unknown[];
      total_paragraphs: number;
    };
    expect(parsed.total_paragraphs).toBe(1);
    expect(mock.requests).toHaveLength(1);
  });

  test('invoke_tool surfaces bridge errors as 500 with the error payload', async () => {
    mock.respondWith(() => ({
      error: { type: 'ValueError', message: "tool 'oops' not in MVP allowlist" },
    }));

    const resp = await fetch(`${proxy.url()}/invoke_tool`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ name: 'oops', args: {} }),
    });

    expect(resp.status).toBe(500);
    const body = (await resp.json()) as {
      error: { type: string; message: string };
    };
    expect(body.error.type).toBe('ValueError');
    expect(body.error.message).toMatch(/MVP allowlist/);
  });

  test('log is fire-and-forget — returns 204, mock sees the line', async () => {
    mock.respondWith(() => ({ result: null }));

    const resp = await fetch(`${proxy.url()}/log`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        level: 'info',
        message: '[chat:user] hi',
        context: null,
      }),
    });

    expect([200, 204]).toContain(resp.status);
    // Single round-trip — same connection, but the mock still saw one
    // line.
    await expect.poll(() => mock.requests.length).toBe(1);
    expect(mock.requests[0]).toMatchObject({
      method: 'log',
      params: { level: 'info', message: '[chat:user] hi' },
    });
  });

  test('list_tools returns the bridge allowlist verbatim', async () => {
    mock.respondWith((req) => {
      expect(req.method).toBe('list_tools');
      return {
        result: [
          'get_document',
          'get_selection',
          'insert_content',
          'format_text',
          'format_paragraph',
          'search_document',
          'manage_preferences',
        ],
      };
    });

    const resp = await fetch(`${proxy.url()}/list_tools`);
    expect(resp.status).toBe(200);
    const body = (await resp.json()) as { result: string[] };
    expect(body.result).toContain('insert_content');
    expect(body.result).toHaveLength(7);
  });

  test('id field is unique per request so concurrent calls do not collide', async () => {
    // The bridge multiplexes responses by ``id``. A naïve proxy that
    // hard-codes id=1 would route the second response to the first
    // caller. Issue two parallel invoke_tool calls and assert both
    // receive their own result.
    let nextResult = 0;
    mock.respondWith(() => ({ result: `result_${nextResult++}` }));

    const results = await Promise.all([
      fetch(`${proxy.url()}/invoke_tool`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ name: 'get_document', args: {} }),
      }).then((r) => r.json() as Promise<{ result: string }>),
      fetch(`${proxy.url()}/invoke_tool`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ name: 'get_selection', args: {} }),
      }).then((r) => r.json() as Promise<{ result: string }>),
    ]);

    // The mock receives both requests; the bridge-proxy must route the
    // numbered responses back to the correct caller.
    expect(mock.requests).toHaveLength(2);
    const ids = mock.requests.map((r) => r.id);
    expect(new Set(ids).size).toBe(2); // ids are distinct
    expect(new Set(results.map((r) => r.result)).size).toBe(2);
  });
});
