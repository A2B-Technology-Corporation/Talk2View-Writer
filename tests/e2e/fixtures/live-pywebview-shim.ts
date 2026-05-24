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
        async proxy_stream_open(url, method, headers, body) {
          // POST to /proxy_stream/open → returns {stream_id}. Then
          // open an EventSource to /proxy_stream/:id/events so the
          // proxy can long-poll the bridge and push events here as
          // they arrive. Events accumulate in a per-stream queue;
          // proxy_stream_next pops from it.
          const open = (await postJson('/proxy_stream/open', {
            url,
            method,
            headers,
            body,
          })) as { stream_id: string };

          type StreamEvent = { type: string; [k: string]: unknown };
          type StreamState = {
            queue: StreamEvent[];
            waiters: Array<(ev: StreamEvent) => void>;
            es: EventSource;
            done: boolean;
          };
          const streams = ((
            window as unknown as { __t2vLiveStreams?: Map<string, StreamState> }
          ).__t2vLiveStreams ??= new Map());

          const state: StreamState = {
            queue: [],
            waiters: [],
            es: new EventSource(`${proxyUrl}/proxy_stream/${open.stream_id}/events`),
            done: false,
          };
          state.es.onmessage = (msg: MessageEvent) => {
            const ev = JSON.parse(msg.data) as StreamEvent;
            const waiter = state.waiters.shift();
            if (waiter) {
              waiter(ev);
            } else {
              state.queue.push(ev);
            }
            if (ev.type === 'done') {
              state.done = true;
              state.es.close();
            }
          };
          state.es.onerror = () => {
            if (state.done) return;
            const errEvt: StreamEvent = {
              type: 'error',
              message: 'EventSource error',
            };
            const waiter = state.waiters.shift();
            if (waiter) waiter(errEvt);
            else state.queue.push(errEvt);
          };
          streams.set(open.stream_id, state);
          return { stream_id: open.stream_id };
        },
        async proxy_stream_next(streamId) {
          type StreamEvent = { type: string; [k: string]: unknown };
          type StreamState = {
            queue: StreamEvent[];
            waiters: Array<(ev: StreamEvent) => void>;
            es: EventSource;
            done: boolean;
          };
          const streams = (
            window as unknown as { __t2vLiveStreams?: Map<string, StreamState> }
          ).__t2vLiveStreams;
          const state = streams?.get(streamId);
          if (!state) {
            return { type: 'error', message: `unknown stream_id ${streamId}` };
          }
          if (state.queue.length > 0) {
            const ev = state.queue.shift()!;
            if (ev.type === 'done') streams!.delete(streamId);
            return ev as Awaited<
              ReturnType<NonNullable<typeof window.pywebview>['api']['proxy_stream_next']>
            >;
          }
          return await new Promise((resolve) => {
            state.waiters.push((ev) => {
              if (ev.type === 'done') streams!.delete(streamId);
              resolve(
                ev as Awaited<
                  ReturnType<
                    NonNullable<typeof window.pywebview>['api']['proxy_stream_next']
                  >
                >,
              );
            });
          });
        },
      },
    };
  })();
}
