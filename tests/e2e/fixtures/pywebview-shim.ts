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
        open_external: (url: string) => Promise<{ opened: boolean }>;
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
    // Test hook so spec code can read what the bundle has logged via
    // `window.pywebview.api.log(...)`.
    __t2vTestLogs?: Array<{ level: string; message: string; context: unknown }>;
    // Test hook for invoke_tool — every call appended here.
    __t2vToolCalls?: Array<{ name: string; args: Record<string, unknown> }>;
    // Test hook for open_external — every opened URL appended here.
    __t2vExternalOpens?: string[];
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
    window.__t2vExternalOpens = [];

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
        async open_external(url) {
          window.__t2vExternalOpens!.push(url);
          return { opened: true };
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
        async proxy_stream_open(url, method, headers, body) {
          // Open a real streaming fetch against the mock engine.
          // The shim drains the response body in the background and
          // feeds chunks into a queue read by proxy_stream_next.
          // Each call to proxy_stream_open creates an independent
          // stream id; cleanup happens after the consumer reads
          // ``done``.
          const streams = ((window as unknown as {
            __t2vMockStreams?: Map<
              string,
              { queue: Array<{ type: string; [k: string]: unknown }>; waiters: Array<(v: unknown) => void> }
            >;
          }).__t2vMockStreams ??= new Map());
          const streamId = `s-${Math.random().toString(36).slice(2)}-${Date.now()}`;
          const state: {
            queue: Array<{ type: string; [k: string]: unknown }>;
            waiters: Array<(v: unknown) => void>;
          } = { queue: [], waiters: [] };
          streams.set(streamId, state);
          const push = (ev: { type: string; [k: string]: unknown }): void => {
            const w = state.waiters.shift();
            if (w) {
              w(ev);
            } else {
              state.queue.push(ev);
            }
          };
          // Drain in the background.
          void (async () => {
            try {
              const resp = await fetch(url, {
                method,
                headers,
                body: method === 'GET' || method === 'HEAD' ? undefined : body ?? undefined,
              });
              const respHeaders: Record<string, string> = {};
              resp.headers.forEach((v, k) => {
                respHeaders[k.toLowerCase()] = v;
              });
              push({
                type: 'headers',
                status: resp.status,
                statusText: resp.statusText,
                headers: respHeaders,
              });
              if (resp.body) {
                const reader = resp.body.getReader();
                const decoder = new TextDecoder();
                while (true) {
                  const { done, value } = await reader.read();
                  if (done) break;
                  if (value && value.byteLength > 0) {
                    push({ type: 'chunk', data: decoder.decode(value, { stream: true }) });
                  }
                }
              }
            } catch (err) {
              push({ type: 'error', message: (err as Error).message });
            } finally {
              push({ type: 'done' });
            }
          })();
          return { stream_id: streamId };
        },
        async proxy_stream_next(streamId) {
          const streams = (window as unknown as {
            __t2vMockStreams?: Map<
              string,
              { queue: Array<{ type: string; [k: string]: unknown }>; waiters: Array<(v: unknown) => void> }
            >;
          }).__t2vMockStreams;
          const state = streams?.get(streamId);
          if (!state) return { type: 'error', message: `unknown stream_id ${streamId}` };
          if (state.queue.length > 0) {
            const ev = state.queue.shift()!;
            if (ev.type === 'done') streams!.delete(streamId);
            return ev as Awaited<ReturnType<NonNullable<typeof window.pywebview>['api']['proxy_stream_next']>>;
          }
          return await new Promise((resolve) => {
            state.waiters.push((ev) => {
              const event = ev as { type: string };
              if (event.type === 'done') streams!.delete(streamId);
              resolve(ev as Awaited<ReturnType<NonNullable<typeof window.pywebview>['api']['proxy_stream_next']>>);
            });
          });
        },
      },
    };
  })();
}
