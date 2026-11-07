import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, type Api } from "./api";
import { RpcClient, type CoreState } from "./rpc";
import { defaultTransport } from "./tauriTransport";
import { terrainApi, type TerrainApi } from "./terrainApi";
import { benchApi, type BenchApi } from "./benchApi";
import { specApi, type SpecApi } from "./specApi";

interface CoreCtx {
  api: Api;
  terrain: TerrainApi;
  bench: BenchApi;
  spec: SpecApi;
  rpc: RpcClient;
  state: CoreState;
  logs: string[];
}

const Ctx = createContext<CoreCtx | null>(null);

export function CoreProvider({ children }: { children: ReactNode }) {
  const rpc = useMemo(() => new RpcClient(defaultTransport()), []);
  const [state, setState] = useState<CoreState>(rpc.state);
  const [logs, setLogs] = useState<string[]>([]);

  useEffect(() => {
    const offState = rpc.onState(setState);
    const offLog = rpc.onLog((l) => setLogs((prev) => [...prev.slice(-199), l]));
    void rpc.start();
    return () => {
      offState();
      offLog();
      rpc.stop();
    };
  }, [rpc]);

  const apis = useMemo(() => ({ api: api(rpc), terrain: terrainApi(rpc), bench: benchApi(rpc), spec: specApi(rpc) }), [rpc]);
  const value = useMemo(() => ({ ...apis, rpc, state, logs }), [apis, rpc, state, logs]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useCore(): CoreCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useCore outside CoreProvider");
  return v;
}

/**
 * Latest-wins request coalescing for continuous inputs (sliders): at most one
 * request in flight; while it runs, newer inputs only replace the pending
 * one. Same rule as the classic app's schedule_calculate (Rule 2).
 */
export function useCoalesced<I, O>(input: I, run: (i: I) => Promise<O>, enabled = true) {
  const [out, setOut] = useState<{ value?: O; error?: string; busy: boolean; ms?: number }>({ busy: false });
  const [box] = useState(() => ({ inflight: false, next: undefined as I | undefined, has: false }));

  useEffect(() => {
    if (!enabled) return;
    box.next = input;
    box.has = true;
    if (box.inflight) return;
    const pump = async () => {
      while (box.has) {
        const i = box.next as I;
        box.has = false;
        box.inflight = true;
        setOut((o) => ({ ...o, busy: true }));
        const t0 = performance.now();
        try {
          const value = await run(i);
          setOut({ value, busy: box.has, ms: performance.now() - t0 });
        } catch (e) {
          setOut((o) => ({ ...o, error: e instanceof Error ? e.message : String(e), busy: box.has }));
        } finally {
          box.inflight = false;
        }
      }
    };
    void pump();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [input, enabled]);

  return out;
}
