import { CircleDot, FileSearch, ShieldCheck } from "lucide-react";
import { type RefObject, useCallback, useEffect, useMemo, useRef } from "react";
import { Composer, type ComposerHandle } from "../components/Composer";
import { MessageView, type MessageActions } from "../components/MessageView";
import { StatusDot } from "../components/ui";
import { exampleQuestions } from "../lib/examples";
import { navigate } from "../lib/router";
import { useApp } from "../lib/store";
import type { Source } from "../lib/types";

function IndexPill() {
  const { project, indexState } = useApp();
  const [tone, label] = indexState.running ? ["accent", "Indexing…"] as const
    : project?.indexed ? ["success", "Project indexed"] as const : ["warning", "Not indexed"] as const;
  const colors = { accent: "border-accent/30 bg-accent-soft text-accent-text", success: "border-success-border bg-success-soft text-success",
    warning: "border-warning-border bg-warning-soft text-warning" }[tone];
  return (
    <span className={`inline-flex h-7 items-center gap-1.5 rounded-full border pl-1.5 pr-2.5 text-[12.5px] font-medium ${colors}`} data-testid="index-pill">
      <StatusDot tone={tone} pulse={indexState.running} /> {label}
    </span>
  );
}

export function AssistantPage({ composerRef }: { composerRef: RefObject<ComposerHandle | null> }) {
  const app = useApp();
  const { messages, project, status, running, send, stop, chatMode, setChatMode, openCode, prepareIssue, setActiveMessageId, repo } = app;
  const thread = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const githubAvailable = !!repo && (status?.github.read_repos.length ?? 0) > 0;
  const examples = useMemo(() => exampleQuestions(project), [project]);
  const notIndexed = !!project && !project.indexed;

  // Smart autoscroll: follow the live steps; when an answer arrives, show it from its start.
  // Stops following when the user scrolls up.
  const answered = useRef(new Set<string>());
  useEffect(() => {
    const el = thread.current;
    if (!el) return;
    const last = messages[messages.length - 1];
    if (last?.answer && !answered.current.has(last.id)) {
      answered.current.add(last.id);
      const start = el.querySelector<HTMLElement>(`#msg-${last.id} [data-answer-start]`);
      if (start && stick.current) el.scrollTo({ top: start.getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop - 16, behavior: "smooth" });
      stick.current = false;
      return;
    }
    if (stick.current) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages]);
  const onScroll = () => {
    const el = thread.current;
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  const openSource = useCallback((s: Source) => {
    if (s.kind === "github") navigate("issues", { number: s.number, repo: s.repo });
    else if (s.file_path) openCode(s.file_path, s.start_line, s.end_line);
  }, [openCode]);

  const actions = useMemo<MessageActions>(() => ({
    onOpenSource: openSource,
    onOpenCitation: (messageId, citeId) => {
      const message = messages.find((m) => m.id === messageId);
      const source = [...(message?.answer?.project_sources ?? []), ...(message?.answer?.github_sources ?? [])].find((s) => s.cite_id === citeId);
      if (source) openSource(source);
    },
    onSearchGitHub: (question) => { stick.current = true; send(question, "github"); },
    onDraft: prepareIssue,
    onSelect: setActiveMessageId,
  }), [openSource, messages, send, prepareIssue, setActiveMessageId]);

  const submit = (q: string) => { stick.current = true; send(q); };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div ref={thread} onScroll={onScroll} className="flex-1 overflow-y-auto" data-testid="thread">
        <div className="mx-auto w-full max-w-[780px] px-8 pt-7">
          <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-5">
            <div>
              <h1 className="text-[24px] font-semibold tracking-[-0.02em] text-fg">Ask your codebase anything</h1>
              <p className="mt-1 text-[14px] text-muted">Understand your code, investigate bugs, and ship with confidence.</p>
            </div>
            <IndexPill />
          </div>

          {messages.length === 0 ? (
            <div className="animate-fade-up py-8" data-testid="empty-state">
              <div className="mb-4 flex flex-wrap gap-x-5 gap-y-2 text-[12.5px] text-muted">
                <span className="flex items-center gap-1.5"><FileSearch size={14} /> Cited project sources</span>
                <span className="flex items-center gap-1.5"><CircleDot size={14} /> GitHub issues as leads</span>
                <span className="flex items-center gap-1.5"><ShieldCheck size={14} /> Issues only with your approval</span>
              </div>
              {project ? (
                <>
                  <div className="mb-2 text-[12px] font-semibold uppercase tracking-[0.06em] text-subtle">Try asking about {project.name}</div>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    {examples.map((q) => (
                      <button key={q} type="button" data-testid="example-question"
                        onClick={() => (notIndexed ? composerRef.current?.setText(q) : submit(q))}
                        className="rounded-xl border border-border bg-surface p-3.5 text-left text-[13.5px] leading-snug text-fg shadow-card transition-[border-color,transform,box-shadow] duration-150 hover:-translate-y-px hover:border-accent/40 hover:shadow-pop">
                        {q}
                      </button>
                    ))}
                  </div>
                  {notIndexed && <p className="mt-3 text-[13px] text-warning">Index this project first (Workspace card in the sidebar).</p>}
                </>
              ) : <p className="text-[13.5px] text-muted">Choose a project in the top bar.</p>}
            </div>
          ) : (
            <div className="pb-4 pt-2">
              {messages.map((m) => (
                <MessageView key={m.id} message={m} actions={actions} writeEnabled={!!status?.github.write_enabled}
                  githubAvailable={githubAvailable} busy={running} />
              ))}
            </div>
          )}
        </div>
      </div>
      <div className="mx-auto w-full max-w-[780px] px-8 pb-5 pt-2">
        <Composer ref={composerRef} running={running} onSend={submit} onStop={stop} mode={chatMode} onMode={setChatMode}
          githubAvailable={githubAvailable} disabled={!project || notIndexed}
          disabledReason={!project ? "Choose a project first" : "Index this project first (sidebar → Index project)"} />
      </div>
    </div>
  );
}
