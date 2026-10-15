import { AlertTriangle, CheckCircle2, CircleDot, ExternalLink, HelpCircle, Lock, ShieldAlert, ShieldCheck, X, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { GithubMark } from "./GithubMark";
import { api, ApiError } from "../lib/api";
import type { Draft, SimilarIssue, SubmissionResult } from "../lib/types";
import { Markdown } from "./Markdown";
import { Badge, Button, IconButton, SectionLabel, Segmented, cx } from "./ui";

type View = "write" | "preview" | "split";

export interface DraftApi {
  editDraft: typeof api.editDraft;
  similar: typeof api.similar;
  approve: typeof api.approve;
  cancelDraft: typeof api.cancelDraft;
}

function ResultCard({ result }: { result: SubmissionResult }) {
  if (result.status === "created") {
    return (
      <div className="animate-fade-up rounded-xl border border-success-border bg-success-soft p-3.5" data-testid="draft-result-created">
        <div className="flex items-center gap-2 text-[13.5px] font-semibold text-success">
          <CheckCircle2 size={16} /> Issue #{result.number} created
        </div>
        <a href={result.html_url ?? undefined} target="_blank" rel="noopener noreferrer"
           className="mt-1 inline-flex items-center gap-1 font-mono text-[12px] text-fg underline underline-offset-2">
          {result.html_url} <ExternalLink size={12} />
        </a>
      </div>
    );
  }
  const unknown = result.status === "unknown";
  return (
    <div className={cx("animate-fade-up rounded-xl border p-3.5", unknown ? "border-warning-border bg-warning-soft" : "border-danger-border bg-danger-soft")}
         data-testid={`draft-result-${result.status}`}>
      <div className={cx("flex items-center gap-2 text-[13.5px] font-semibold", unknown ? "text-warning" : "text-danger")}>
        {unknown ? <HelpCircle size={16} /> : <XCircle size={16} />}
        {unknown ? "Outcome unknown: the issue may or may not exist" : "Issue not created"}
      </div>
      <p className="mt-1 text-[12.5px] text-fg">{result.message}</p>
      {result.possible_matches.map((m) => (
        <a key={m.number} href={m.html_url} target="_blank" rel="noopener noreferrer"
           className="mt-1 flex items-center gap-1 text-[12px] text-fg underline underline-offset-2">
          Possible match #{m.number}: {m.title} <ExternalLink size={11} />
        </a>
      ))}
      <p className="mt-1.5 text-[11.5px] text-muted">This draft had its one creation attempt. Check the repository before trying again with a new draft.</p>
    </div>
  );
}

/**
 * Review, edit and (maybe) approve a GitHub issue draft.
 *
 * Safety (Phase 7, unchanged): the destination repo is shown at the top; edits are saved to the
 * server and produce a new hash; approving requires typing the repo name exactly, is disabled
 * while there are errors or unsaved edits, and any edit clears the typed confirmation; one attempt.
 */
export function DraftDrawer({ initial, onClose, client = api }: {
  initial: Draft; onClose: (outcome: "cancelled" | "closed") => void; client?: DraftApi;
}) {
  const [draft, setDraft] = useState<Draft>(initial);
  const [title, setTitle] = useState(initial.title);
  const [body, setBody] = useState(initial.body);
  const [labels, setLabels] = useState(initial.labels.join(", "));
  const [confirm, setConfirm] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [similar, setSimilar] = useState<SimilarIssue[]>([]);
  const [acknowledgeSimilar, setAcknowledgeSimilar] = useState(false);
  const [result, setResult] = useState<SubmissionResult | null>(initial.result);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>("split");
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!draft.errors.length) client.similar(draft.id).then((r) => setSimilar(r.similar)).catch(() => setSimilar([]));
  }, [draft.id, draft.draft_hash, draft.errors.length, client]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !submitting) void cancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const edit = (next: { title?: string; body?: string; labels?: string }) => {
    if (next.title !== undefined) setTitle(next.title);
    if (next.body !== undefined) setBody(next.body);
    if (next.labels !== undefined) setLabels(next.labels);
    setConfirm(""); // any edit requires typing the confirmation again
    setAcknowledgeSimilar(false);
    setDirty(true);
    window.clearTimeout(timer.current);
    const t = next.title ?? title, b = next.body ?? body, l = next.labels ?? labels;
    timer.current = window.setTimeout(async () => {
      setSaving(true);
      try {
        const saved = await client.editDraft(draft.id, t, b, l.split(",").map((s) => s.trim()).filter(Boolean));
        setDraft(saved);
        setDirty(false);
      } catch (e) {
        setError(e instanceof ApiError ? e.info.message : "Couldn't save the draft.");
      } finally {
        setSaving(false);
      }
    }, 450);
  };

  const attempted = draft.attempted || (result !== null && result.status !== "needs_confirmation");
  const needsSimilar = result?.status === "needs_confirmation" || similar.length > 0;
  const matches = confirm === draft.repo;
  const canApprove = !attempted && !dirty && !saving && !submitting && draft.errors.length === 0 && matches
    && (!needsSimilar || acknowledgeSimilar);

  const approve = async () => {
    if (!canApprove) return;
    setSubmitting(true);
    setError(null);
    try {
      const response = await client.approve(draft.id, draft.draft_hash, confirm, acknowledgeSimilar);
      setResult(response.result);
      setDraft(response.draft);
      if (response.result.status === "needs_confirmation") setSimilar(response.result.similar_issues);
    } catch (e) {
      setError(e instanceof ApiError ? e.info.message : "Approval failed.");
      setConfirm("");
    } finally {
      setSubmitting(false);
    }
  };

  async function cancel() {
    if (attempted) return onClose("closed");
    try { await client.cancelDraft(draft.id); } catch { /* nothing was created either way */ }
    onClose("cancelled");
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="Draft GitHub issue">
      <div className="absolute inset-0 bg-overlay backdrop-blur-[2px]" onClick={() => !submitting && void cancel()} />
      <div className="animate-fade-up relative flex h-full w-[min(760px,100vw)] flex-col border-l border-border bg-bg shadow-pop" data-testid="draft-drawer">
        <header className="border-b border-border px-5 pb-4 pt-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-[14px] font-semibold"><CircleDot size={15} className="text-subtle" /> Draft GitHub issue</div>
            <IconButton label="Cancel and close" onClick={() => void cancel()}><X size={16} /></IconButton>
          </div>
          <div className="mt-3 flex items-center gap-3 rounded-xl border border-border-strong bg-surface px-3.5 py-2.5">
            <GithubMark size={18} className="shrink-0 text-fg" />
            <div className="min-w-0">
              <div className="text-[10.5px] font-semibold uppercase tracking-[0.08em] text-subtle">Destination repository</div>
              <div className="truncate font-mono text-[14px] font-semibold text-fg" data-testid="draft-repo">{draft.repo}</div>
            </div>
            <Badge tone="warning" className="ml-auto shrink-0"><Lock size={10} /> Creates a public record</Badge>
          </div>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
          <label className="block">
            <SectionLabel>Title</SectionLabel>
            <input
              value={title}
              disabled={attempted}
              onChange={(e) => edit({ title: e.target.value })}
              className="h-9 w-full rounded-lg border border-border bg-surface px-3 text-[13.5px] text-fg outline-none transition-colors focus:border-accent disabled:opacity-60"
              aria-label="Issue title"
            />
          </label>

          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <SectionLabel>Body</SectionLabel>
              <div className="w-[220px]">
                <Segmented label="Body view" value={view} onChange={setView}
                  options={[{ value: "write", label: "Write" }, { value: "split", label: "Split" }, { value: "preview", label: "Preview" }]} />
              </div>
            </div>
            <div className={cx("grid gap-3", view === "split" ? "grid-cols-2" : "grid-cols-1")}>
              {view !== "preview" && (
                <textarea
                  value={body}
                  disabled={attempted}
                  onChange={(e) => edit({ body: e.target.value })}
                  aria-label="Issue body"
                  spellCheck={false}
                  className="h-[380px] w-full resize-none rounded-lg border border-border bg-surface p-3 font-mono text-[12px] leading-[1.6] text-fg outline-none focus:border-accent disabled:opacity-60"
                />
              )}
              {view !== "write" && (
                <div className="h-[380px] overflow-y-auto rounded-lg border border-border bg-surface p-3 text-[13px]" data-testid="draft-preview">
                  <div className="mb-2 border-b border-border pb-2 text-[15px] font-semibold">{title || "Untitled"}</div>
                  <Markdown>{body}</Markdown>
                </div>
              )}
            </div>
          </div>

          <label className="block">
            <SectionLabel>Labels (optional, comma-separated)</SectionLabel>
            <input
              value={labels}
              disabled={attempted}
              onChange={(e) => edit({ labels: e.target.value })}
              placeholder="bug, auth"
              aria-label="Issue labels"
              className="h-9 w-full rounded-lg border border-border bg-surface px-3 text-[13px] text-fg outline-none focus:border-accent"
            />
          </label>

          {draft.errors.length > 0 && (
            <div className="rounded-xl border border-danger-border bg-danger-soft p-3" data-testid="draft-errors">
              <div className="mb-1 flex items-center gap-1.5 text-[12.5px] font-semibold text-danger"><ShieldAlert size={14} /> Blocked: fix before approving</div>
              <ul className="list-disc space-y-0.5 pl-5 text-[12.5px] text-fg">{draft.errors.map((e) => <li key={e}>{e}</li>)}</ul>
            </div>
          )}
          {draft.warnings.length > 0 && (
            <div className="rounded-xl border border-border bg-surface p-3">
              <div className="mb-1 text-[12px] font-semibold text-muted">Applied automatically</div>
              <ul className="list-disc space-y-0.5 pl-5 text-[12px] text-muted">{draft.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
            </div>
          )}
          {similar.length > 0 && (
            <div className="rounded-xl border border-warning-border bg-warning-soft p-3" data-testid="draft-similar">
              <div className="mb-1.5 flex items-center gap-1.5 text-[12.5px] font-semibold text-warning"><AlertTriangle size={14} /> Similar open issues exist</div>
              {similar.map((s) => (
                <a key={s.number} href={s.html_url} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1 text-[12.5px] text-fg hover:underline">
                  #{s.number} {s.title} <span className="text-subtle">({Math.round(s.similarity * 100)}% similar)</span>
                </a>
              ))}
              {!attempted && (
                <label className="mt-2 flex items-center gap-2 text-[12.5px] text-fg">
                  <input type="checkbox" checked={acknowledgeSimilar} onChange={(e) => setAcknowledgeSimilar(e.target.checked)} />
                  I checked these. Create a new issue anyway.
                </label>
              )}
            </div>
          )}
          {error && <div className="rounded-lg border border-danger-border bg-danger-soft px-3 py-2 text-[12.5px] text-danger" role="alert">{error}</div>}
          {result && result.status !== "needs_confirmation" && <ResultCard result={result} />}
        </div>

        {!attempted && (
          <footer className="border-t border-border bg-bg-subtle px-5 py-4">
            <label className="block text-[12.5px] text-muted" htmlFor="confirm-repo">
              To approve, type <span className="font-mono font-semibold text-fg">{draft.repo}</span>
            </label>
            <div className="mt-2 flex items-center gap-2">
              <input
                id="confirm-repo"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                autoComplete="off"
                spellCheck={false}
                placeholder="owner/repo"
                aria-label="Type the repository name to confirm"
                className={cx(
                  "h-9 min-w-0 flex-1 rounded-lg border bg-surface px-3 font-mono text-[13px] text-fg outline-none transition-colors",
                  confirm && !matches ? "border-danger-border" : matches ? "border-success-border" : "border-border focus:border-accent",
                )}
              />
              <Button variant="ghost" onClick={() => void cancel()}>Cancel</Button>
              <Button variant="consequential" onClick={approve} disabled={!canApprove} loading={submitting}
                      icon={<ShieldCheck size={14} />} data-testid="approve-button" className="h-9 px-3.5">
                Approve and create
              </Button>
            </div>
            <p className="mt-2 text-[11.5px] text-subtle">
              {saving ? "Saving edits…" : dirty ? "Unsaved edits…" : draft.errors.length ? "Approval is blocked until the problems above are fixed."
                : "One attempt per draft. Editing clears the confirmation."}
            </p>
          </footer>
        )}
      </div>
    </div>
  );
}
