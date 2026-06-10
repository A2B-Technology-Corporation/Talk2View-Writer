/**
 * JS↔Python bridge for Talk2View-Writer.
 *
 * The pywebview subprocess (talk2view_writer.web_runner) injects an
 * ``Api`` object into the page as ``window.pywebview.api`` once the
 * page finishes loading. Each method on that object is callable as
 * an async function that resolves to the value the Python side
 * returned (or rejects if Python raised).
 *
 * ``invokeTool`` proxies a single tool call back to LibreOffice's
 * Python over a Unix-socket JSON-RPC bridge; the Python side
 * dispatches to the matching @ui_thread_tool function which
 * marshals the UNO calls onto LO's UI thread.
 */

interface PywebviewApi {
  ping(message: string): Promise<{ echo: string; from: string }>;
  list_tools(): Promise<string[]>;
  invoke_tool(
    name: string,
    args: Record<string, unknown>,
  ): Promise<unknown>;
  log(
    level: string,
    message: string,
    context: unknown,
  ): Promise<null>;
  open_external(url: string): Promise<{ opened: boolean }>;
  proxy_fetch(
    url: string,
    method: string,
    headers: Record<string, string>,
    body: string | null,
  ): Promise<{
    status: number;
    statusText: string;
    headers: Record<string, string>;
    body: string;
  }>;
  proxy_stream_open(
    url: string,
    method: string,
    headers: Record<string, string>,
    body: string | null,
  ): Promise<{ stream_id: string }>;
  proxy_stream_next(stream_id: string): Promise<
    | { type: 'headers'; status: number; statusText: string; headers: Record<string, string> }
    | { type: 'chunk'; data: string }
    | { type: 'done' }
    | { type: 'error'; message: string }
    | { type: 'timeout' }
  >;
}

declare global {
  interface Window {
    pywebview?: { api?: PywebviewApi };
  }
}

const READY_TIMEOUT_MS = 10_000;
const POLL_INTERVAL_MS = 50;

let _readyPromise: Promise<PywebviewApi> | null = null;

/**
 * Resolve when ``window.pywebview.api`` is injected.
 *
 * pywebview injects the bridge object after the page's ``load`` event
 * fires but before any user interaction is possible. We poll briefly
 * because the SDK may try to call tools immediately on mount.
 */
export function whenBridgeReady(): Promise<PywebviewApi> {
  if (_readyPromise) return _readyPromise;
  _readyPromise = new Promise((resolve, reject) => {
    const t0 = Date.now();
    const tick = () => {
      const api = window.pywebview?.api;
      // A truthy ``api`` is NOT enough: pywebview can inject
      // ``window.pywebview.api`` a beat before all of its methods are
      // attached. SDK >=0.10.0 fetches the partner config eagerly the
      // instant <Talk2View> mounts (usePartnerConfig), which raced
      // ``proxy_fetch`` being undefined and threw "i.proxy_fetch is not a
      // function" (investigation #54). Wait until the methods we actually
      // call are present so an early proxied request can't see a partial
      // bridge.
      if (
        api &&
        typeof api.proxy_fetch === 'function' &&
        typeof api.proxy_stream_open === 'function' &&
        typeof api.proxy_stream_next === 'function' &&
        typeof api.invoke_tool === 'function'
      ) {
        resolve(api);
        return;
      }
      if (Date.now() - t0 > READY_TIMEOUT_MS) {
        reject(
          new Error(
            `window.pywebview.api not available after ${READY_TIMEOUT_MS}ms — ` +
              'is the page hosted in the pywebview subprocess?',
          ),
        );
        return;
      }
      setTimeout(tick, POLL_INTERVAL_MS);
    };
    tick();
  });
  return _readyPromise;
}

/**
 * Invoke a tool by name with JSON-serialisable args. Returns the
 * tool's return value (typically a JSON string per ADR-0021 on the
 * Python side — the SDK passes that straight to the engine).
 */
export async function invokeTool(
  name: string,
  args: Record<string, unknown>,
): Promise<unknown> {
  const api = await whenBridgeReady();
  return api.invoke_tool(name, args);
}

/**
 * Open an external http(s) URL in the user's default browser.
 *
 * Goes through the pywebview host because JS ``window.open`` is a no-op
 * in the WebKitGTK/Cocoa/EdgeChromium webview the chat runs in. Falls
 * back to ``window.open`` when there's no host bridge (e.g. a plain
 * browser during tests). Best-effort — never throws.
 */
export async function openExternal(url: string): Promise<void> {
  try {
    const api = await whenBridgeReady();
    await api.open_external(url);
  } catch (err) {
    logToHost('debug', `[open_external] host open failed, falling back: ${String(err)}`);
    window.open(url, '_blank', 'noopener');
  }
}

/**
 * Forward a log line to LO's rotating log via the bridge.
 *
 * Best-effort and async. Buffers calls made before the bridge is
 * ready and drains them once it is, so log lines emitted by
 * import-time SDK init or React's first render aren't lost.
 */
type LogLevel = 'debug' | 'info' | 'warning' | 'error';

const _logBuffer: Array<{ level: LogLevel; message: string; context?: unknown }> = [];
// One drain pump runs at a time. Each call to logToHost pushes to the
// buffer, and if the pump is idle we wake it. A previous version
// latched _drainStarted permanently after the first drain finished —
// new logs added afterward stayed in the buffer forever. Caught by
// the Playwright smoke spec on 2026-05-22: the bridge in the test
// shim is instant, so the first drain emptied the buffer before
// React's useEffects had a chance to push their logs.
let _drainInFlight = false;
let _bridgeUnavailable = false;

async function _pumpLogBuffer(): Promise<void> {
  if (_drainInFlight || _bridgeUnavailable) return;
  _drainInFlight = true;
  try {
    let api: PywebviewApi;
    try {
      api = await whenBridgeReady();
    } catch {
      // Bridge never became ready. Drop the current buffer and stop
      // retrying so we don't leak unbounded log entries.
      _logBuffer.length = 0;
      _bridgeUnavailable = true;
      return;
    }
    while (_logBuffer.length > 0) {
      const entry = _logBuffer.shift()!;
      try {
        await api.log(entry.level, entry.message, entry.context ?? null);
      } catch {
        // Swallow — losing a single log line is preferable to a
        // cascading promise rejection that breaks the UI.
      }
    }
  } finally {
    _drainInFlight = false;
  }
  // A log might have arrived during the await — re-pump if so.
  if (_logBuffer.length > 0) {
    void _pumpLogBuffer();
  }
}

export function logToHost(level: LogLevel, message: string, context?: unknown): void {
  _logBuffer.push({ level, message, context });
  void _pumpLogBuffer();
}

/**
 * Install global hooks that route console.* + uncaught errors to LO.
 *
 * - console.{log,info,warn,error} are mirrored to logToHost. The
 *   original console method is still called so the webview's
 *   DevTools (if open) shows the same output.
 * - window.error and window.unhandledrejection are intercepted and
 *   logged at error level.
 *
 * Call this once at the very start of main() so we capture errors
 * from React mount + SDK init.
 */
export function installHostLogging(): void {
  const origLog = console.log.bind(console);
  const origInfo = console.info.bind(console);
  const origWarn = console.warn.bind(console);
  const origError = console.error.bind(console);
  const origDebug = console.debug?.bind(console) ?? origLog;

  function _fmt(args: unknown[]): string {
    return args
      .map((a) => {
        if (a instanceof Error) return `${a.name}: ${a.message}\n${a.stack ?? ''}`;
        if (typeof a === 'string') return a;
        try {
          return JSON.stringify(a);
        } catch {
          return String(a);
        }
      })
      .join(' ');
  }

  console.log = (...args: unknown[]) => {
    origLog(...args);
    logToHost('info', `[console.log] ${_fmt(args)}`);
  };
  console.info = (...args: unknown[]) => {
    origInfo(...args);
    logToHost('info', `[console.info] ${_fmt(args)}`);
  };
  console.warn = (...args: unknown[]) => {
    origWarn(...args);
    logToHost('warning', `[console.warn] ${_fmt(args)}`);
  };
  console.error = (...args: unknown[]) => {
    origError(...args);
    logToHost('error', `[console.error] ${_fmt(args)}`);
  };
  console.debug = (...args: unknown[]) => {
    origDebug(...args);
    logToHost('debug', `[console.debug] ${_fmt(args)}`);
  };

  window.addEventListener('error', (e) => {
    const err = e.error;
    const detail = err instanceof Error
      ? { name: err.name, message: err.message, stack: err.stack }
      : { message: String(e.message), source: e.filename, line: e.lineno };
    logToHost('error', `[window.error] ${detail.message ?? e.message}`, detail);
  });

  window.addEventListener('unhandledrejection', (e) => {
    const reason = e.reason;
    const detail = reason instanceof Error
      ? { name: reason.name, message: reason.message, stack: reason.stack }
      : { reason: String(reason) };
    logToHost(
      'error',
      `[unhandledrejection] ${reason instanceof Error ? reason.message : String(reason)}`,
      detail,
    );
  });

  // Instrument fetch + XHR so every network request is captured.
  // Critical for diagnosing CORS / network failures that don't fire
  // console.error (browsers often reject silently). Also routes
  // engine.talk2view.com requests through the Python bridge to
  // bypass WebKit's file://-origin CORS (see ENGINE_HOST below).
  const origFetch = window.fetch.bind(window);
  let fetchSeq = 0;

  // The webview is loaded via ``file://``. WebKit gates cross-origin
  // responses on the engine's ``Access-Control-Allow-Origin`` header,
  // which doesn't allow ``null`` / ``file://``. Requests fire but
  // responses are silently dropped. We sidestep by routing engine
  // calls through Python's httpx via the bridge (no browser CORS
  // rules). Non-streaming endpoints only for now — chat streaming
  // (SSE) needs a separate mechanism (next iteration).
  const ENGINE_HOST = 'engine.talk2view.com';

  async function _headersToObj(init?: RequestInit): Promise<Record<string, string>> {
    const out: Record<string, string> = {};
    const h = init?.headers;
    if (!h) return out;
    if (h instanceof Headers) {
      h.forEach((v, k) => { out[k] = v; });
    } else if (Array.isArray(h)) {
      for (const [k, v] of h) out[k] = String(v);
    } else {
      for (const [k, v] of Object.entries(h)) out[k] = String(v);
    }
    return out;
  }

  function _arrayBufferToBase64(buf: ArrayBuffer): string {
    const bytes = new Uint8Array(buf);
    let binary = '';
    // Chunk to avoid a call-stack overflow on large audio blobs.
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
  }

  async function _bodyToString(init?: RequestInit): Promise<string | null> {
    const b = init?.body;
    if (b == null) return null;
    if (typeof b === 'string') return b;
    if (b instanceof URLSearchParams) return b.toString();
    // FormData (the SDK's speech-to-text upload: an audio file + a model
    // field). Raw multipart can't cross the JSON bridge, so serialise a
    // sentinel envelope that bridge_server._proxy_fetch rebuilds into a real
    // multipart request (investigations #59). Must precede the Blob branch.
    if (typeof FormData !== 'undefined' && b instanceof FormData) {
      const fields: Array<{ name: string; value: string }> = [];
      const files: Array<{ name: string; filename: string; type: string; b64: string }> = [];
      for (const [name, value] of (b as FormData).entries()) {
        if (typeof value === 'string') {
          fields.push({ name, value });
        } else {
          const buf = await value.arrayBuffer();
          files.push({
            name,
            filename: (value as File).name || 'blob',
            type: value.type || 'application/octet-stream',
            b64: _arrayBufferToBase64(buf),
          });
        }
      }
      return JSON.stringify({ __t2v_multipart__: true, fields, files });
    }
    if (b instanceof Blob) return await b.text();
    if (b instanceof ArrayBuffer) return new TextDecoder().decode(b);
    if (ArrayBuffer.isView(b)) {
      return new TextDecoder().decode(b as Uint8Array);
    }
    // ReadableStream etc. — fall back to String() and log a warning so we
    // know if this hits.
    logToHost(
      'warning',
      `[fetch] unrecognised body type ${b.constructor?.name ?? typeof b}; serialising via String()`,
    );
    return String(b);
  }

  function _shouldProxy(url: string): boolean {
    try {
      return new URL(url, window.location.href).hostname === ENGINE_HOST;
    } catch {
      return false;
    }
  }

  /**
   * Stream a proxied request chunk-by-chunk back to the SDK.
   *
   * The bridge's ``proxy_stream_open`` returns a ``stream_id`` and
   * starts an httpx worker that pushes events into a queue: one
   * ``headers`` event, zero or more ``chunk`` events, then ``done``
   * (with an optional ``error`` immediately before done on failure).
   * We drain via ``proxy_stream_next`` polls and re-shape the events
   * into a ``ReadableStream`` body so the SDK can use ``response.body``
   * exactly as it would with a real network fetch.
   *
   * Latency: each ``proxy_stream_next`` is one Unix-socket round-trip
   * (~1-2 ms), so chunks arrive in JS within a few ms of being read
   * from the engine. Good enough for token-by-token rendering.
   */
  async function _proxyStream(
    api: PywebviewApi,
    reqId: number,
    t0: number,
    url: string,
    method: string,
    headers: Record<string, string>,
    body: string | null,
  ): Promise<Response> {
    const { stream_id } = await api.proxy_stream_open(url, method, headers, body);

    // Pump the first event — it must be ``headers``, ``error``, or
    // ``done`` (engine could close immediately).
    let first = await api.proxy_stream_next(stream_id);
    while (first.type === 'timeout') {
      first = await api.proxy_stream_next(stream_id);
    }

    if (first.type === 'error') {
      logToHost(
        'error',
        `[fetch:${reqId}] (proxy-stream) !! ${first.message} (${Date.now() - t0}ms) ${url}`,
      );
      // Drain the trailing done.
      while ((await api.proxy_stream_next(stream_id)).type !== 'done') {
        // loop
      }
      throw new Error(`Bridge stream error: ${first.message}`);
    }
    if (first.type === 'done') {
      logToHost(
        'warning',
        `[fetch:${reqId}] (proxy-stream) ← empty (closed before headers) (${Date.now() - t0}ms) ${url}`,
      );
      return new Response('', { status: 502, statusText: 'Bridge: empty stream' });
    }
    if (first.type !== 'headers') {
      throw new Error(`Bridge stream: unexpected first event type ${first.type}`);
    }

    const { status, statusText, headers: respHeaders } = first;
    logToHost('info', `[fetch:${reqId}] (proxy-stream) ⇡ ${status} ${statusText} (${Date.now() - t0}ms) ${url}`, {
      headerKeys: Object.keys(respHeaders),
    });

    let chunkCount = 0;
    let totalBytes = 0;
    const encoder = new TextEncoder();

    const responseBody = new ReadableStream<Uint8Array>({
      async pull(controller) {
        while (true) {
          const ev = await api.proxy_stream_next(stream_id);
          if (ev.type === 'timeout') {
            continue;
          }
          if (ev.type === 'chunk') {
            chunkCount += 1;
            const encoded = encoder.encode(ev.data);
            totalBytes += encoded.byteLength;
            controller.enqueue(encoded);
            return;
          }
          if (ev.type === 'error') {
            logToHost(
              'error',
              `[fetch:${reqId}] (proxy-stream) mid-stream error: ${ev.message}`,
            );
            controller.error(new Error(ev.message));
            return;
          }
          if (ev.type === 'done') {
            logToHost(
              'info',
              `[fetch:${reqId}] (proxy-stream) ⇣ closed ${chunkCount}ch ${totalBytes}B (${Date.now() - t0}ms) ${url}`,
            );
            controller.close();
            return;
          }
          throw new Error(`Bridge stream: unexpected event type ${(ev as { type: string }).type}`);
        }
      },
    });

    return new Response(responseBody, {
      status,
      statusText,
      headers: respHeaders,
    });
  }

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const reqId = ++fetchSeq;
    const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
    const method = (init?.method ?? (typeof input !== 'string' && !(input instanceof URL) ? input.method : 'GET')) || 'GET';

    // Never log request headers — they carry the Authorization bearer
    // JWT and partner key. (The Python log boundary also redacts these,
    // but don't emit them to the devtools console in the first place.)
    logToHost('info', `[fetch:${reqId}] → ${method} ${url}`, {
      bodyType: init?.body ? typeof init.body : null,
      proxied: _shouldProxy(url),
    });
    const t0 = Date.now();

    if (_shouldProxy(url)) {
      // Routes that stream (chat completion is SSE) need chunk-by-chunk
      // delivery so the UI sees text as the model emits it. We pick
      // the streaming path when the caller declared
      // ``Accept: text/event-stream`` OR when the URL is one of the
      // SDK's known streaming endpoints. The platform streams both
      // ``/v1/sessions/{id}/messages`` AND ``/v1/sessions/{id}/resume``
      // — both return a long-lived SSE response. Routing /resume
      // through proxy_fetch made the LLM-thinking pause on a
      // multi-step plan look like an httpx.ReadTimeout (Investigation
      // #41). Everything else uses the non-streaming proxy_fetch.
      const hdrs = await _headersToObj(init);
      const accept = (hdrs.Accept || hdrs.accept || '').toLowerCase();
      const sessionStreamPath =
        /\/v1\/sessions\/[^/]+\/(messages|resume)$/;
      const isStreaming =
        accept.includes('text/event-stream') ||
        sessionStreamPath.test(new URL(url).pathname);
      try {
        const api = await whenBridgeReady();
        const bodyStr = await _bodyToString(init);
        if (isStreaming) {
          return await _proxyStream(api, reqId, t0, url, method, hdrs, bodyStr);
        }
        const result = await api.proxy_fetch(url, method, hdrs, bodyStr);
        logToHost(
          result.status >= 400 || result.status === 0 ? 'error' : 'info',
          `[fetch:${reqId}] (proxy) ← ${result.status} ${result.statusText} (${Date.now() - t0}ms) ${url}`,
          {
            bodyPreview: result.body.slice(0, 200),
            headerKeys: Object.keys(result.headers),
          },
        );
        // Synthesise a Response. Headers passed through verbatim
        // so the SDK can read Content-Type, etc.
        return new Response(result.body, {
          status: result.status || 502,
          statusText: result.statusText || (result.status === 0 ? 'Bridge Error' : ''),
          headers: result.headers,
        });
      } catch (err) {
        const e = err as Error;
        logToHost(
          'error',
          `[fetch:${reqId}] (proxy) !! ${e.name}: ${e.message} (${Date.now() - t0}ms) ${url}`,
          { name: e.name, message: e.message, stack: e.stack },
        );
        throw err;
      }
    }

    try {
      const resp = await origFetch(input, init);
      logToHost('info', `[fetch:${reqId}] ← ${resp.status} ${resp.statusText} (${Date.now() - t0}ms) ${url}`, {
        ok: resp.ok,
        type: resp.type,
        redirected: resp.redirected,
      });
      return resp;
    } catch (err) {
      const e = err as Error;
      logToHost('error', `[fetch:${reqId}] !! ${e.name}: ${e.message} (${Date.now() - t0}ms) ${url}`, {
        name: e.name,
        message: e.message,
        stack: e.stack,
      });
      throw err;
    }
  };

  const OrigXHR = window.XMLHttpRequest;
  let xhrSeq = 0;
  function PatchedXHR(this: XMLHttpRequest) {
    const xhr = new OrigXHR();
    const reqId = ++xhrSeq;
    let method = 'GET';
    let url = '';
    const origOpen = xhr.open.bind(xhr);
    xhr.open = function (m: string, u: string | URL, ...rest: unknown[]) {
      method = m;
      url = typeof u === 'string' ? u : u.toString();
      logToHost('info', `[xhr:${reqId}] open ${method} ${url}`);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      return (origOpen as any)(m, u, ...rest);
    };
    const origSend = xhr.send.bind(xhr);
    xhr.send = function (body?: Document | XMLHttpRequestBodyInit | null) {
      const t0 = Date.now();
      logToHost('info', `[xhr:${reqId}] send ${method} ${url}`, { bodyType: body ? typeof body : null });
      xhr.addEventListener('loadend', () => {
        logToHost(
          xhr.status >= 400 || xhr.status === 0 ? 'error' : 'info',
          `[xhr:${reqId}] ← ${xhr.status} (${Date.now() - t0}ms) ${url}`,
        );
      });
      xhr.addEventListener('error', () => {
        logToHost('error', `[xhr:${reqId}] !! network error ${url}`);
      });
      return origSend(body);
    };
    return xhr;
  }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  window.XMLHttpRequest = PatchedXHR as any;

  logToHost('info', '[bridge] host logging installed (fetch + xhr + console + errors)');
}
