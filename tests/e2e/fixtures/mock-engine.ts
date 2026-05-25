/**
 * In-process mock of the Talk2View engine.
 *
 * Speaks the same HTTP + SSE surface as engine.talk2view.com:
 *
 *   GET  /v1/config                       → partner config
 *   POST /v1/tools/register               → tool registration ack
 *   POST /v1/sessions                     → session create
 *   POST /v1/sessions/{id}/messages       → chat completion (SSE stream)
 *   POST /v1/auth/login                   → returns access_token + user
 *
 * Each test owns a MockEngine instance bound to a random localhost
 * port. Tests script the chat-completion responses ahead of time via
 * `engine.scriptChatStream([...chunks])`; the SSE stream then drains
 * those chunks in order, simulating the agent.
 *
 * Why a custom server rather than `msw` or `nock`: we need to serve
 * BOTH the bundle's static assets and the engine API on the same
 * origin (so the page-init shim's `fetch(BASE_URL)` reaches us without
 * CORS), and we need real SSE — not just response stubbing.
 *
 * See ADR-0031.
 */
import { createServer, IncomingMessage, ServerResponse, Server } from 'http';
import { AddressInfo } from 'net';
import { readFileSync, existsSync, statSync } from 'fs';
import { join, normalize, extname } from 'path';
import { randomUUID } from 'crypto';

/** A single SSE chunk (the engine's "delta" event payload). */
export type StreamChunk = {
  type: 'delta';
  content?: string;
  tool_calls?: Array<{ id: string; name: string; arguments: string }>;
  finish_reason?: 'stop' | 'length' | 'tool_calls';
  /** Optional delay before sending this chunk, in milliseconds. */
  delayMs?: number;
};

/** Scripted response to a chat-completion request. */
export type StreamScript = StreamChunk[];

/** Partner config shape returned by /v1/config. */
export type PartnerConfig = {
  default_llm_model: string | null;
  default_stt_model: string | null;
  system_prompt: string | null;
  allowed_llm_models: string[] | null;
  allowed_stt_models: string[] | null;
};

export class MockEngine {
  private server: Server | null = null;
  private baseUrl = '';
  private scripts: StreamScript[] = [];
  private staticRoot: string | null = null;
  private partnerConfig: PartnerConfig = {
    default_llm_model: 'mock-model',
    default_stt_model: null,
    system_prompt: null,
    allowed_llm_models: ['mock-model'],
    allowed_stt_models: null,
  };

  /** Observed requests for assertion in tests. */
  readonly requests: Array<{
    method: string;
    path: string;
    headers: Record<string, string>;
    body: string;
  }> = [];

  /**
   * Boot the server on a random localhost port. Returns the base URL
   * the page should fetch from.
   */
  async start(opts: { staticRoot?: string } = {}): Promise<string> {
    this.staticRoot = opts.staticRoot ?? null;
    this.server = createServer((req, res) => {
      this.handle(req, res).catch((err) => {
        // Surface server-side errors to the test runner — a hidden
        // 500 here can otherwise look like a frontend bug.
        // eslint-disable-next-line no-console
        console.error('[mock-engine] handler error', err);
        if (!res.headersSent) {
          res.writeHead(500, { 'content-type': 'text/plain' });
          res.end('mock engine error');
        }
      });
    });

    return new Promise((resolve, reject) => {
      this.server!.once('error', reject);
      this.server!.listen(0, '127.0.0.1', () => {
        const addr = this.server!.address() as AddressInfo;
        this.baseUrl = `http://127.0.0.1:${addr.port}`;
        resolve(this.baseUrl);
      });
    });
  }

  /** Tear down the server. Safe to call multiple times.
   *
   * ``server.close()`` alone waits for in-flight SSE long-polls — if
   * a chat stream is mid-iteration when the test ends, the connection
   * keeps the server alive past Playwright's 30s teardown timeout,
   * marking the test failed on macOS Chromium (Investigation #39).
   * ``closeAllConnections()`` forcibly closes every open socket
   * first so ``close()`` returns immediately.
   */
  async stop(): Promise<void> {
    if (!this.server) return;
    this.server.closeAllConnections();
    await new Promise<void>((resolve) => this.server!.close(() => resolve()));
    this.server = null;
  }

  /** URL the bundle should use as `baseUrl`. */
  url(): string {
    if (!this.baseUrl) throw new Error('mock engine not started');
    return this.baseUrl;
  }

  /** Push a scripted SSE response. Consumed FIFO on chat-completion. */
  scriptChatStream(chunks: StreamScript): void {
    this.scripts.push(chunks);
  }

  /** Override the /v1/config response (defaults to a sane mock-model profile). */
  setPartnerConfig(cfg: Partial<PartnerConfig>): void {
    this.partnerConfig = { ...this.partnerConfig, ...cfg };
  }

  // -------------------------------------------------------------------
  // Request handling
  // -------------------------------------------------------------------

  private async handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const method = (req.method ?? 'GET').toUpperCase();
    const path = (req.url ?? '/').split('?')[0];
    const body = await readBody(req);
    this.requests.push({
      method,
      path,
      headers: req.headers as Record<string, string>,
      body,
    });

    // CORS — Playwright's Chromium hits us cross-origin when the
    // bundle is served from a different port. Always allow it.
    res.setHeader('access-control-allow-origin', '*');
    res.setHeader(
      'access-control-allow-headers',
      'authorization, content-type, x-t2v-partner-key',
    );
    res.setHeader('access-control-allow-methods', 'GET, POST, OPTIONS');
    if (method === 'OPTIONS') {
      res.writeHead(204);
      res.end();
      return;
    }

    if (method === 'GET' && path === '/v1/config') {
      this.json(res, 200, this.partnerConfig);
      return;
    }

    if (method === 'POST' && path === '/v1/tools/register') {
      const { tools = [] } = safeJson(body) as { tools?: Array<{ name: string }> };
      this.json(res, 200, {
        registered: tools.map((t) => t.name),
        count: tools.length,
      });
      return;
    }

    if (method === 'POST' && path === '/v1/sessions') {
      this.json(res, 200, {
        session_id: randomUUID(),
        thread_id: randomUUID(),
        model: this.partnerConfig.default_llm_model ?? 'mock-model',
      });
      return;
    }

    if (method === 'POST' && /^\/v1\/sessions\/[^/]+\/messages$/.test(path)) {
      await this.streamChatCompletion(res);
      return;
    }

    if (method === 'POST' && path === '/v1/auth/login') {
      const parsed = safeJson(body) as { email?: string };
      this.json(res, 200, {
        access_token: 'mock-token-' + randomUUID(),
        refresh_token: 'mock-refresh-' + randomUUID(),
        expires_in: 3600,
        user: { id: 'mock-user-id', email: parsed.email ?? 'tester@example.com' },
      });
      return;
    }

    // Static asset fallback — serve the webpack bundle dir so the
    // page-init code lives on the same origin as the engine and
    // doesn't need CORS pre-flights for its own assets.
    if (method === 'GET' && this.staticRoot) {
      const safe = normalize(path).replace(/^\/+/, '');
      const file = join(this.staticRoot, safe || 'index.html');
      if (file.startsWith(this.staticRoot) && existsSync(file) && statSync(file).isFile()) {
        const body = readFileSync(file);
        res.writeHead(200, { 'content-type': mimeFor(file) });
        res.end(body);
        return;
      }
    }

    res.writeHead(404, { 'content-type': 'text/plain' });
    res.end(`mock engine: no route for ${method} ${path}`);
  }

  /**
   * Drain one scripted SSE script into the response. Defaults to a
   * single "hello" delta if no script is queued (so a test that
   * forgets to script the assistant turn gets a deterministic reply
   * rather than hanging).
   */
  private async streamChatCompletion(res: ServerResponse): Promise<void> {
    const script: StreamScript = this.scripts.shift() ?? [
      { type: 'delta', content: 'hello from the mock engine', finish_reason: 'stop' },
    ];

    res.writeHead(200, {
      'content-type': 'text/event-stream; charset=utf-8',
      'cache-control': 'no-cache',
      connection: 'keep-alive',
      'access-control-allow-origin': '*',
    });

    const completionId = 'chatcmpl-mock-' + randomUUID();
    const created = Math.floor(Date.now() / 1000);
    const model = this.partnerConfig.default_llm_model ?? 'mock-model';
    const threadId = randomUUID();

    for (const chunk of script) {
      if (chunk.delayMs && chunk.delayMs > 0) {
        await sleep(chunk.delayMs);
      }
      const sse = {
        id: completionId,
        object: 'chat.completion.chunk',
        created,
        model,
        choices: [
          {
            index: 0,
            message: null,
            delta: {
              role: null,
              content: chunk.content ?? null,
              tool_calls: chunk.tool_calls ?? null,
            },
            finish_reason: chunk.finish_reason ?? null,
          },
        ],
        thread_id: threadId,
        interrupt: null,
        status: null,
        todos: null,
      };
      res.write(`data: ${JSON.stringify(sse)}\n\n`);
      // Force a flush so the chunk reaches the client now, not at
      // request end. Node's http stream buffers tiny writes by
      // default; without flushHeaders+cork toggling we get a single
      // batched delivery that defeats progressive-render tests.
      if (typeof (res as unknown as { flush?: () => void }).flush === 'function') {
        (res as unknown as { flush: () => void }).flush();
      }
      // Default tiny gap so tests can observe streaming behaviour
      // when no explicit delayMs is given.
      if (!chunk.delayMs) await sleep(10);
    }
    res.write('data: [DONE]\n\n');
    res.end();
  }

  private json(res: ServerResponse, status: number, body: unknown): void {
    res.writeHead(status, { 'content-type': 'application/json' });
    res.end(JSON.stringify(body));
  }
}

// ---------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on('data', (c: Buffer) => chunks.push(c));
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf-8')));
    req.on('error', reject);
  });
}

function safeJson(s: string): unknown {
  if (!s) return {};
  try {
    return JSON.parse(s);
  } catch {
    return {};
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function mimeFor(file: string): string {
  switch (extname(file).toLowerCase()) {
    case '.html':
      return 'text/html; charset=utf-8';
    case '.js':
      return 'application/javascript; charset=utf-8';
    case '.css':
      return 'text/css; charset=utf-8';
    case '.json':
      return 'application/json; charset=utf-8';
    case '.svg':
      return 'image/svg+xml';
    case '.png':
      return 'image/png';
    case '.wasm':
      return 'application/wasm';
    default:
      return 'application/octet-stream';
  }
}
