import { AlertCircle, CheckCircle2, Info, X } from "lucide-react";
import { useEffect, useRef } from "react";
import { cx } from "./ui";

export interface Toast { id: number; tone: "error" | "success" | "info"; title: string; hint?: string | null }

export function Toasts({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: number) => void }) {
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[360px] flex-col gap-2" aria-live="polite">
      {toasts.map((t) => <ToastItem key={t.id} toast={t} onDismiss={onDismiss} />)}
    </div>
  );
}

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: (id: number) => void }) {
  // Keep the latest callback without restarting the timer when the parent re-renders.
  const dismiss = useRef(onDismiss);
  dismiss.current = onDismiss;
  useEffect(() => {
    const timer = setTimeout(() => dismiss.current(toast.id), toast.tone === "error" ? 7000 : 4000);
    return () => clearTimeout(timer);
  }, [toast.id, toast.tone]);
  const Icon = toast.tone === "error" ? AlertCircle : toast.tone === "success" ? CheckCircle2 : Info;
  return (
    <div role={toast.tone === "error" ? "alert" : "status"}
      className="animate-fade-up pointer-events-auto flex gap-2.5 rounded-xl border border-border bg-surface p-3 shadow-pop">
      <Icon size={16} className={cx("mt-0.5 shrink-0", toast.tone === "error" ? "text-danger" : toast.tone === "success" ? "text-success" : "text-accent-text")} />
      <div className="min-w-0 flex-1">
        <div className="text-[13px] font-medium text-fg">{toast.title}</div>
        {toast.hint && <div className="mt-0.5 text-[12px] text-muted">{toast.hint}</div>}
      </div>
      <button type="button" aria-label="Dismiss" onClick={() => onDismiss(toast.id)} className="h-5 text-subtle hover:text-fg"><X size={14} /></button>
    </div>
  );
}
