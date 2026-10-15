import { PanelRightOpen } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Composer, type ComposerHandle } from "./components/Composer";
import { DraftDrawer } from "./components/DraftDrawer";
import { EmptyState } from "./components/EmptyState";
import { EvidencePanel, type EvidenceTab } from "./components/EvidencePanel";
import { MessageView } from "./components/MessageView";
import { Sidebar, type IndexState } from "./components/Sidebar";
import { Toasts, type Toast } from "./components/Toasts";
import { IconButton } from "./components/ui";
import { api, ApiError, streamEvents } from "./lib/api";
import { applyTheme, readTheme, type ThemeChoice } from "./lib/theme";
import type { Answer, ApiErrorInfo, Draft, Message, Mode, Project, Source, Status, Step } from "./lib/types";

const LANG: Record<string, string> = { py: "python", md: "markdown", json: "json", yaml: "yaml", yml: "yaml", ts: "typescript", js: "javascript" };
let toastId = 0;

function useWidth(): number {
  const [width, setWidth] = useState(() => window.innerWidth);
  useEffect(() => {
    const onResize = () => setWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return width;
}

function quickAnswer(data: any): Answer {
  const cited = new Set<string>(data.cited ?? []);
  const sources: Source[] = (data.sources ?? []).map((s: any) => ({
    cite_id: s.source_id, kind: "project", label: s.file_path, cited: cited.has(s.source_id), file_path: s.file_path,
    location: `chars ${s.start_char}-${s.end_char}`, score: s.score, excerpt: s.content, labels: [],
    language: LANG[String(s.file_path).split(".").pop() ?? ""] ?? null, start_line: null,
  }));
  return {
    answer_markdown: data.answer_markdown, sections: data.sections, causes: data.causes, project_sources: sources,
    github_sources: [], invalid_citations: data.invalid_citations ?? [], trace: [], model: data.model,
    insufficient_evidence: data.insufficient_evidence, suggestions: data.suggestions,
    totals: { tool_calls: 0, cached_calls: 0, rejected_calls: 0, llm_calls: data.model ? 1 : 0,
      input_tokens: data.input_tokens ?? 0, output_tokens: data.output_tokens ?? 0, latency_ms: data.latency?.total_ms ?? 0,
      llm_ms: data.latency?.llm_ms ?? 0, tools_ms: 0 },
  };
}

export default function App() {
  const width = useWidth();
  const [theme, setTheme] = useState<ThemeChoice>(readTheme);
  const [status, setStatus] = useState<Status | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectPath, setProjectPath] = useState<string | null>(null);
  const [repo, setRepo] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("auto");
  const [quick, setQuick] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [indexState, setIndexState] = useState<IndexState>({ running: false, percent: 0, label: "" });
  const [indexedAt, setIndexedAt] = useState<Record<string, number>>({});
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => window.innerWidth < 960);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [evidenceTab, setEvidenceTab] = useState<EvidenceTab>("sources");
  const [evidenceWidth, setEvidenceWidth] = useState(420);
  const [activeMessage, setActiveMessage] = useState<string | null>(null);
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [now, setNow] = useState(() => Date.now());

  const session = useRef<{ id: string; key: string } | null>(null);
  const abort = useRef<AbortController | null>(null);
  const composer = useRef<ComposerHandle>(null);
  const thread = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);

  const toast = useCallback((tone: Toast["tone"], title: string, hint?: string | null) => {
    setToasts((t) => [...t.slice(-3), { id: ++toastId, tone, title, hint }]);
  }, []);
  const toastError = useCallback((e: unknown) => {
    const info: ApiErrorInfo = e instanceof ApiError ? e.info : { category: "network", message: "Can't reach the local server.", hint: "Is it still running?" };
    toast("error", info.message, info.hint);
  }, [toast]);

  // ---- bootstrap ------------------------------------------------------------------------
  useEffect(() => { applyTheme(theme); }, [theme]);
  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    const onChange = () => theme === "system" && applyTheme("system");
    media?.addEventListener("change", onChange);
    return () => media?.removeEventListener("change", onChange);
  }, [theme]);

  const loadProjects = useCallback(async () => {
    const { projects: list } = await api.projects();
    setProjects((prev) => {
      const extra = prev.filter((p) => !list.some((l) => l.path === p.path));
      return [...list, ...extra];
    });
    return list;
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const s = await api.status();
        setStatus(s);
        setRepo(s.github.default_repo && s.github.read_repos.includes(s.github.default_repo) ? s.github.default_repo : null);
        const list = await loadProjects();
        setProjectPath((p) => p ?? list[0]?.path ?? s.allowed_roots[0] ?? null);
      } catch (e) {
        toastError(e);
      }
    })();
  }, [loadProjects, toastError]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        composer.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const running = messages.some((m) => m.status === "running");
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(timer);
  }, [running]);

  // ---- autoscroll (stops when the user scrolls up) ----------------------------------------------
  useEffect(() => {
    const el = thread.current;
    if (el && stickToBottom.current) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages]);
  const onScroll = () => {
    const el = thread.current;
    if (el) stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  const project = useMemo(() => projects.find((p) => p.path === projectPath) ?? null, [projects, projectPath]);
  const selectProject = (path: string) => {
    if (!projects.some((p) => p.path === path)) {
      setProjects((list) => [...list, { path, name: path.split("/").filter(Boolean).pop() ?? path, indexed: false, chunks: 0, files: 0, model: null }]);
    }
    setProjectPath(path);
  };

  // ---- indexing ----------------------------------------------------------------------------------
  const runIndex = async () => {
    if (!project) return;
    setIndexState({ running: true, percent: 4, label: "Starting…" });
    try {
      await streamEvents("/api/index", { path: project.path }, (event, data) => {
        if (event === "progress") {
          if (data.type === "discovered") setIndexState({ running: true, percent: 15, label: `Found ${data.files_found} files (${data.files_skipped} skipped)` });
          if (data.type === "parsed") setIndexState({ running: true, percent: 25, label: `Parsed ${data.documents} documents` });
          if (data.type === "chunked") setIndexState({ running: true, percent: 35, label: `Split into ${data.chunks} chunks` });
          if (data.type === "embedding") setIndexState({ running: true, percent: 35 + (60 * data.done) / Math.max(1, data.total), label: `Embedding ${data.done}/${data.total} chunks` });
        } else if (event === "done") {
          setIndexState({ running: false, percent: 100, label: `Indexed ${data.chunks_indexed} chunks from ${data.files_indexed} files in ${data.duration_seconds}s` });
          setIndexedAt((m) => ({ ...m, [project.path]: Date.now() }));
          toast("success", "Index ready", `${data.chunks_indexed} chunks from ${data.files_indexed} files.`);
        } else if (event === "error") {
          throw new ApiError(400, data);
        }
      });
      await loadProjects();
    } catch (e) {
      setIndexState({ running: false, percent: 0, label: "" });
      toastError(e);
      return;
    }
    setTimeout(() => setIndexState((s) => (s.running ? s : { running: false, percent: 0, label: "" })), 2500);
  };

  // ---- asking --------------------------------------------------------------------------------------
  const ensureSession = async (): Promise<string> => {
    const key = `${projectPath}|${repo}|${mode}`;
    if (session.current?.key === key) return session.current.id;
    if (session.current) api.deleteSession(session.current.id).catch(() => {});
    const created = await api.createSession(projectPath!, mode === "project" ? null : repo, mode);
    session.current = { id: created.id, key };
    return created.id;
  };

  const update = (id: string, fn: (m: Message) => Message) => setMessages((list) => list.map((m) => (m.id === id ? fn(m) : m)));

  const send = async (question: string) => {
    if (!projectPath || running) return;
    const id = `m${Date.now()}`;
    const kind = quick ? "quick" : "investigate";
    stickToBottom.current = true;
    setMessages((list) => [...list, { id, question, kind, status: "running", steps: [], llmCalls: 0, tokens: { input: 0, output: 0 }, startedAt: Date.now(), repo: mode === "project" ? null : repo }]);
    setActiveMessage(id);
    const controller = new AbortController();
    abort.current = controller;
    const fail = (info: ApiErrorInfo) => {
      update(id, (m) => ({ ...m, status: "error", error: info, finishedAt: Date.now() }));
      toast("error", info.message, info.hint);
    };
    try {
      if (kind === "quick") {
        await streamEvents("/api/ask", { path: projectPath, question }, (event, data) => {
          if (event === "answer") update(id, (m) => ({ ...m, answer: quickAnswer(data) }));
          if (event === "error") fail(data);
        }, controller.signal);
      } else {
        const sessionId = await ensureSession();
        await streamEvents("/api/investigate", { session_id: sessionId, question }, (event, data) => {
          if (event === "step_started") {
            update(id, (m) => ({ ...m, steps: [...m.steps, { callId: data.call_id, name: data.name, args: data.args ?? {}, status: "running" } as Step] }));
          } else if (event === "step_finished") {
            update(id, (m) => {
              const steps = [...m.steps];
              const index = steps.map((s) => s.callId).lastIndexOf(data.call_id);
              const step: Step = { callId: data.call_id, name: data.name, args: data.args ?? {}, status: data.status,
                durationMs: data.duration_ms, resultCount: data.result_count, errorCategory: data.error_category };
              if (index >= 0) steps[index] = { ...steps[index], ...step, args: steps[index].args };
              else steps.push(step);
              return { ...m, steps };
            });
          } else if (event === "llm_call") {
            update(id, (m) => ({ ...m, llmCalls: m.llmCalls + 1, tokens: { input: m.tokens.input + (data.input_tokens ?? 0), output: m.tokens.output + (data.output_tokens ?? 0) } }));
          } else if (event === "answer") {
            update(id, (m) => ({ ...m, answer: data as Answer }));
          } else if (event === "cancelled") {
            update(id, (m) => ({ ...m, status: "cancelled", finishedAt: Date.now() }));
          } else if (event === "error") {
            fail(data);
          }
        }, controller.signal);
      }
      update(id, (m) => (m.status === "running" ? { ...m, status: m.answer ? "done" : "cancelled", finishedAt: Date.now() } : m));
    } catch (e) {
      if (controller.signal.aborted) {
        update(id, (m) => ({ ...m, status: "cancelled", finishedAt: Date.now() }));
      } else if (e instanceof ApiError) {
        fail(e.info);
      } else {
        fail({ category: "network", message: "Lost connection to the local server.", hint: "Check that it's still running." });
      }
    } finally {
      abort.current = null;
    }
  };

  const stop = () => abort.current?.abort();

  // ---- evidence & drafts ---------------------------------------------------------------------------
  const openCitation = useCallback((messageId: string, citeId: string) => {
    setActiveMessage(messageId);
    setEvidenceTab(citeId.startsWith("G") ? "github" : "sources");
    setHighlighted(citeId);
    setEvidenceOpen(true);
  }, []);
  const showEvidence = (messageId: string) => {
    setActiveMessage(messageId);
    setHighlighted(null);
    setEvidenceOpen(true);
  };
  const prepareIssue = async (messageId: string) => {
    const message = messages.find((m) => m.id === messageId);
    if (!message?.answer?.turn_id || !session.current) return;
    try {
      setDraft(await api.prepareDraft(session.current.id, message.answer.turn_id));
    } catch (e) {
      toastError(e);
    }
  };

  const active = messages.find((m) => m.id === activeMessage) ?? messages[messages.length - 1] ?? null;
  const overlayEvidence = width < 1100;
  const notIndexed = project && !project.indexed;

  return (
    <div className="flex h-full overflow-hidden bg-bg text-fg">
      <Sidebar
        status={status} projects={projects} selected={project} onSelectProject={selectProject} onIndex={runIndex}
        indexState={indexState} repo={repo} onRepo={setRepo} mode={mode} onMode={setMode}
        collapsed={sidebarCollapsed} onToggle={() => setSidebarCollapsed((v) => !v)} theme={theme} onTheme={setTheme}
        indexedAt={project ? indexedAt[project.path] : undefined}
      />

      <main className="relative flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 shrink-0 items-center justify-between border-b border-border px-4">
          <div className="flex min-w-0 items-center gap-2 text-[13px]">
            <span className="truncate font-medium text-fg">{project?.name ?? "No project"}</span>
            <span className="text-subtle">/</span>
            <span className="truncate text-muted">{quick ? "Quick answer" : `Investigate · ${mode}`}</span>
            {repo && mode !== "project" && <span className="hidden truncate font-mono text-[12px] text-subtle md:inline">· {repo}</span>}
          </div>
          {!evidenceOpen && (
            <IconButton label="Open evidence panel" onClick={() => setEvidenceOpen(true)}><PanelRightOpen size={16} /></IconButton>
          )}
        </header>

        <div ref={thread} onScroll={onScroll} className="flex-1 overflow-y-auto" data-testid="thread">
          {messages.length === 0 ? (
            <EmptyState projectName={project?.name} onPick={(q) => (notIndexed ? composer.current?.setText(q) : send(q))} />
          ) : (
            <div className="pb-6 pt-2">
              {messages.map((m) => (
                <MessageView key={m.id} message={m} onOpenCitation={openCitation} onShowEvidence={showEvidence}
                  onPrepareIssue={prepareIssue} writeEnabled={!!status?.github.write_enabled}
                  active={evidenceOpen && active?.id === m.id} now={now} />
              ))}
            </div>
          )}
        </div>

        <Composer
          ref={composer} running={running} quick={quick} onQuick={setQuick} onSend={send} onStop={stop}
          disabled={!project || !!notIndexed}
          disabledReason={!project ? "Choose a project first" : "Index this project first (sidebar → Index project)"}
        />
      </main>

      {evidenceOpen && (
        overlayEvidence ? (
          <div className="fixed inset-y-0 right-0 z-40 shadow-pop">
            <EvidencePanel message={active} tab={evidenceTab} onTab={setEvidenceTab} highlighted={highlighted}
              onClose={() => setEvidenceOpen(false)} width={Math.min(420, width - 48)} onResize={() => {}} />
          </div>
        ) : (
          <EvidencePanel message={active} tab={evidenceTab} onTab={setEvidenceTab} highlighted={highlighted}
            onClose={() => setEvidenceOpen(false)} width={evidenceWidth} onResize={setEvidenceWidth} />
        )
      )}

      {draft && (
        <DraftDrawer initial={draft} onClose={(outcome) => {
          setDraft(null);
          if (outcome === "cancelled") toast("info", "Draft cancelled", "Nothing was created.");
        }} />
      )}
      <Toasts toasts={toasts} onDismiss={(id) => setToasts((t) => t.filter((x) => x.id !== id))} />
    </div>
  );
}
