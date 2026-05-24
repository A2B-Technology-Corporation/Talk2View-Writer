/**
 * Node-side fake of the Python ``BridgeServer``'s Unix-socket
 * newline-delimited JSON-RPC protocol. Used by bridge-proxy unit
 * tests so they don't need a real soffice + extension running.
 *
 * The real protocol (see ``src/talk2view_writer/bridge_server.py``):
 *
 *   request:  {"id": <int>, "method": <str>, "params": <object>} + "\n"
 *   response: {"id": <int>, "result": ...} + "\n"
 *           | {"id": <int>, "error": {"type": <str>, "message": <str>}} + "\n"
 *
 * Single connection at a time, listen(1).
 *
 * Usage:
 *
 *     const mock = new MockBridge();
 *     await mock.start();
 *     mock.respondWith((req) => ({ result: { ok: true } }));
 *     // ... drive the proxy ...
 *     await mock.stop();
 *     expect(mock.requests).toHaveLength(1);
 */
import { createServer, Server, Socket } from 'net';
import { mkdtemp, rm } from 'fs/promises';
import { tmpdir } from 'os';
import { join } from 'path';

export type BridgeRequest = {
  id: number;
  method: string;
  params: Record<string, unknown>;
};

export type BridgeResponse =
  | { id: number; result: unknown }
  | { id: number; error: { type: string; message: string } };

/** Strategy for replying. ``id`` is filled in by the mock. */
export type Responder = (
  req: BridgeRequest,
) => { result: unknown } | { error: { type: string; message: string } } | Promise<
  { result: unknown } | { error: { type: string; message: string } }
>;

export class MockBridge {
  private server: Server | null = null;
  private dir: string | null = null;
  /** Absolute path of the Unix socket the proxy should connect to. */
  socketPath = '';
  /** Every request the mock has received, in arrival order. */
  readonly requests: BridgeRequest[] = [];

  private responder: Responder = () => ({ result: null });

  async start(): Promise<string> {
    this.dir = await mkdtemp(join(tmpdir(), 'mock-bridge-'));
    this.socketPath = join(this.dir, 'sock');
    return new Promise((resolve, reject) => {
      this.server = createServer((sock) => this.handleConnection(sock));
      this.server.once('error', reject);
      this.server.listen(this.socketPath, () => {
        this.server!.removeListener('error', reject);
        resolve(this.socketPath);
      });
    });
  }

  /** Install the reply strategy for subsequent requests. */
  respondWith(responder: Responder): void {
    this.responder = responder;
  }

  async stop(): Promise<void> {
    if (this.server) {
      await new Promise<void>((resolve) => this.server!.close(() => resolve()));
      this.server = null;
    }
    if (this.dir) {
      await rm(this.dir, { recursive: true, force: true });
      this.dir = null;
    }
  }

  private handleConnection(sock: Socket): void {
    let buf = '';
    sock.setEncoding('utf-8');
    sock.on('data', async (chunk: string) => {
      buf += chunk;
      // Newline-delimited JSON; drain whole lines, leave partial in buf.
      let nl: number;
      while ((nl = buf.indexOf('\n')) !== -1) {
        const line = buf.slice(0, nl);
        buf = buf.slice(nl + 1);
        if (!line.trim()) continue;
        let req: BridgeRequest;
        try {
          req = JSON.parse(line) as BridgeRequest;
        } catch (err) {
          sock.write(
            JSON.stringify({
              id: null,
              error: { type: 'ParseError', message: (err as Error).message },
            }) + '\n',
          );
          continue;
        }
        this.requests.push(req);
        const replyShape = await this.responder(req);
        const resp: BridgeResponse = { id: req.id, ...replyShape } as BridgeResponse;
        sock.write(JSON.stringify(resp) + '\n');
      }
    });
    sock.on('error', () => {
      // Connection dropped during a test teardown — fine.
    });
  }
}
