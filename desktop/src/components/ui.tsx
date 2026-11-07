import type { ReactNode } from "react";

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
          <h2 className="text-[13px] font-semibold">{title}</h2>
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
      <div className="font-mono text-[10px] text-text-3">{label}</div>
      <div className={`font-mono text-[17px] font-bold ${tone}`}>
        {value}
        {unit && <span className="ml-1 text-[10px] font-normal text-text-3">{unit}</span>}
      </div>
    </div>
  );
}

export function Slider({ label, value, min, max, step, unit, onChange, disabled }: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  unit: string;
  onChange: (v: number) => void;
  disabled?: boolean;
}) {
  return (
    <label className="block">
      <div className="mb-1 flex justify-between">
        <span className="label">{label}</span>
        <span className="font-mono text-[12px] font-semibold">
          {fmt(value)} {unit}
        </span>
      </div>
      <input
        type="range"
        className="w-full"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
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
