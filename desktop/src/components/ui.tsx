import { useState, type ReactNode } from "react";

export function Panel({ title, icon, right, children, className = "" }: {
  title?: string;
  icon?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-md bg-bg-4 p-3 ${className}`}>
      {title && (
        <div className="mb-2.5 flex items-center gap-2">
          {icon}
          <h2 className="shrink-0 whitespace-nowrap text-[13px] font-semibold">{title}</h2>
          <div className="flex-1" />
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

export function Tile({ label, value, unit, tone = "text-text" }: {
  label: string;
  value: string;
  unit?: string;
  tone?: string;
}) {
  return (
    <div className="rounded bg-bg-1 p-2">
      <div className="font-mono text-[11px] text-text-3">{label}</div>
      <div className={`font-mono text-[17px] font-bold ${tone}`}>
        {value}
        {unit && <span className="ml-1 text-[11px] font-normal text-text-3">{unit}</span>}
      </div>
    </div>
  );
}

export function Slider({ label, value, min, max, step, unit, onChange, disabled, digits }: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  unit: string;
  onChange: (v: number) => void;
  disabled?: boolean;
  /** decimals shown (default: fmt's rule) */
  digits?: number;
}) {
  // Typed entry: a slider cannot be set to an exact value (and its step hides in-between ones such
  // as 62,5). Click the number, type, Enter / leave the field to apply; Esc cancels. The typed
  // value is clamped to the range but NOT snapped to the step.
  const [draft, setDraft] = useState<string | null>(null);
  const commit = () => {
    if (draft === null) return;
    const v = Number(draft.trim().replace(/\s/g, "").replace(",", "."));
    if (draft.trim() !== "" && Number.isFinite(v)) onChange(Math.min(max, Math.max(min, v)));
    setDraft(null);
  };
  return (
    <div className="block">
      <div className="mb-1 flex items-center justify-between">
        <span className="label">{label}</span>
        {draft === null ? (
          <button type="button" disabled={disabled} title="Değeri yazmak için tıklayın" aria-label={`${label}: ${fmt(value, digits)} ${unit} — yazmak için tıklayın`}
            onClick={() => setDraft(String(Math.round(value * 1000) / 1000).replace(".", ","))}
            className="rounded px-1 font-mono text-[12px] font-semibold hover:bg-bg-5 disabled:hover:bg-transparent">
            {fmt(value, digits)} {unit}
          </button>
        ) : (
          <span className="flex items-center gap-1 font-mono text-[12px] font-semibold">
            <input autoFocus inputMode="decimal" value={draft} aria-label={`${label} (${unit})`}
              onChange={(e) => setDraft(e.target.value)} onBlur={commit} onFocus={(e) => e.target.select()}
              onKeyDown={(e) => { if (e.key === "Enter") commit(); else if (e.key === "Escape") setDraft(null); }}
              className="h-6 w-20 rounded border border-line bg-bg-0 px-1 text-right" />
            {unit}
          </span>
        )}
      </div>
      <input
        type="range"
        className="w-full"
        aria-label={label}
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </div>
  );
}

export function Button({ children, primary, ...rest }: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  primary?: boolean;
}) {
  return (
    <button
      type="button"
      {...rest}
      className={`inline-flex h-8 items-center justify-center gap-1.5 rounded px-3 text-xs font-medium disabled:opacity-50 ${
        primary ? "bg-primary font-semibold text-on-primary hover:brightness-110" : "bg-bg-5 text-text hover:bg-bg-6"
      } ${rest.className ?? ""}`}
    >
      {children}
    </button>
  );
}

/** Turkish number formatting: decimal comma, 1 decimal under 100. */
export function fmt(v: number | null | undefined, digits?: number): string {
  if (v === null || v === undefined) return "∞";
  if (!Number.isFinite(v)) return "∞";
  const d = digits ?? (Math.abs(v) >= 100 ? 0 : 1);
  return v.toLocaleString("tr-TR", { minimumFractionDigits: d, maximumFractionDigits: d });
}
