import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { Toast } from "../components/Toasts";
import { api, ApiError, streamEvents } from "./api";
import { navigate } from "./router";
import { applyTheme, readTheme, type ThemeChoice } from "./theme";
import type { Answer, ApiErrorInfo, Draft, GitHubStatus, Message, Mode, Project, ProjectInfo, Source, Status, Step } from "./types";

export const APP_NAME = "Dev Assistant";

export type ChatMode = Mode | "quick";

export interface IndexState { running: boolean; percent: number; label: string; error?: string | null }

const LANG: Record<string, string> = { py: "python", md: "markdown", json: "json", yaml: "yaml", yml: "yaml", ts: "typescript", js: "javascript" };
let toastId = 0;

function quickAnswer(data: any): Answer {
  const cited = new Set<string>(data.cited ?? []);
  const sources: Source[] = (data.sources ?? []).map((s: any) => ({
    cite_id: s.source_id, kind: "project", label: s.file_path, cited: cited.has(s.source_id), file_path: s.file_path,
    location: `chars ${s.start_char}-${s.end_char}`, score: s.score, excerpt: s.content, labels: [],
    language: LANG[String(s.file_path).split(".").pop() ?? ""] ?? null, start_line: s.start_line, end_line: s.end_line,
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

function useAppController() {
  const [theme, setThemeState] = useState<ThemeChoice>(readTheme);
  const [status, setStatus] = useState<Status | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectPath, setProjectPath] = useState<string | null>(null);
  const [projectInfo, setProjectInfo] = useState<ProjectInfo | null>(null);
  const [repo, setRepo] = useState<string | null>(null);
  const [githubStatus, setGithubStatus] = useState<GitHubStatus | null>(null);
  const [chatMode, setChatMode] = useState<ChatMode>("auto");
  const [messages, setMessages] = useState<Message[]>([]);
  const [activeMessageId, setActiveMessageId] = useState<string | null>(null);
  const [indexState, setIndexState] = useState<IndexState>({ running: false, percent: 0, label: "" });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [booted, setBooted] = useState(false);

  const sessions = useRef(new Map<string, string>()); // project|repo|mode -> session id
  const abort = useRef<AbortController | null>(null);

  const toast = useCallback((tone: Toast["tone"], title: string, hint?: string | null) => {
    setToasts((t) => [...t.slice(-3), { id: ++toastId, tone, title, hint }]);
  }, []);
  const toastError = useCallback((e: unknown) => {
    const info: ApiErrorInfo = e instanceof ApiError ? e.info : { category: "network", message: "Can't reach the local server.", hint: "Is it still running?" };
    toast("error", info.message, info.hint);
  }, [toast]);
  const dismissToast = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);

  // ---- theme ------------------------------------------------------------------------------
  const setTheme = useCallback((t: ThemeChoice) => setThemeState(t), []);
  useEffect(() => { applyTheme(theme); }, [theme]);
  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    const onChange = () => theme === "system" && applyTheme("system");
    media?.addEventListener("change", onChange);
    return () => media?.removeEventListener("change", onChange);
  }, [theme]);

  // ---- bootstrap ----------------------------------------------------------------------------
  const loadProjects = useCallback(async () => {
    const { projects: list } = await api.projects();
    setProjects((prev) => [...list, ...prev.filter((p) => !list.some((l) => l.path === p.path))]);
    return list;
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const s = await api.status();
        setStatus(s);
        setRepo(s.github.default_repo && s.github.read_repos.includes(s.github.default_repo) ? s.github.default_repo : s.github.read_repos[0] ?? null);
        const list = await loadProjects();
        setProjectPath((p) => p ?? list[0]?.path ?? s.allowed_roots[0] ?? null);
      } catch (e) {
        toastError(e);
      } finally {
        setBooted(true);
      }
    })();
  }, [loadProjects, toastError]);

  useEffect(() => {
    if (!projectPath) return;
    setProjectInfo(null);
    api.projectInfo(projectPath).then(setProjectInfo).catch(() => setProjectInfo(null));
  }, [projectPath]);

  const refreshGithub = useCallback(() => {
    if (!status) return;
    api.githubStatus(repo).then(setGithubStatus).catch(() => setGithubStatus(null));
  }, [repo, status]);
  useEffect(() => { refreshGithub(); }, [refreshGithub]);

  const project = useMemo(() => projects.find((p) => p.path === projectPath) ?? null, [projects, projectPath]);
  const selectProject = useCallback((path: string) => {
    setProjects((list) => list.some((p) => p.path === path) ? list
      : [...list, { path, name: path.split("/").filter(Boolean).pop() ?? path, indexed: false, chunks: 0, files: 0, model: null }]);
    setProjectPath(path);
  }, []);

  // ---- indexing ------------------------------------------------------------------------------
  const runIndex = useCallback(async () => {
    if (!project || indexState.running) return;
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
          toast("success", "Index ready", `${data.chunks_indexed} chunks from ${data.files_indexed} files.`);
        } else if (event === "error") {
          throw new ApiError(400, data);
        }
      });
      await loadProjects();
    } catch (e) {
      setIndexState({ running: false, percent: 0, label: "", error: e instanceof ApiError ? e.info.message : "Indexing failed." });
      toastError(e);
      return;
    }
    setTimeout(() => setIndexState((s) => (s.running ? s : { running: false, percent: 0, label: "" })), 2500);
  }, [project, indexState.running, loadProjects, toast, toastError]);

  // ---- asking ---------------------------------------------------------------------------------
  const running = messages.some((m) => m.status === "running");
  const update = useCallback((id: string, fn: (m: Message) => Message) =>
    setMessages((list) => list.map((m) => (m.id === id ? fn(m) : m))), []);

  const ensureSession = useCallback(async (mode: Mode): Promise<string> => {
    const sessionRepo = mode === "project" ? null : repo;
    const key = `${projectPath}|${sessionRepo}|${mode}`;
    const existing = sessions.current.get(key);
    if (existing) return existing;
    const created = await api.createSession(projectPath!, sessionRepo, mode);
    sessions.current.set(key, created.id);
    return created.id;
  }, [projectPath, repo]);

  const send = useCallback(async (question: string, override?: ChatMode) => {
    if (!projectPath || running) return;
    const chosen = override ?? chatMode;
    const id = `m${Date.now()}`;
    const kind = chosen === "quick" ? "quick" : "investigate";
    const mode: Mode = chosen === "quick" ? "project" : chosen;
    setMessages((list) => [...list, { id, question, kind, status: "running", steps: [], llmCalls: 0, tokens: { input: 0, output: 0 },
      startedAt: Date.now(), repo: mode === "project" ? null : repo, mode }]);
    setActiveMessageId(id);
    navigate("assistant");
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
        const sessionId = await ensureSession(mode);
        update(id, (m) => ({ ...m, sessionId }));
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
      if (controller.signal.aborted) update(id, (m) => ({ ...m, status: "cancelled", finishedAt: Date.now() }));
      else if (e instanceof ApiError) fail(e.info);
      else fail({ category: "network", message: "Lost connection to the local server.", hint: "Check that it's still running." });
    } finally {
      abort.current = null;
    }
  }, [projectPath, running, chatMode, repo, ensureSession, update, toast]);

  const stop = useCallback(() => abort.current?.abort(), []);

  // ---- drafts, navigation helpers --------------------------------------------------------------
  const prepareIssue = useCallback(async (messageId: string) => {
    const message = messages.find((m) => m.id === messageId);
    if (!message?.answer?.turn_id || !message.sessionId) return;
    try {
      setDraft(await api.prepareDraft(message.sessionId, message.answer.turn_id));
    } catch (e) {
      toastError(e);
    }
  }, [messages, toastError]);

  const closeDraft = useCallback((outcome: "cancelled" | "closed") => {
    setDraft(null);
    if (outcome === "cancelled") toast("info", "Draft cancelled", "Nothing was created.");
  }, [toast]);

  const openCode = useCallback((file: string, start?: number | null, end?: number | null) => {
    navigate("code", { file, start: start ?? undefined, end: end ?? undefined });
  }, []);

  const activeMessage = messages.find((m) => m.id === activeMessageId) ?? messages[messages.length - 1] ?? null;

  return {
    booted, theme, setTheme, status, projects, project, projectPath, projectInfo, selectProject, loadProjects,
    repo, setRepo, githubStatus, refreshGithub, chatMode, setChatMode, messages, running, send, stop,
    activeMessage, setActiveMessageId, indexState, runIndex, draft, prepareIssue, closeDraft, openCode,
    toasts, toast, toastError, dismissToast, paletteOpen, setPaletteOpen,
  };
}

export type AppState = ReturnType<typeof useAppController>;

const AppContext = createContext<AppState | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const state = useAppController();
  return <AppContext.Provider value={state}>{children}</AppContext.Provider>;
}

export function useApp(): AppState {
  const state = useContext(AppContext);
  if (!state) throw new Error("useApp outside AppProvider");
  return state;
}

/** Re-renders only the caller, every 100 ms while ``active``: live elapsed time without re-rendering the app. */
export function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}
