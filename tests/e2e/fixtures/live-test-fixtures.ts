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
import { promisify } from 'util';
import { resolve } from 'path';

import { BridgeProxy } from './bridge-proxy';

const execFileP = promisify(execFile);

type LiveFixtures = {
  liveBridgeProxy: BridgeProxy;
};

const REPO_ROOT = resolve(__dirname, '../../..');

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
});

export { expect } from '@playwright/test';
