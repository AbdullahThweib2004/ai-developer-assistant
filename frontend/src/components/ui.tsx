import { Check, Copy, Loader2 } from "lucide-react";
import { type ButtonHTMLAttributes, type ReactNode, useState } from "react";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

type Variant = "primary" | "secondary" | "ghost" | "consequential" | "danger-ghost";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-accent-fg hover:bg-accent-hover shadow-soft",
  secondary: "bg-surface text-fg border border-border hover:bg-surface-2 hover:border-border-strong shadow-soft",
  ghost: "text-muted hover:text-fg hover:bg-surface-2",
  // Deliberately unlike every other button: used only for "Approve and create".
  consequential:
    "bg-warning-soft text-warning border border-warning-border hover:border-warning font-semibold " +
    "ring-1 ring-inset ring-warning/15",
  "danger-ghost": "text-muted hover:text-danger hover:bg-danger-soft",
};

export function Button({
  variant = "secondary", size = "md", icon, loading, className, children, ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant; size?: "sm" | "md"; icon?: ReactNode; loading?: boolean;
}) {
  return (
    <button
      type="button"
      {...props}
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-md font-medium whitespace-nowrap select-none",
        "transition-[background-color,border-color,color,box-shadow,opacity] duration-150 ease-out",
        "disabled:opacity-45 disabled:cursor-not-allowed disabled:shadow-none",
        size === "sm" ? "h-7 px-2.5 text-[12.5px]" : "h-8 px-3 text-[13px]",
        VARIANTS[variant], className,
      )}
    >
      {loading ? <Loader2 size={14} className="animate-spin-slow" aria-hidden /> : icon}
      {children}
    </button>
  );
}

export function IconButton({ label, className, children, ...props }:
  ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      {...props}
      className={cx(
        "inline-flex h-7 w-7 items-center justify-center rounded-md text-subtle transition-colors duration-150",
        "hover:bg-surface-2 hover:text-fg disabled:opacity-40", className,
      )}
    >
      {children}
    </button>
  );
}

type Tone = "neutral" | "accent" | "success" | "warning" | "danger";
const TONES: Record<Tone, string> = {
  neutral: "bg-surface-2 text-muted border-border",
  accent: "bg-accent-soft text-accent-text border-transparent",
  success: "bg-success-soft text-success border-success-border",
  warning: "bg-warning-soft text-warning border-warning-border",
  danger: "bg-danger-soft text-danger border-danger-border",
};

export function Badge({ tone = "neutral", className, children }: { tone?: Tone; className?: string; children: ReactNode }) {
  return (
    <span className={cx("inline-flex items-center gap-1 whitespace-nowrap rounded-[5px] border px-1.5 py-px text-[11px] font-medium leading-4",
      TONES[tone], className)}>
      {children}
    </span>
  );
}

export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T; options: { value: T; label: string; icon?: ReactNode }[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="grid auto-cols-fr grid-flow-col gap-0.5 rounded-lg border border-border bg-bg-subtle p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cx(
            "flex h-7 items-center justify-center gap-1.5 rounded-md text-[12.5px] font-medium transition-all duration-150",
            value === o.value ? "bg-surface text-fg shadow-soft" : "text-subtle hover:text-fg",
          )}
        >
          {o.icon}
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Tabs<T extends string>({ value, tabs, onChange, label }: {
  value: T; tabs: { value: T; label: string; count?: number }[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="flex items-center gap-1">
      {tabs.map((t) => (
        <button
          key={t.value}
          type="button"
          role="tab"
          aria-selected={value === t.value}
          onClick={() => onChange(t.value)}
          className={cx(
            "relative h-8 rounded-md px-2.5 text-[13px] font-medium transition-colors duration-150",
            value === t.value ? "text-fg bg-surface-2" : "text-subtle hover:text-fg",
          )}
        >
          {t.label}
          {t.count != null && (
            <span className={cx("ml-1.5 rounded px-1 font-mono text-[11px]", value === t.value ? "text-muted" : "text-subtle")}>
              {t.count}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="inline-flex h-[18px] min-w-[18px] items-center justify-center rounded border border-border bg-surface-2 px-1 font-mono text-[10.5px] text-subtle">
      {children}
    </kbd>
  );
}

export function CopyButton({ text, label = "Copy", className }: { text: string; label?: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <IconButton
      label={copied ? "Copied" : label}
      className={className}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1400);
        } catch {
          /* clipboard unavailable */
        }
      }}
    >
      {copied ? <Check size={14} className="text-success" /> : <Copy size={14} />}
    </IconButton>
  );
}

export function SectionLabel({ children }: { children: ReactNode }) {
  return <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.06em] text-subtle">{children}</div>;
}
