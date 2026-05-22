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
      if (api) {
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
 * Forward a log line to LO's rotating log via the bridge.
 *
 * Best-effort and async. Buffers calls made before the bridge is
 * ready and drains them once it is, so log lines emitted by
 * import-time SDK init or React's first render aren't lost.
 */
type LogLevel = 'debug' | 'info' | 'warning' | 'error';

const _logBuffer: Array<{ level: LogLevel; message: string; context?: unknown }> = [];
let _logFlushStarted = false;

export function logToHost(level: LogLevel, message: string, context?: unknown): void {
  _logBuffer.push({ level, message, context });
  if (!_logFlushStarted) {
    _logFlushStarted = true;
    whenBridgeReady().then(
      async (api) => {
        // Drain on the microtask queue so we never block the main
        // thread with chains of awaits on a slow socket.
        while (_logBuffer.length > 0) {
          const entry = _logBuffer.shift()!;
          try {
            await api.log(entry.level, entry.message, entry.context ?? null);
          } catch {
            // Swallow — losing a single log line is preferable to a
            // cascading promise rejection that breaks the UI.
          }
        }
      },
      () => {
        // Bridge never became ready. Drop the buffer; the local
        // console already has the lines.
        _logBuffer.length = 0;
      },
    );
  }
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

  logToHost('info', '[bridge] host logging installed');
}
