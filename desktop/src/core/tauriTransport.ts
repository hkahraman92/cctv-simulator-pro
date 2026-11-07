import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import type { Transport } from "./rpc";

/** Line pipe to the core through the Rust shell (`src-tauri/src/lib.rs`). */
export const tauriTransport: Transport = {
  send: (line) => invoke<void>("core_send", { line }),
  restart: () => invoke<void>("core_restart"),
  async listen({ frame, exit, log }) {
    const offs = await Promise.all([
      listen<string>("core://frame", (e) => frame(e.payload)),
      listen<number>("core://exit", () => exit()),
      listen<string>("core://log", (e) => log?.(e.payload)),
    ]);
    return () => offs.forEach((off) => off());
  },
};

/** Opened in a plain browser (`npm run dev` without Tauri): no core. */
export const missingTransport: Transport = {
  send: () => Promise.reject(new Error("Tauri dışında çalışıyor — çekirdek yok")),
  restart: () => Promise.resolve(),
  listen: () => Promise.resolve(() => undefined),
};

export const defaultTransport = (): Transport => (isTauri() ? tauriTransport : missingTransport);
