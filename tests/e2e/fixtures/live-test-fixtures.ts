/**
 * Live-E2E Playwright fixtures.
 *
 * Orchestrate a real soffice + extension + bridge_server stack and
 * expose a connected BridgeProxy to the test. Architecture C, see
 * ADR-0036 forthcoming. The MockBridge-based fixtures in
 * test-fixtures.ts stay independent so the bulk of unit-style E2E
 * specs don't need a soffice.
 *
 * Pre-conditions (set by the calling CI step / dev workflow):
 *
 *   - soffice is already running with
 *     ``--accept=socket,host=127.0.0.1,port=NNNN;urp;`` and the
 *     Talk2View-Writer .oxt installed in the soffice profile.
 *   - ``T2V_WRITER_HEADLESS_BRIDGE=1`` is in soffice's environment
 *     so ``WebWindow.show()`` starts the bridge but does NOT spawn
 *     pywebview. The Node BridgeProxy will then own the single
 *     bridge connection.
 *   - ``T2V_E2E_LIVE_SOFFICE_PORT`` env var points at the UNO port
 *     (default 2002). If unset, every live test in the spec skips
 *     so PRs from forks (without soffice setup) don't fail.
 *
 * The fixture spawns ``scripts/start_headless_bridge.py``, which
 * uses python3-uno to dispatch the chat-open menu URL. The
 * extension instantiates the bridge; the helper scrapes
 * ``talk2view.log`` for the ``BridgeServer.start: listening on …``
 * line and returns the socket path on stdout.
 */
import { test as base } from '@playwright/test';
import { execFile } from 'child_process';
import { createServer as createHttp, Server, IncomingMessage, ServerResponse } from 'http';
import { readFile } from 'fs/promises';
import { extname, join, normalize, resolve } from 'path';
import { promisify } from 'util';

import { BridgeProxy } from './bridge-proxy';

const execFileP = promisify(execFile);

type LiveFixtures = {
  liveBridgeProxy: BridgeProxy;
  liveBundleServer: StaticBundleServer;
};

const REPO_ROOT = resolve(__dirname, '../../..');
const BUNDLE_DIR = resolve(REPO_ROOT, 'src/web/dist');

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.wasm': 'application/wasm',
};

/**
 * Minimal localhost static server for the chat-UI bundle. Different
 * from MockEngine: that one also intercepts /v1/* engine requests.
 * Live tests need the bundle served + the engine requests passing
 * through the live shim → bridge-proxy → real engine.
 */
export class StaticBundleServer {
  private http: Server | null = null;

  url(): string {
    if (!this.http) throw new Error('StaticBundleServer not started');
    const addr = this.http.address();
    if (!addr || typeof addr === 'string') throw new Error('no address');
    return `http://127.0.0.1:${addr.port}`;
  }

  async start(): Promise<void> {
    await new Promise<void>((resolveStart, reject) => {
      this.http = createHttp((req, res) => this.handle(req, res));
      this.http.once('error', reject);
      this.http.listen(0, '127.0.0.1', () => {
        this.http!.removeListener('error', reject);
        resolveStart();
      });
    });
  }

  async stop(): Promise<void> {
    if (!this.http) return;
    this.http.closeAllConnections?.();
    await new Promise<void>((r) => this.http!.close(() => r()));
    this.http = null;
  }

  private async handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    let urlPath = (req.url ?? '/').split('?', 1)[0];
    if (urlPath === '/') urlPath = '/index.html';
    const absolute = normalize(join(BUNDLE_DIR, urlPath));
    if (!absolute.startsWith(BUNDLE_DIR)) {
      res.statusCode = 403;
      res.end('forbidden');
      return;
    }
    try {
      const buf = await readFile(absolute);
      res.setHeader('content-type', MIME[extname(absolute)] ?? 'application/octet-stream');
      res.statusCode = 200;
      res.end(buf);
    } catch {
      res.statusCode = 404;
      res.end('not found');
    }
  }
}

/**
 * True when ``T2V_E2E_LIVE_SOFFICE_PORT`` is set to a valid port.
 * Specs use this with ``test.skip(!liveSofficeAvailable(), …)`` so
 * forks / dev runs without soffice no-op cleanly instead of failing.
 */
export function liveSofficeAvailable(): boolean {
  const raw = process.env.T2V_E2E_LIVE_SOFFICE_PORT;
  if (!raw) return false;
  const port = parseInt(raw, 10);
  return Number.isFinite(port) && port > 0;
}

/** Resolved UNO port — caller should have already checked
 * ``liveSofficeAvailable()``. */
function sofficePort(): number {
  return parseInt(process.env.T2V_E2E_LIVE_SOFFICE_PORT!, 10);
}

/**
 * Pick a Python interpreter that can ``import uno``. The helper needs
 * python3-uno's wrappers + the C bridge to dispatch the menu URL.
 *
 *   - ``T2V_E2E_PYTHON`` overrides explicitly (preferred in CI).
 *   - Otherwise default to ``/usr/bin/python3`` on Linux because the
 *     apt python3-uno package installs into ``/usr/lib/python3/dist-
 *     packages`` and the system Python is the one with the bridge.
 *     A bare ``python3`` from PATH inside an active venv would miss
 *     uno unless that venv has uno symlinked (CI does this; local
 *     dev usually doesn't).
 */
function unoCapablePython(): string {
  const override = process.env.T2V_E2E_PYTHON;
  if (override) return override;
  return '/usr/bin/python3';
}

/**
 * Run ``scripts/start_headless_bridge.py`` against the given UNO port
 * and return the bridge socket path it prints to stdout.
 */
async function startHeadlessBridge(port: number): Promise<string> {
  const { stdout } = await execFileP(
    unoCapablePython(),
    [
      resolve(REPO_ROOT, 'scripts/start_headless_bridge.py'),
      '--port',
      String(port),
    ],
    { timeout: 60_000 },
  );
  const socketPath = stdout.trim();
  if (!socketPath || !socketPath.startsWith('/')) {
    throw new Error(
      `start_headless_bridge.py produced unexpected stdout: ${JSON.stringify(stdout)}`,
    );
  }
  return socketPath;
}

/**
 * Authenticate against the real engine using a real user's email +
 * password, returning the tokens the bundle expects in localStorage.
 *
 * Hits ``/v1/auth/login`` directly (the same endpoint the SDK calls).
 * Uses Word's partner key per ADR-0034 — Writer's own key is broken
 * upstream (Platform #61) until that's resolved.
 */
const WORD_PARTNER_KEY =
  'pk_live_45c878caa500cdf6ea1a72f3e9a4ad324df061b7ec2c70d7';

export type LiveAuthTokens = {
  access_token: string;
  refresh_token: string;
  user: { id: string; email: string };
};

export async function liveEngineLogin(
  email: string,
  password: string,
): Promise<LiveAuthTokens> {
  const resp = await fetch('https://engine.talk2view.com/v1/auth/login', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-t2v-partner-key': WORD_PARTNER_KEY,
    },
    body: JSON.stringify({ email, password }),
  });
  if (!resp.ok) {
    throw new Error(
      `live engine login failed: ${resp.status} ${resp.statusText} — ` +
        (await resp.text()),
    );
  }
  const data = (await resp.json()) as {
    access_token: string;
    refresh_token: string;
    user?: { id: string; email: string };
  };
  if (!data.access_token || !data.user) {
    throw new Error(
      `live engine login returned unexpected payload: ${JSON.stringify(data)}`,
    );
  }
  return data as LiveAuthTokens;
}

export const test = base.extend<LiveFixtures>({
  liveBridgeProxy: async ({}, use) => {
    const socketPath = await startHeadlessBridge(sofficePort());
    const proxy = new BridgeProxy({ socketPath });
    await proxy.start();
    try {
      await use(proxy);
    } finally {
      await proxy.stop();
    }
  },

  liveBundleServer: async ({}, use) => {
    const server = new StaticBundleServer();
    await server.start();
    try {
      await use(server);
    } finally {
      await server.stop();
    }
  },
});

export { expect } from '@playwright/test';
