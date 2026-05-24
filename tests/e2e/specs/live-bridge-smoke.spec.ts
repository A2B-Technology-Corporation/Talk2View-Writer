/**
 * Live-E2E smoke: BridgeProxy against the real Python BridgeServer
 * inside a running soffice + Talk2View-Writer extension.
 *
 * Skips when ``T2V_E2E_LIVE_SOFFICE_PORT`` isn't set (no live env).
 * Architecture C, scaffold step 5 — proves the soffice + extension
 * + bridge + proxy chain works end-to-end without yet driving the
 * chat UI bundle.
 */
import { test, expect, liveSofficeAvailable } from '../fixtures/live-test-fixtures';

test.skip(
  !liveSofficeAvailable(),
  'T2V_E2E_LIVE_SOFFICE_PORT not set — start soffice with the .oxt + ' +
    'T2V_WRITER_HEADLESS_BRIDGE=1 first, then set this env var to the ' +
    'UNO port (see scripts/start_headless_bridge.py for the protocol).',
);

// Windows skip: bridge_server is AF_UNIX-only — same constraint as
// the other live-routed specs.
test.skip(
  process.platform === 'win32',
  "Python BridgeServer is AF_UNIX-only; live E2E doesn't apply on Windows",
);

test.describe('live bridge smoke (real soffice + extension + bridge)', () => {
  test('list_tools returns the seven MVP tools', async ({ liveBridgeProxy }) => {
    const resp = await fetch(`${liveBridgeProxy.url()}/list_tools`);
    expect(resp.status).toBe(200);
    const body = (await resp.json()) as { result: string[] };
    // _MVP_TOOL_NAMES in bridge_server.py — keep this set in lockstep.
    const expected = new Set([
      'get_document',
      'get_selection',
      'insert_content',
      'format_text',
      'format_paragraph',
      'search_document',
      'manage_preferences',
    ]);
    expect(new Set(body.result)).toEqual(expected);
  });

  test('invoke_tool get_document returns valid JSON for an empty document', async ({
    liveBridgeProxy,
  }) => {
    const resp = await fetch(`${liveBridgeProxy.url()}/invoke_tool`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ name: 'get_document', args: {} }),
    });
    expect(resp.status).toBe(200);
    const body = (await resp.json()) as { result: string };
    expect(typeof body.result).toBe('string');
    // Python tool returns JSON-encoded strings (ADR-0021).
    const doc = JSON.parse(body.result) as {
      paragraphs: Array<{ index: number; text: string; style: string }>;
      total_paragraphs: number;
    };
    expect(Array.isArray(doc.paragraphs)).toBe(true);
    expect(typeof doc.total_paragraphs).toBe('number');
    // Fresh Writer doc from start_headless_bridge.py has one empty
    // paragraph at index 0.
    expect(doc.total_paragraphs).toBeGreaterThanOrEqual(1);
    expect(doc.paragraphs[0].text).toBe('');
  });

  test('invoke_tool unknown name surfaces the MVP-allowlist error', async ({
    liveBridgeProxy,
  }) => {
    const resp = await fetch(`${liveBridgeProxy.url()}/invoke_tool`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ name: 'definitely_not_a_tool', args: {} }),
    });
    expect(resp.status).toBe(500);
    const body = (await resp.json()) as {
      error: { type: string; message: string };
    };
    expect(body.error.message).toMatch(/MVP allowlist|not in/);
  });
});
