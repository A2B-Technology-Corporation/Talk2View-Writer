/**
 * Node bridge-proxy: translates browser-side `window.pywebview.api`
 * calls (HTTP) into the Python ``BridgeServer``'s newline-delimited
 * JSON-RPC protocol over a Unix socket.
 *
 * The live E2E suite (Architecture C — see ADR-0036 forthcoming) uses
 * this to drive the chat UI bundle in Playwright-Chromium against a
 * real soffice + extension, with the extension running in headless-
 * bridge mode (T2V_WRITER_HEADLESS_BRIDGE=1, see web_window.py) so
 * pywebview isn't spawned and the proxy is the sole consumer of the
 * bridge socket.
 *
 * Scope of this scaffold (step 2):
 *   - POST /invoke_tool      { name, args }              → JSON result
 *   - GET  /list_tools                                   → JSON allowlist
 *   - POST /log              { level, message, context } → 204
 *   - POST /proxy_fetch      { url, method, headers, body } → JSON resp
 *   - SSE  /proxy_stream/:id  (added in scaffold step 5)
 *
 * Concurrency: the Python bridge multiplexes responses by their ``id``
 * field. The proxy assigns a fresh id per request and routes the
 * matching response back to the right HTTP caller via a pending-map.
 */
import { createServer as createHttp, IncomingMessage, Server, ServerResponse } from 'http';
import { createConnection, Socket } from 'net';
import { AddressInfo } from 'net';

export type BridgeProxyOptions = {
  /** Path to the Python BridgeServer's Unix socket. */
  socketPath: string;
  /** HTTP port; 0 picks any free port (default). */
  port?: number;
};

type Pending = {
  resolve: (resp: { result?: unknown; error?: { type: string; message: string } }) => void;
};

export class BridgeProxy {
  private opts: BridgeProxyOptions;
  private http: Server | null = null;
  private sock: Socket | null = null;
  private buf = '';
  private nextId = 1;
  private pending = new Map<number, Pending>();
  private connectPromise: Promise<void> | null = null;

  constructor(opts: BridgeProxyOptions) {
    this.opts = opts;
  }

  url(): string {
    if (!this.http) throw new Error('BridgeProxy not started');
    const { port } = this.http.address() as AddressInfo;
    return `http://127.0.0.1:${port}`;
  }

  async start(): Promise<void> {
    await this.connectBridge();
    await new Promise<void>((resolve, reject) => {
      this.http = createHttp((req, res) => this.handleHttp(req, res));
      this.http.once('error', reject);
      this.http.listen(this.opts.port ?? 0, '127.0.0.1', () => {
        this.http!.removeListener('error', reject);
        resolve();
      });
    });
  }

  async stop(): Promise<void> {
    if (this.http) {
      // closeAllConnections() force-closes the in-flight SSE long-polls
      // that ``handleStreamEvents`` may have left open. Without this,
      // server.close() blocks forever waiting for them to drain — bites
      // hardest on WebKit, where EventSource holds the connection open
      // past test teardown.
      this.http.closeAllConnections?.();
      await new Promise<void>((resolve) => this.http!.close(() => resolve()));
      this.http = null;
    }
    if (this.sock) {
      this.sock.end();
      this.sock.destroy();
      this.sock = null;
    }
    // Reject any still-pending requests so callers waiting on us
    // unblock instead of hanging the test.
    for (const pending of this.pending.values()) {
      pending.resolve({
        error: { type: 'BridgeShutdown', message: 'proxy stopped before response' },
      });
    }
    this.pending.clear();
  }

  // ----- Bridge socket connection -----------------------------------------

  private connectBridge(): Promise<void> {
    if (this.connectPromise) return this.connectPromise;
    this.connectPromise = new Promise<void>((resolve, reject) => {
      const sock = createConnection({ path: this.opts.socketPath });
      sock.setEncoding('utf-8');
      sock.once('connect', () => resolve());
      sock.once('error', reject);
      sock.on('data', (chunk: string) => this.onBridgeData(chunk));
      sock.on('close', () => {
        this.sock = null;
        // Surface as errors on every pending caller so they don't hang.
        for (const pending of this.pending.values()) {
          pending.resolve({
            error: {
              type: 'BridgeClosed',
              message: 'bridge socket closed before response',
            },
          });
        }
        this.pending.clear();
      });
      this.sock = sock;
    });
    return this.connectPromise;
  }

  private onBridgeData(chunk: string): void {
    this.buf += chunk;
    let nl: number;
    while ((nl = this.buf.indexOf('\n')) !== -1) {
      const line = this.buf.slice(0, nl);
      this.buf = this.buf.slice(nl + 1);
      if (!line.trim()) continue;
      let msg: { id: number; result?: unknown; error?: { type: string; message: string } };
      try {
        msg = JSON.parse(line);
      } catch (err) {
        // Malformed line — the Python bridge wrote something we
        // can't parse. Don't crash; just log to stderr.
        console.error('[bridge-proxy] unparseable line from bridge:', line, err);
        continue;
      }
      const pending = this.pending.get(msg.id);
      if (!pending) {
        console.error('[bridge-proxy] no pending caller for id=', msg.id);
        continue;
      }
      this.pending.delete(msg.id);
      pending.resolve({ result: msg.result, error: msg.error });
    }
  }

  private async sendBridgeRequest(
    method: string,
    params: Record<string, unknown>,
  ): Promise<{ result?: unknown; error?: { type: string; message: string } }> {
    await this.connectBridge();
    if (!this.sock) {
      return {
        error: { type: 'BridgeClosed', message: 'bridge socket not connected' },
      };
    }
    const id = this.nextId++;
    return new Promise((resolve) => {
      this.pending.set(id, { resolve });
      const line = JSON.stringify({ id, method, params }) + '\n';
      this.sock!.write(line);
    });
  }

  // ----- HTTP handlers -----------------------------------------------------

  private async handleHttp(req: IncomingMessage, res: ServerResponse): Promise<void> {
    // Permissive CORS so the chat-UI bundle (served from any localhost
    // origin) can hit us. Production has the bundle and the proxy on
    // the same host:port pair, but tests routinely cross origins.
    res.setHeader('access-control-allow-origin', '*');
    res.setHeader('access-control-allow-methods', 'GET, POST, OPTIONS');
    res.setHeader('access-control-allow-headers', 'content-type');
    if (req.method === 'OPTIONS') {
      res.statusCode = 204;
      res.end();
      return;
    }
    try {
      if (req.method === 'GET' && req.url === '/') {
        // Same-origin probe page for unit tests of the live shim — a
        // Playwright test can navigate here and then fetch our other
        // endpoints without crossing origins. The bundle never uses
        // this in production.
        res.setHeader('content-type', 'text/html; charset=utf-8');
        res.statusCode = 200;
        res.end('<!doctype html><meta charset="utf-8"><title>bridge-proxy</title>');
        return;
      }
      if (req.method === 'POST' && req.url === '/invoke_tool') {
        const body = await readJson<{ name: string; args: Record<string, unknown> }>(req);
        const out = await this.sendBridgeRequest('invoke_tool', body);
        respondBridge(res, out);
        return;
      }
      if (req.method === 'GET' && req.url === '/list_tools') {
        const out = await this.sendBridgeRequest('list_tools', {});
        respondBridge(res, out);
        return;
      }
      if (req.method === 'POST' && req.url === '/log') {
        const body = await readJson<{
          level: string;
          message: string;
          context?: unknown;
        }>(req);
        const out = await this.sendBridgeRequest('log', body);
        if (out.error) {
          respondBridge(res, out);
          return;
        }
        res.statusCode = 204;
        res.end();
        return;
      }
      if (req.method === 'POST' && req.url === '/proxy_fetch') {
        const body = await readJson<{
          url: string;
          method: string;
          headers: Record<string, string>;
          body: string | null;
        }>(req);
        const out = await this.sendBridgeRequest('proxy_fetch', body);
        respondBridge(res, out);
        return;
      }
      if (req.method === 'POST' && req.url === '/proxy_stream/open') {
        const body = await readJson<{
          url: string;
          method: string;
          headers: Record<string, string>;
          body: string | null;
        }>(req);
        const out = await this.sendBridgeRequest('proxy_stream_open', body);
        respondBridge(res, out);
        return;
      }
      const streamEventsMatch =
        req.method === 'GET' && /^\/proxy_stream\/([^/]+)\/events$/.exec(req.url ?? '');
      if (streamEventsMatch) {
        const streamId = streamEventsMatch[1];
        await this.handleStreamEvents(req, res, streamId);
        return;
      }
      res.statusCode = 404;
      res.end(JSON.stringify({ error: { type: 'NotFound', message: req.url ?? '' } }));
    } catch (err) {
      res.statusCode = 500;
      res.setHeader('content-type', 'application/json');
      res.end(
        JSON.stringify({
          error: { type: (err as Error).name, message: (err as Error).message },
        }),
      );
    }
  }

  /**
   * SSE long-poll: in a loop, ``proxy_stream_next(streamId)`` the
   * bridge and write each event as one ``data: <json>\n\n`` frame.
   * Exits on ``done`` or when the client disconnects.
   */
  private async handleStreamEvents(
    req: IncomingMessage,
    res: ServerResponse,
    streamId: string,
  ): Promise<void> {
    res.setHeader('content-type', 'text/event-stream');
    res.setHeader('cache-control', 'no-cache');
    res.setHeader('connection', 'keep-alive');
    res.statusCode = 200;
    res.flushHeaders?.();

    let clientGone = false;
    req.on('close', () => {
      clientGone = true;
    });

    while (!clientGone) {
      const out = await this.sendBridgeRequest('proxy_stream_next', {
        stream_id: streamId,
      });
      if (out.error) {
        // Surface as an SSE error event so the shim can route it
        // through its proxy_stream_next "error" path.
        const errEvt = { type: 'error', message: out.error.message };
        res.write(`data: ${JSON.stringify(errEvt)}\n\n`);
        res.write(`data: ${JSON.stringify({ type: 'done' })}\n\n`);
        res.end();
        return;
      }
      const ev = out.result as { type: string; [k: string]: unknown };
      res.write(`data: ${JSON.stringify(ev)}\n\n`);
      if (ev.type === 'done') {
        res.end();
        return;
      }
    }
  }
}

async function readJson<T>(req: IncomingMessage): Promise<T> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(chunk as Buffer);
  const text = Buffer.concat(chunks).toString('utf-8');
  return JSON.parse(text) as T;
}

function respondBridge(
  res: ServerResponse,
  out: { result?: unknown; error?: { type: string; message: string } },
): void {
  res.setHeader('content-type', 'application/json');
  if (out.error) {
    res.statusCode = 500;
    res.end(JSON.stringify({ error: out.error }));
    return;
  }
  res.statusCode = 200;
  res.end(JSON.stringify({ result: out.result }));
}
