import { useCallback, useRef, useState } from "react";
import type { Progress } from "./rpc";
import { REQUEST_CANCELLED } from "./rpc";

export interface JobState {
  running: boolean;
  label: string;
  progress?: Progress;
  error?: string;
}

/**
 * One long core call at a time with progress and a cancel button.
 * `run(label, fn)` passes an AbortSignal + progress sink to `fn`; a second
 * run while one is busy is refused (the UI disables its trigger anyway).
 */
export function useJob() {
  const [state, setState] = useState<JobState>({ running: false, label: "" });
  const ctl = useRef<AbortController | null>(null);

  const run = useCallback(
    async <R,>(label: string, fn: (o: { signal: AbortSignal; onProgress: (p: Progress) => void }) => Promise<R>) => {
      if (ctl.current) return undefined;
      const c = new AbortController();
      ctl.current = c;
      setState({ running: true, label });
      try {
        const r = await fn({ signal: c.signal, onProgress: (p) => setState((s) => ({ ...s, progress: p })) });
        setState({ running: false, label: "" });
        return r;
      } catch (e: unknown) {
        const code = (e as { code?: number }).code;
        const msg = e instanceof Error ? e.message : String(e);
        setState({ running: false, label: "", error: code === REQUEST_CANCELLED ? undefined : msg });
        return undefined;
      } finally {
        ctl.current = null;
      }
    },
    [],
  );

  const cancel = useCallback(() => ctl.current?.abort(), []);
  const clearError = useCallback(() => setState((s) => ({ ...s, error: undefined })), []);
  return { ...state, run, cancel, clearError };
}
