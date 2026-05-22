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
