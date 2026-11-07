import { X } from "lucide-react";
import type { JobState } from "../core/useJob";
import { Button, fmt } from "./ui";

/** Progress + cancel for the running long call, or its error. */
export default function JobBar({ job, onCancel, onDismiss }: { job: JobState; onCancel: () => void; onDismiss: () => void }) {
  if (job.running) {
    const p = job.progress;
    const frac = p && p.total > 0 ? Math.min(p.done / p.total, 1) : null;
    return (
      <div className="flex items-center gap-3 rounded bg-bg-3 px-3 py-2" role="status" aria-live="polite">
        <div className="min-w-0 flex-1">
          <div className="flex justify-between text-[11px]">
            <span className="truncate">{job.label}{p?.message ? ` — ${p.message}` : ""}</span>
            <span className="font-mono text-text-3">{frac !== null ? `%${fmt(frac * 100, 0)}` : "…"}</span>
          </div>
          <div className="mt-1 h-1.5 overflow-hidden rounded bg-bg-0">
            <div className={`h-full bg-secondary ${frac === null ? "w-1/3 animate-pulse" : ""}`}
              style={frac !== null ? { width: `${frac * 100}%` } : undefined} />
          </div>
        </div>
        <Button onClick={onCancel}><X size={14} /> İptal</Button>
      </div>
    );
  }
  if (job.error) {
    return (
      <div className="flex items-start gap-2 rounded bg-danger-bg px-3 py-2 text-[12px] text-danger" role="alert">
        <span className="flex-1">■ {job.error}</span>
        <button type="button" aria-label="Kapat" onClick={onDismiss}><X size={14} /></button>
      </div>
    );
  }
  return null;
}
