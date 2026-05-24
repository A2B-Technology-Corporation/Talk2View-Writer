/**
 * Browser-side shim that proxies `window.pywebview.api.*` calls to
 * the Node bridge-proxy via HTTP.
 *
 * Sibling to ``pywebview-shim.ts`` (mock-engine shim). The mock shim
 * intercepts calls and returns canned responses locally. This live
 * shim forwards every call to a Node bridge-proxy whose other end
 * routes to the real Python BridgeServer running inside soffice — so
 * the chat UI drives real tools that mutate the real Writer document.
 *
 * The shim runs in the page context via ``page.addInitScript`` so it
 * is installed before the bundle's first script tag evaluates. The
 * proxyUrl is passed as the second arg to addInitScript so it's
 * bound at install time (the shim's function body is stringified by
 * Playwright and the closure isn't reachable).
 */

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
        proxy_stream_open: (
          url: string,
          method: string,
          headers: Record<string, string>,
          body: string | null,
        ) => Promise<{ stream_id: string }>;
        proxy_stream_next: (streamId: string) => Promise<
          | { type: 'headers'; status: number; statusText: string; headers: Record<string, string> }
          | { type: 'chunk'; data: string }
          | { type: 'done' }
          | { type: 'error'; message: string }
          | { type: 'timeout' }
        >;
      };
    };
    __t2vTestLogs?: Array<{ level: string; message: string; context: unknown }>;
    __t2vToolCalls?: Array<{ name: string; args: Record<string, unknown> }>;
  }
}

export type LiveShimOpts = {
  /** Base URL of the Node bridge-proxy, e.g. ``http://127.0.0.1:43123``. */
  proxyUrl: string;
};

/**
 * Returns the JS source the live shim injects via
 * ``page.addInitScript(installLivePywebviewShim, opts)``.
 *
 * Idempotent: re-running is a no-op so reload-based tests don't
 * double-install. Every invoke_tool call is appended to
 * ``window.__t2vToolCalls`` so specs can assert on the tool
 * sequence without re-listening to the bridge.
 */
export function installLivePywebviewShim(opts: LiveShimOpts): void {
  (function install() {
    if (window.pywebview) return;
    const { proxyUrl } = opts;
    window.__t2vTestLogs = [];
    window.__t2vToolCalls = [];

    async function postJson(path: string, body: unknown): Promise<unknown> {
      const resp = await fetch(`${proxyUrl}${path}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (resp.status === 204) return null;
      const text = await resp.text();
      // bridge-proxy always returns JSON for 200/500.
      const parsed = JSON.parse(text) as
        | { result: unknown }
        | { error: { type: string; message: string } };
      if ('error' in parsed) {
        throw new Error(
          `${parsed.error.type}: ${parsed.error.message}`,
        );
      }
      return parsed.result;
    }

    async function getJson(path: string): Promise<unknown> {
      const resp = await fetch(`${proxyUrl}${path}`);
      const text = await resp.text();
      const parsed = JSON.parse(text) as
        | { result: unknown }
        | { error: { type: string; message: string } };
      if ('error' in parsed) {
        throw new Error(
          `${parsed.error.type}: ${parsed.error.message}`,
        );
      }
      return parsed.result;
    }

    window.pywebview = {
      api: {
        async invoke_tool(name, args) {
          window.__t2vToolCalls!.push({ name, args });
          return await postJson('/invoke_tool', { name, args });
        },
        async list_tools() {
          return (await getJson('/list_tools')) as string[];
        },
        async log(level, message, context) {
          window.__t2vTestLogs!.push({ level, message, context });
          await postJson('/log', { level, message, context: context ?? null });
        },
        async ping(message) {
          // ping isn't routed to the bridge (the BridgeServer has no
          // ping method). It's a debug round-trip; return a synthetic
          // echo so the bundle's diagnostic hooks don't break.
          return { echo: message, from: 'live-shim' };
        },
        async proxy_fetch(url, method, headers, body) {
          return (await postJson('/proxy_fetch', {
            url,
            method,
            headers,
            body,
          })) as {
            status: number;
            statusText: string;
            headers: Record<string, string>;
            body: string;
          };
        },
        async proxy_stream_open(_url, _method, _headers, _body) {
          // Streaming added in scaffold step 5 (SSE between proxy
          // and shim). Until then, return a clear error so the
          // bundle's stream consumer can route it to its error path.
          throw new Error('proxy_stream_open not yet implemented in live shim');
        },
        async proxy_stream_next(_streamId) {
          throw new Error('proxy_stream_next not yet implemented in live shim');
        },
      },
    };
  })();
}
