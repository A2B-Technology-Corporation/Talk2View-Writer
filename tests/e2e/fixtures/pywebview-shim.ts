/**
 * Browser-side shim that mimics the parts of `window.pywebview.api`
 * the Talk2View-Writer bundle calls in production.
 *
 * In production (the `.oxt`), pywebview injects this object before the
 * bundle's first script tag runs. Under Playwright we use
 * `page.addInitScript(installPywebviewShim)` so the same contract is
 * available before any module evaluates.
 *
 * The shim's `proxy_fetch` rounds-trips back through the page's own
 * `fetch` so the mock engine sees a normal HTTP request (CORS allowed
 * server-side). `invoke_tool` runs a deterministic fake — tests that
 * need to assert on tool invocation override individual entries via
 * `installPywebviewShim({ overrides })`.
 *
 * Keep this in lock-step with `src/talk2view_writer/web_runner.py`'s
 * `_Api` class. If a method name diverges, the bundle stops working.
 * See ADR-0030 and ADR-0031.
 */

export type ShimOverrides = Partial<{
  invoke_tool: (name: string, args: Record<string, unknown>) => unknown;
  list_tools: () => string[];
  log: (level: string, message: string, context?: unknown) => void;
  ping: (message: string) => unknown;
}>;

declare global {
  interface Window {
    pywebview?: {
      api: {
        invoke_tool: (name: string, args: Record<string, unknown>) => Promise<unknown>;
        list_tools: () => Promise<string[]>;
        log: (level: string, message: string, context?: unknown) => Promise<void>;
        ping: (message: string) => Promise<unknown>;
        proxy_fetch: (
          url: string,
          method: string,
          headers: Record<string, string>,
          body: string | null,
        ) => Promise<{
          status: number;
          statusText: string;
          headers: Record<string, string>;
          body: string;
        }>;
      };
    };
    // Test hook so spec code can read what the bundle has logged via
    // `window.pywebview.api.log(...)`.
    __t2vTestLogs?: Array<{ level: string; message: string; context: unknown }>;
    // Test hook for invoke_tool — every call appended here.
    __t2vToolCalls?: Array<{ name: string; args: Record<string, unknown> }>;
  }
}

/**
 * Returns the JS source the shim injects into the page via
 * `page.addInitScript`. Playwright stringifies the function and runs
 * it in the page context, so this file's imports never reach the
 * browser — only the body of `installPywebviewShim`.
 *
 * The shim runs once per page load. Idempotent: a second call is
 * a no-op so reload-based tests don't double-install.
 */
export function installPywebviewShim(opts: { overrides?: ShimOverrides } = {}): void {
  // Use IIFE so the shim doesn't pollute the page's module scope.
  (function install() {
    if (window.pywebview) return;

    window.__t2vTestLogs = [];
    window.__t2vToolCalls = [];

    const overrides = opts.overrides ?? {};

    window.pywebview = {
      api: {
        async invoke_tool(name, args) {
          window.__t2vToolCalls!.push({ name, args });
          if (overrides.invoke_tool) return overrides.invoke_tool(name, args);
          // Default stubs for the five MVP tools. Mirror the JSON shape
          // each Python tool returns so the bundle's tool-call render
          // path exercises real branches.
          switch (name) {
            case 'get_document':
              return JSON.stringify({ paragraphs: [], total_paragraphs: 0 });
            case 'get_selection':
              return JSON.stringify({ selection: '' });
            case 'insert_content':
              return JSON.stringify({ inserted: true });
            case 'format_text':
              return JSON.stringify({ matches: 0 });
            case 'search_document':
              return JSON.stringify({ matches: 0 });
            default:
              return JSON.stringify({ ok: true });
          }
        },
        async list_tools() {
          return (
            overrides.list_tools?.() ?? [
              'get_document',
              'get_selection',
              'insert_content',
              'format_text',
              'search_document',
            ]
          );
        },
        async log(level, message, context) {
          window.__t2vTestLogs!.push({ level, message, context });
          overrides.log?.(level, message, context);
        },
        async ping(message) {
          return overrides.ping?.(message) ?? { echo: message, from: 'shim' };
        },
        async proxy_fetch(url, method, headers, body) {
          // Round-trip through the page's own fetch — the mock engine
          // sits on a sibling localhost port and accepts CORS, so this
          // succeeds without the production WebKitGTK workaround.
          const resp = await fetch(url, {
            method,
            headers,
            body: method === 'GET' || method === 'HEAD' ? undefined : body ?? undefined,
          });
          const text = await resp.text();
          const respHeaders: Record<string, string> = {};
          resp.headers.forEach((v, k) => {
            respHeaders[k.toLowerCase()] = v;
          });
          return {
            status: resp.status,
            statusText: resp.statusText,
            headers: respHeaders,
            body: text,
          };
        },
      },
    };
  })();
}
