import { Check, ChevronDown } from "lucide-react";
import type React from "react";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { cx } from "./ui";

export interface SelectOption<T extends string> {
  value: T;
  label: string;
  description?: string;
  title?: string; // tooltip, e.g. a full path
  icon?: ReactNode;
  right?: ReactNode;
  disabled?: boolean;
}

/**
 * The one dropdown used everywhere (no native <select>): a button plus a listbox popover.
 * Keyboard: Enter/Space/ArrowDown opens; arrows move; Enter selects; Escape closes.
 */
export function Select<T extends string>({
  value, options, onChange, label, placeholder = "Select…", trigger, align = "left", placement = "bottom",
  className, menuClassName, disabled, testId, footer,
}: {
  value: T | null; options: SelectOption<T>[]; onChange: (value: T) => void; label: string; placeholder?: string;
  trigger?: (selected: SelectOption<T> | null, open: boolean) => ReactNode; align?: "left" | "right";
  placement?: "bottom" | "top"; className?: string; menuClassName?: string; disabled?: boolean; testId?: string;
  footer?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const id = useId();
  const selected = options.find((o) => o.value === value) ?? null;

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open]);

  useEffect(() => {
    if (open) {
      setActive(Math.max(0, options.findIndex((o) => o.value === value)));
      requestAnimationFrame(() => list.current?.focus());
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const choose = (option: SelectOption<T> | undefined) => {
    if (!option || option.disabled) return;
    onChange(option.value);
    setOpen(false);
    button.current?.focus();
  };

  const onListKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => Math.min(options.length - 1, i + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => Math.max(0, i - 1)); }
    else if (e.key === "Home") { e.preventDefault(); setActive(0); }
    else if (e.key === "End") { e.preventDefault(); setActive(options.length - 1); }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(options[active]); }
    else if (e.key === "Escape" || e.key === "Tab") { setOpen(false); button.current?.focus(); }
  };

  return (
    <div ref={box} className={cx("relative", className)}>
      <button
        ref={button}
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => { if (e.key === "ArrowDown" && !open) { e.preventDefault(); setOpen(true); } }}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-label={label}
        data-testid={testId}
        className={cx(
          "w-full text-left outline-none transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50",
          !trigger && "flex h-9 items-center gap-2 rounded-lg border border-border bg-surface px-3 text-[13px] text-fg shadow-soft hover:border-border-strong",
        )}
      >
        {trigger ? trigger(selected, open) : (
          <>
            {selected?.icon}
            <span className={cx("min-w-0 flex-1 truncate", !selected && "text-subtle")}>{selected?.label ?? placeholder}</span>
            <ChevronDown size={14} className={cx("shrink-0 text-subtle transition-transform duration-200", open && "rotate-180")} />
          </>
        )}
      </button>
      {open && (
        <div
          ref={list}
          id={id}
          role="listbox"
          tabIndex={-1}
          aria-label={label}
          aria-activedescendant={`${id}-${active}`}
          onKeyDown={onListKey}
          className={cx(
            "animate-fade-up absolute z-50 min-w-full overflow-hidden rounded-xl border border-border bg-surface p-1 shadow-pop outline-none",
            placement === "top" ? "bottom-[calc(100%+6px)]" : "top-[calc(100%+6px)]",
            align === "right" ? "right-0" : "left-0",
            menuClassName,
          )}
        >
          <div className="max-h-[320px] overflow-y-auto">
            {options.map((option, i) => {
              const isSelected = option.value === value;
              return (
                <div
                  key={option.value}
                  id={`${id}-${i}`}
                  role="option"
                  aria-selected={isSelected}
                  aria-disabled={option.disabled || undefined}
                  title={option.title}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => choose(option)}
                  className={cx(
                    "flex cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px]",
                    i === active && "bg-surface-2",
                    option.disabled && "cursor-not-allowed opacity-50",
                  )}
                >
                  {option.icon}
                  <span className="min-w-0 flex-1">
                    <span className={cx("block truncate", isSelected ? "font-medium text-fg" : "text-fg")}>{option.label}</span>
                    {option.description && <span className="block truncate text-[11.5px] text-subtle">{option.description}</span>}
                  </span>
                  {option.right}
                  <Check size={14} className={cx("shrink-0 text-accent-text", !isSelected && "invisible")} />
                </div>
              );
            })}
          </div>
          {footer}
        </div>
      )}
    </div>
  );
}
