import { useEffect, useState, useSyncExternalStore } from "react";
import { Button } from "./ui";

/**
 * In-app confirm / prompt. The native window.confirm / window.prompt froze the
 * whole webview, ignored the theme and showed a password in clear text.
 * Usage: `if (await confirmDialog("…")) …`, `const s = await promptDialog("…")`.
 */
interface Request {
  kind: "confirm" | "prompt";
  message: string;
  okLabel: string;
  danger: boolean;
  password: boolean;
  initial: string;
  resolve: (v: boolean | string | null) => void;
}

let current: Request | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

function ask(r: Omit<Request, "resolve">): Promise<boolean | string | null> {
  return new Promise((resolve) => {
    current?.resolve(current.kind === "confirm" ? false : null);     // a second dialog replaces the first
    current = { ...r, resolve };
    emit();
  });
}

export const confirmDialog = (message: string, o: { okLabel?: string; danger?: boolean } = {}) =>
  ask({ kind: "confirm", message, okLabel: o.okLabel ?? "Devam", danger: !!o.danger, password: false, initial: "" }) as Promise<boolean>;

export const promptDialog = (message: string, o: { okLabel?: string; password?: boolean; initial?: string } = {}) =>
  ask({ kind: "prompt", message, okLabel: o.okLabel ?? "Tamam", danger: false, password: !!o.password, initial: o.initial ?? "" }) as Promise<string | null>;

export function DialogHost() {
  const req = useSyncExternalStore((l) => { listeners.add(l); return () => { listeners.delete(l); }; }, () => current);
  const [text, setText] = useState("");
  useEffect(() => { setText(req?.initial ?? ""); }, [req]);
  if (!req) return null;
  const close = (v: boolean | string | null) => { current = null; emit(); req.resolve(v); };
  const cancel = () => close(req.kind === "confirm" ? false : null);
  const accept = () => close(req.kind === "confirm" ? true : text);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/55" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) cancel(); }}>
      <div role="dialog" aria-modal="true" aria-label={req.message} className="w-[420px] max-w-[90vw] rounded-md border border-bg-6 bg-bg-2 p-4 shadow-xl"
        onKeyDown={(e) => { if (e.key === "Escape") cancel(); else if (e.key === "Enter") accept(); }}>
        <p className="text-[13px] whitespace-pre-wrap">{req.message}</p>
        {req.kind === "prompt" && (
          <input autoFocus type={req.password ? "password" : "text"} value={text} onChange={(e) => setText(e.target.value)}
            className="mt-3 h-8 w-full rounded border border-line bg-bg-0 px-2 text-[13px]" />
        )}
        <div className="mt-4 flex justify-end gap-2">
          <Button onClick={cancel}>Vazgeç</Button>
          <Button autoFocus={req.kind === "confirm"} primary={!req.danger} className={req.danger ? "!bg-danger-bg !text-danger" : ""} onClick={accept}>{req.okLabel}</Button>
        </div>
      </div>
    </div>
  );
}
