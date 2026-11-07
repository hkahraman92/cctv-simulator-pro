import { describe, expect, it } from "vitest";
import { CORE_GONE, REQUEST_CANCELLED, RpcClient, RpcError, TIMEOUT, type Transport } from "./rpc";

/** Resolves with the RpcError a call was rejected with (fails if it resolved). */
const failure = (p: Promise<unknown>) =>
  p.then(
    () => { throw new Error("beklenmedik başarı"); },
    (e) => e as RpcError,
  );

/** In-memory core: `reply` decides what to answer for each request. */
function fakeCore(reply: (msg: any, push: (o: object) => void) => void) {
  let frame: (l: string) => void = () => undefined;
  let exit: () => void = () => undefined;
  const sent: any[] = [];
  const push = (o: object) => queueMicrotask(() => frame(JSON.stringify(o)));
  const transport: Transport = {
    async send(line) {
      const msg = JSON.parse(line);
      sent.push(msg);
      if (msg.method === "system.ping") push({ jsonrpc: "2.0", id: msg.id, result: { pong: true } });
      else reply(msg, push);
    },
    async listen(h) {
      frame = h.frame;
      exit = h.exit;
      return () => undefined;
    },
    async restart() {},
  };
  return { transport, sent, kill: () => exit() };
}

describe("RpcClient", () => {
  it("handshakes, then matches replies by id regardless of order", async () => {
    const held: any[] = [];
    const core = fakeCore((msg, push) => {
      held.push(msg);
      if (held.length === 2) {
        for (const m of [...held].reverse()) push({ jsonrpc: "2.0", id: m.id, result: m.params.n });
      }
    });
    const rpc = new RpcClient(core.transport);
    await rpc.start();
    expect(rpc.state).toBe("ready");
    const [a, b] = await Promise.all([rpc.call("echo", { n: 1 }), rpc.call("echo", { n: 2 })]);
    expect([a, b]).toEqual([1, 2]);
  });

  it("turns error frames into RpcError with code and data", async () => {
    const core = fakeCore((msg, push) =>
      push({ jsonrpc: "2.0", id: msg.id, error: { code: -32602, message: "kötü", data: { unknown: ["x"] } } }),
    );
    const rpc = new RpcClient(core.transport);
    await rpc.start();
    const err = await failure(rpc.call("x"));
    expect(err).toBeInstanceOf(RpcError);
    expect(err.code).toBe(-32602);
    expect(err.data).toEqual({ unknown: ["x"] });
  });

  it("routes progress and sends $/cancelRequest on abort", async () => {
    const core = fakeCore((msg, push) => {
      if (msg.method === "slow") push({ jsonrpc: "2.0", method: "$/progress", params: { id: msg.id, done: 1, total: 4 } });
      if (msg.method === "$/cancelRequest")
        push({ jsonrpc: "2.0", id: msg.params.id, error: { code: REQUEST_CANCELLED, message: "iptal" } });
    });
    const rpc = new RpcClient(core.transport);
    await rpc.start();
    const ctl = new AbortController();
    const seen: number[] = [];
    const p = rpc.call("slow", {}, { signal: ctl.signal, onProgress: (pr) => seen.push(pr.done) });
    await new Promise((r) => setTimeout(r, 5));
    ctl.abort();
    const err = await failure(p);
    expect(err.code).toBe(REQUEST_CANCELLED);
    expect(seen).toEqual([1]);
    expect(core.sent.some((m) => m.method === "$/cancelRequest")).toBe(true);
  });

  it("times out and fails every pending call when the core exits", async () => {
    const core = fakeCore(() => undefined);
    const rpc = new RpcClient(core.transport);
    await rpc.start();
    const slow = await failure(rpc.call("never", {}, { timeoutMs: 10 }));
    expect(slow.code).toBe(TIMEOUT);
    const states: string[] = [];
    rpc.onState((s) => states.push(s));
    const p = rpc.call("never");
    core.kill();
    expect((await failure(p)).code).toBe(CORE_GONE);
    expect(states).toEqual(["down"]);
  });
});
