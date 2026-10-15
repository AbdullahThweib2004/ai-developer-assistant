import { Bot, Wrench } from "lucide-react";
import { compact, seconds } from "../lib/format";
import type { Answer } from "../lib/types";
import { Badge } from "./ui";

export function TraceTotals({ answer, wide }: { answer: Answer; wide?: boolean }) {
  const totals = answer.totals;
  if (!totals) return null;
  return (
    <div className={wide ? "grid grid-cols-4 gap-2" : "grid grid-cols-2 gap-2"}>
      {[
        ["Tool calls", String(totals.tool_calls)],
        ["LLM calls", String(totals.llm_calls)],
        ["Tokens", `${compact(totals.input_tokens)} / ${compact(totals.output_tokens)}`],
        ["Duration", seconds(totals.latency_ms)],
      ].map(([k, v]) => (
        <div key={k} className="rounded-lg border border-border bg-surface px-2.5 py-2">
          <div className="text-[10.5px] font-medium uppercase tracking-wide text-subtle">{k}</div>
          <div className="mt-0.5 font-mono text-[13px] tabular-nums text-fg">{v}</div>
        </div>
      ))}
    </div>
  );
}

/** Full trace table: metadata only (no file, issue or prompt content). */
export function TraceTable({ answer }: { answer: Answer }) {
  if (!answer.trace.length) return <p className="text-[13px] text-subtle">No trace for this answer (quick answers make one LLM call).</p>;
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-surface">
      <table className="w-full table-fixed text-[12px]" data-testid="trace-table">
        <thead className="bg-surface-2 text-left text-[10.5px] uppercase tracking-wide text-subtle">
          <tr>
            <th className="w-7 px-2 py-1.5 font-medium">#</th>
            <th className="px-2 py-1.5 font-medium">Step</th>
            <th className="w-[84px] px-2 py-1.5 font-medium">Status</th>
            <th className="w-[58px] px-2 py-1.5 text-right font-medium">Time</th>
          </tr>
        </thead>
        <tbody>
          {answer.trace.map((t) => (
            <tr key={t.step} className="border-t border-border align-top">
              <td className="px-2 py-1.5 font-mono text-subtle">{t.step}</td>
              <td className="min-w-0 px-2 py-1.5">
                <div className="flex items-center gap-1.5 text-fg">
                  {t.kind === "llm" ? <Bot size={12} className="text-accent-text" /> : <Wrench size={12} className="text-subtle" />}
                  <span className="truncate font-mono">{t.kind === "llm" ? "LLM" : t.name}</span>
                </div>
                <div className="mt-0.5 truncate font-mono text-[11px] text-subtle" title={JSON.stringify(t.args ?? {})}>
                  {t.kind === "llm"
                    ? `${compact(t.input_tokens)} in · ${compact(t.output_tokens)} out · ${t.tool_calls_requested ?? 0} tool calls`
                    : `${JSON.stringify(t.args ?? {})}${t.result_chars != null ? ` → ${compact(t.result_chars)} chars` : ""}`}
                </div>
              </td>
              <td className="px-2 py-1.5">
                <Badge tone={t.status === "error" ? "danger" : t.status === "rejected" ? "warning" : "neutral"}>
                  {t.error_category ?? t.status.replace("_", " ")}
                </Badge>
              </td>
              <td className="px-2 py-1.5 text-right font-mono tabular-nums text-subtle">{seconds(t.duration_ms)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
