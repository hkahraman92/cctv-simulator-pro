// JSON-RPC 2.0 client for the Python core.
//
// The transport is a dumb line pipe (Tauri events in the app, an in-memory
// fake in tests). This file owns request ids, response matching, progress,
// cancellation and timeouts — the Rust side never parses a frame.

export interface Transport {
  send(line: string): Promise<void>;
  /** Subscribe to frames, logs and exits; returns an unsubscribe function. */
  listen(handlers: {
    frame: (line: string) => void;
    exit: () => void;
    log?: (line: string) => void;
  }): Promise<() => void>;
  restart(): Promise<void>;
  /** Why the core is not running (start failure), if the transport knows. */
  status?(): Promise<string | null>;
}

export class RpcError extends Error {
  readonly code: number;
  readonly data: unknown;
  constructor(code: number, message: string, data?: unknown) {
    super(message);
    this.name = "RpcError";
    this.code = code;
    this.data = data;
  }
}

export const REQUEST_CANCELLED = -32800;
export const CORE_GONE = -32099; // local: the process exited mid-request
export const TIMEOUT = -32098; // local: no reply in time

export interface Progress {
  done: number;
  total: number;
  message: string;
}

export interface CallOptions {
  signal?: AbortSignal;
  onProgress?: (p: Progress) => void;
  timeoutMs?: number;
}

interface Pending {
  resolve: (v: unknown) => void;
  reject: (e: unknown) => void;
  onProgress?: (p: Progress) => void;
  timer?: ReturnType<typeof setTimeout>;
  cleanup: () => void;
}

export type CoreState = "connecting" | "ready" | "down";

export class RpcClient {
  private nextId = 1;
  private pending = new Map<number, Pending>();
  private unlisten?: () => void;
  private stateListeners = new Set<(s: CoreState) => void>();
  private logListeners = new Set<(l: string) => void>();
  state: CoreState = "connecting";
  /** Reason shown when the state is "down". */
  lastError = "";

  constructor(private transport: Transport, private defaultTimeoutMs = 120_000) {}

  async start(): Promise<void> {
    this.unlisten = await this.transport.listen({
      frame: (line) => this.onFrame(line),
      exit: () => this.onExit(),
      log: (line) => this.logListeners.forEach((f) => f(line)),
    });
    await this.handshake();
  }

  stop(): void {
    this.unlisten?.();
  }

  onState(f: (s: CoreState) => void): () => void {
    this.stateListeners.add(f);
    return () => this.stateListeners.delete(f);
  }

  onLog(f: (l: string) => void): () => void {
    this.logListeners.add(f);
    return () => this.logListeners.delete(f);
  }

  async restart(): Promise<void> {
    this.setState("connecting");
    await this.transport.restart();
    await this.handshake();
  }

  call<T>(method: string, params: object = {}, opts: CallOptions = {}): Promise<T> {
    const id = this.nextId++;
    return new Promise<T>((resolve, reject) => {
      const onAbort = () => {
        void this.transport
          .send(JSON.stringify({ jsonrpc: "2.0", method: "$/cancelRequest", params: { id } }))
          .catch(() => undefined);
      };
      const entry: Pending = {
        resolve: resolve as (v: unknown) => void,
        reject,
        onProgress: opts.onProgress,
        cleanup: () => {
          if (entry.timer) clearTimeout(entry.timer);
          opts.signal?.removeEventListener("abort", onAbort);
          this.pending.delete(id);
        },
      };
      if (opts.signal?.aborted) {
        reject(new RpcError(REQUEST_CANCELLED, "İstek iptal edildi."));
        return;
      }
      opts.signal?.addEventListener("abort", onAbort, { once: true });
      const timeout = opts.timeoutMs ?? this.defaultTimeoutMs;
      if (timeout > 0) {
        entry.timer = setTimeout(() => {
          onAbort();
          entry.cleanup();
          reject(new RpcError(TIMEOUT, `Çekirdek ${Math.round(timeout / 1000)} sn içinde yanıt vermedi: ${method}`));
        }, timeout);
      }
      this.pending.set(id, entry);
      this.transport
        .send(JSON.stringify({ jsonrpc: "2.0", id, method, params }))
        .catch((e) => {
          entry.cleanup();
          reject(new RpcError(CORE_GONE, `Çekirdeğe ulaşılamadı: ${String(e)}`));
        });
    });
  }

  private async handshake(): Promise<void> {
    try {
      await this.call("system.ping", {}, { timeoutMs: 15_000 });
      this.lastError = "";
      this.setState("ready");
    } catch (e) {
      const reason = await this.transport.status?.().catch(() => null);
      this.lastError = reason || (e instanceof Error ? e.message : String(e));
      this.setState("down");
    }
  }

  private setState(s: CoreState) {
    if (this.state === s) return;
    this.state = s;
    this.stateListeners.forEach((f) => f(s));
  }

  private onFrame(line: string) {
    let msg: any;
    try {
      msg = JSON.parse(line);
    } catch {
      return; // not a frame; the core logs to stderr, so this should not happen
    }
    if (msg.method === "$/progress") {
      const p = msg.params ?? {};
      this.pending.get(p.id)?.onProgress?.({ done: p.done, total: p.total, message: p.message ?? "" });
      return;
    }
    if (typeof msg.id !== "number") return;
    const entry = this.pending.get(msg.id);
    if (!entry) return; // timed out or cancelled earlier
    entry.cleanup();
    if (msg.error) entry.reject(new RpcError(msg.error.code, msg.error.message, msg.error.data));
    else entry.resolve(msg.result);
  }

  private onExit() {
    this.lastError = "Çekirdek süreci kapandı.";
    this.setState("down");
    for (const entry of [...this.pending.values()]) {
      entry.cleanup();
      entry.reject(new RpcError(CORE_GONE, "Çekirdek süreci kapandı."));
    }
  }
}
