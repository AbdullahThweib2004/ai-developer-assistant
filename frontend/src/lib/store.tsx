import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { Toast } from "../components/Toasts";
import { api, ApiError, streamEvents } from "./api";
import { chatIdOf, navigate, navigateChat, routeOf } from "./router";
import { applyTheme, readTheme, type ThemeChoice } from "./theme";
import type {
  Answer, ApiErrorInfo, Conversation, Draft, GitHubStatus, Message, Mode, Project, ProjectInfo, Source, Status, Step, StoredMessage,
} from "./types";

export const APP_NAME = "Dev Assistant";

export type ChatMode = Mode | "quick";

export interface IndexState { running: boolean; percent: number; label: string; error?: string | null }

const LANG: Record<string, string> = { py: "python", md: "markdown", json: "json", yaml: "yaml", yml: "yaml", ts: "typescript", js: "javascript" };
let toastId = 0;

const LAST_KEY = "devassistant:lastConversation:";

function lastConversation(project: string): string | null {
  try { return localStorage.getItem(LAST_KEY + project); } catch { return null; }
}

function rememberConversation(project: string, conversationId: string | null): void {
  try {
    if (conversationId) localStorage.setItem(LAST_KEY + project, conversationId);
    else localStorage.removeItem(LAST_KEY + project);
  } catch { /* storage unavailable: falls back to the most recent conversation */ }
}

/** A stored turn, shown again exactly as it finished. */
export function fromStored(stored: StoredMessage): Message {
  const answer = stored.answer ? (stored.kind === "quick" ? quickAnswer(stored.answer) : (stored.answer as Answer)) : undefined;
  const steps: Step[] = (answer?.trace ?? []).filter((t) => t.kind === "tool").map((t) => ({
    callId: `t${t.step}`, name: t.name, args: t.args ?? {}, durationMs: t.duration_ms, errorCategory: t.error_category ?? null,
    status: (["ok", "error", "cached", "rejected"].includes(t.status) ? t.status : "ok") as Step["status"],
  }));
  return {
    id: stored.id, question: stored.question, kind: stored.kind, status: stored.status, steps,
    llmCalls: answer?.totals?.llm_calls ?? 0,
    tokens: { input: answer?.totals?.input_tokens ?? 0, output: answer?.totals?.output_tokens ?? 0 },
    answer, error: stored.error, startedAt: stored.started_at, finishedAt: stored.finished_at,
    repo: stored.repo, mode: stored.mode, sessionId: stored.session_id,
  };
}

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
  const [chatMode, setChatModeState] = useState<ChatMode>("auto");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  // Messages per conversation: switching back restores a conversation exactly as it was.
  const [messagesByConversation, setMessagesByConversation] = useState<Record<string, Message[]>>({});
  const [saveHistory, setSaveHistory] = useState(true);
  const [activeMessageId, setActiveMessageId] = useState<string | null>(null);
  const [indexState, setIndexState] = useState<IndexState>({ running: false, percent: 0, label: "" });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [booted, setBooted] = useState(false);

  const abort = useRef<AbortController | null>(null);
  const pendingConversation = useRef<string | null>(null); // to open once its project is selected
  const currentProject = useRef<string | null>(null);

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
        const fromUrl = chatIdOf(window.location.pathname);
        const restored = fromUrl ? await api.conversation(fromUrl).catch(() => null) : null;
        if (restored) {
          pendingConversation.current = restored.id;
          addProject(restored.project);
          setProjectPath(restored.project);
        } else {
          if (fromUrl) navigateChat(null, true); // unknown or deleted conversation
          setProjectPath((p) => p ?? list[0]?.path ?? s.allowed_roots[0] ?? null);
        }
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
  currentProject.current = projectPath;
  function addProject(path: string) {
    setProjects((list) => list.some((p) => p.path === path) ? list
      : [...list, { path, name: path.split("/").filter(Boolean).pop() ?? path, indexed: false, chunks: 0, files: 0, model: null }]);
  }

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

  // ---- conversations: each belongs to exactly one project ------------------------------------
  const messages = useMemo(
    () => (activeConversationId ? messagesByConversation[activeConversationId] ?? [] : []),
    [activeConversationId, messagesByConversation]);
  const running = messages.some((m) => m.status === "running");
  const activeConversation = conversations.find((c) => c.id === activeConversationId) ?? null;

  const update = useCallback((conversationId: string, id: string, fn: (m: Message) => Message) =>
    setMessagesByConversation((all) => ({
      ...all, [conversationId]: (all[conversationId] ?? []).map((m) => (m.id === id ? fn(m) : m)),
    })), []);

  const stop = useCallback(() => abort.current?.abort(), []);

  const loadConversations = useCallback(async (path: string) => {
    const { conversations: list, save_history } = await api.conversations(path);
    setConversations(list);
    setSaveHistory(save_history);
    return list;
  }, []);

  /** Keep the URL on /chat/:id while the assistant page is showing. */
  const showConversation = useCallback((conversationId: string | null, replace = true) => {
    if (routeOf(window.location.pathname) === "assistant") navigateChat(conversationId, replace);
  }, []);

  const activate = useCallback((conversation: Conversation) => {
    if (conversation.messages) {
      const restored = conversation.messages.map(fromStored);
      setMessagesByConversation((all) => (all[conversation.id] ? all : { ...all, [conversation.id]: restored }));
    }
    setActiveConversationId(conversation.id);
    setActiveMessageId(null);
    setChatModeState(conversation.mode);
    if (conversation.repo) setRepo(conversation.repo); // a conversation keeps its repository
    rememberConversation(conversation.project, conversation.id);
    showConversation(conversation.id);
  }, [showConversation]);

  /** Open a stored conversation (switching to its project first if needed). */
  const openConversation = useCallback(async (conversationId: string) => {
    if (conversationId === activeConversationId) return;
    stop();
    setDraft(null);
    try {
      const conversation = await api.conversation(conversationId);
      if (conversation.project !== projectPath) {
        pendingConversation.current = conversationId;
        addProject(conversation.project);
        setProjectPath(conversation.project);
        return;
      }
      activate(conversation);
    } catch (e) {
      toastError(e);
    }
  }, [activeConversationId, stop, projectPath, activate, toastError]);

  const newChat = useCallback(() => {
    stop();
    setDraft(null);
    setActiveConversationId(null);
    setActiveMessageId(null);
    if (projectPath) rememberConversation(projectPath, null);
    showConversation(null, false);
  }, [stop, projectPath, showConversation]);

  // Switching project: stop what's running first; the effect below then shows that project's
  // last used (or most recent) conversation, or a new empty chat.
  const selectProject = useCallback((path: string) => {
    if (path === projectPath) return;
    stop();
    setDraft(null);
    addProject(path);
    setProjectPath(path);
  }, [projectPath, stop]);

  useEffect(() => {
    if (!projectPath) return;
    let cancelled = false;
    setActiveConversationId(null);
    setActiveMessageId(null);
    setConversations([]);
    (async () => {
      try {
        const list = await loadConversations(projectPath);
        if (cancelled) return;
        const pending = pendingConversation.current;
        pendingConversation.current = null;
        const target = [pending, lastConversation(projectPath), list[0]?.id].find((id) => id && list.some((c) => c.id === id));
        if (!target) {
          setChatModeState("auto");
          showConversation(null);
          return;
        }
        const conversation = await api.conversation(target);
        if (!cancelled) activate(conversation);
      } catch (e) {
        if (!cancelled) toastError(e);
      }
    })();
    return () => { cancelled = true; };
  }, [projectPath]); // eslint-disable-line react-hooks/exhaustive-deps

  const renameConversation = useCallback(async (id: string, title: string) => {
    try {
      const updated = await api.updateConversation(id, { title });
      setConversations((list) => list.map((c) => (c.id === id ? { ...c, title: updated.title } : c)));
    } catch (e) {
      toastError(e);
    }
  }, [toastError]);

  const deleteConversation = useCallback(async (id: string) => {
    try {
      await api.deleteConversation(id);
      setMessagesByConversation((all) => {
        const next = { ...all };
        delete next[id];
        return next;
      });
      const rest = conversations.filter((c) => c.id !== id);
      setConversations(rest);
      if (id === activeConversationId) {
        setActiveConversationId(null);
        if (rest[0]) void api.conversation(rest[0].id).then(activate).catch(toastError);
        else newChat();
      }
    } catch (e) {
      toastError(e);
    }
  }, [conversations, activeConversationId, activate, newChat, toastError]);

  const clearHistory = useCallback(async () => {
    try {
      await api.clearHistory();
      stop();
      setMessagesByConversation({});
      setConversations([]);
      setActiveConversationId(null);
      showConversation(null);
      toast("success", "History cleared", "All saved conversations were deleted.");
    } catch (e) {
      toastError(e);
    }
  }, [stop, showConversation, toast, toastError]);

  const updateSaveHistory = useCallback(async (value: boolean) => {
    try {
      setSaveHistory((await api.setHistorySettings(value)).save_history);
    } catch (e) {
      toastError(e);
    }
  }, [toastError]);

  /** Picking another repository starts a new chat: a conversation keeps its repository. */
  const selectRepo = useCallback((next: string) => {
    setRepo(next);
    if (activeConversation && activeConversation.repo && activeConversation.repo !== next) newChat();
  }, [activeConversation, newChat]);

  /** The composer's mode; also stored as the conversation's mode. */
  const setChatMode = useCallback((mode: ChatMode) => {
    setChatModeState(mode);
    if (activeConversationId && mode !== "quick") {
      api.updateConversation(activeConversationId, { mode }).catch(() => {});
      setConversations((list) => list.map((c) => (c.id === activeConversationId ? { ...c, mode } : c)));
    }
  }, [activeConversationId]);

  // ---- asking ---------------------------------------------------------------------------------
  const send = useCallback(async (question: string, override?: ChatMode) => {
    if (!projectPath || running) return;
    const chosen = override ?? chatMode;
    const kind = chosen === "quick" ? "quick" : "investigate";
    const mode: Mode = chosen === "quick" ? "project" : chosen;
    let conv = activeConversationId;
    if (!conv) {
      try {
        const created = await api.createConversation(projectPath, repo, chatMode === "quick" ? "auto" : chatMode);
        conv = created.id;
        setConversations((list) => [created, ...list]);
        setActiveConversationId(created.id);
        rememberConversation(projectPath, created.id);
      } catch (e) {
        toastError(e);
        return;
      }
    }
    const conversationId = conv;
    const conversationRepo = activeConversation?.repo ?? repo;
    const id = `m${Date.now()}`;
    setMessagesByConversation((all) => ({
      ...all,
      [conversationId]: [...(all[conversationId] ?? []), { id, question, kind, status: "running", steps: [], llmCalls: 0,
        tokens: { input: 0, output: 0 }, startedAt: Date.now(), repo: mode === "project" ? null : conversationRepo, mode }],
    }));
    setActiveMessageId(id);
    navigateChat(conversationId, !activeConversationId);
    const controller = new AbortController();
    abort.current = controller;
    const fail = (info: ApiErrorInfo) => {
      update(conversationId, id, (m) => ({ ...m, status: "error", error: info, finishedAt: Date.now() }));
      toast("error", info.message, info.hint);
    };
    try {
      if (kind === "quick") {
        await streamEvents("/api/ask", { conversation_id: conversationId, path: projectPath, question }, (event, data) => {
          if (event === "answer") update(conversationId, id, (m) => ({ ...m, answer: quickAnswer(data) }));
          if (event === "error") fail(data);
        }, controller.signal);
      } else {
        await streamEvents("/api/investigate", { conversation_id: conversationId, path: projectPath, question, mode }, (event, data) => {
          if (event === "started") {
            update(conversationId, id, (m) => ({ ...m, sessionId: data.session_id }));
          } else if (event === "step_started") {
            update(conversationId, id, (m) => ({ ...m, steps: [...m.steps, { callId: data.call_id, name: data.name, args: data.args ?? {}, status: "running" } as Step] }));
          } else if (event === "step_finished") {
            update(conversationId, id, (m) => {
              const steps = [...m.steps];
              const index = steps.map((s) => s.callId).lastIndexOf(data.call_id);
              const step: Step = { callId: data.call_id, name: data.name, args: data.args ?? {}, status: data.status,
                durationMs: data.duration_ms, resultCount: data.result_count, errorCategory: data.error_category };
              if (index >= 0) steps[index] = { ...steps[index], ...step, args: steps[index].args };
              else steps.push(step);
              return { ...m, steps };
            });
          } else if (event === "llm_call") {
            update(conversationId, id, (m) => ({ ...m, llmCalls: m.llmCalls + 1,
              tokens: { input: m.tokens.input + (data.input_tokens ?? 0), output: m.tokens.output + (data.output_tokens ?? 0) } }));
          } else if (event === "answer") {
            update(conversationId, id, (m) => ({ ...m, answer: data as Answer }));
          } else if (event === "cancelled") {
            update(conversationId, id, (m) => ({ ...m, status: "cancelled", finishedAt: Date.now() }));
          } else if (event === "error") {
            fail(data);
          }
        }, controller.signal);
      }
      update(conversationId, id, (m) => (m.status === "running" ? { ...m, status: m.answer ? "done" : "cancelled", finishedAt: Date.now() } : m));
    } catch (e) {
      if (controller.signal.aborted) update(conversationId, id, (m) => ({ ...m, status: "cancelled", finishedAt: Date.now() }));
      else if (e instanceof ApiError) fail(e.info);
      else fail({ category: "network", message: "Lost connection to the local server.", hint: "Check that it's still running." });
    } finally {
      if (abort.current === controller) abort.current = null;
      if (currentProject.current === projectPath) loadConversations(projectPath).catch(() => {}); // titles, order
    }
  }, [projectPath, running, chatMode, repo, activeConversationId, activeConversation, update, toast, toastError, loadConversations]);

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
    repo, setRepo: selectRepo, githubStatus, refreshGithub, chatMode, setChatMode, messages, running, send, stop,
    conversations, activeConversation, activeConversationId, openConversation, newChat, renameConversation,
    deleteConversation, clearHistory, saveHistory, updateSaveHistory, loadConversations,
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
