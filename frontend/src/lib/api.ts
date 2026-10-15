import type {
  ApiErrorInfo, AuditEntry, Conversation, Draft, FileView, GitHubStatus, IssueDetail, IssueSummary, Mode, Project, ProjectFiles,
  ProjectInfo, PullDetail, PullSummary, RateLimit, SearchHit, SimilarIssue, Status, SubmissionResult,
} from "./types";

/** The per-launch session token: injected into the page by the server (or Vite env in dev). */
export function sessionToken(): string {
  const meta = document.querySelector('meta[name="ai-dev-assistant-token"]');
  return meta?.getAttribute("content") || (import.meta.env.VITE_SESSION_TOKEN as string | undefined) || "";
}

export class ApiError extends Error {
  info: ApiErrorInfo;
  status: number;
  constructor(status: number, info: ApiErrorInfo) {
    super(info.message);
    this.status = status;
    this.info = info;
  }
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const response = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json", "X-Session-Token": sessionToken() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = (data as { detail?: ApiErrorInfo }).detail;
    throw new ApiError(response.status, detail ?? { category: "error", message: `Request failed (${response.status}).` });
  }
  return data as T;
}

export type SseHandler = (event: string, data: any) => void;

/** POST that streams Server-Sent Events back (EventSource only supports GET). Abortable. */
export async function streamEvents(url: string, body: unknown, onEvent: SseHandler, signal?: AbortSignal): Promise<void> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Session-Token": sessionToken(), Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body) {
    const data = await response.json().catch(() => ({}));
    const detail = (data as { detail?: ApiErrorInfo }).detail;
    throw new ApiError(response.status, detail ?? { category: "error", message: `Request failed (${response.status}).` });
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const raw = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      let name = "message";
      let data = "";
      for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (data) onEvent(name, JSON.parse(data));
      boundary = buffer.indexOf("\n\n");
    }
  }
}

const qs = (params: Record<string, string | number | null | undefined>) =>
  new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== "").map(([k, v]) => [k, String(v)])).toString();

export const api = {
  status: () => request<Status>("GET", "/api/status"),
  projects: () => request<{ projects: Project[] }>("GET", "/api/projects"),
  conversations: (path: string) =>
    request<{ project: string; conversations: Conversation[]; save_history: boolean }>("GET", `/api/conversations?${qs({ path })}`),
  createConversation: (path: string, repo: string | null, mode: Mode) =>
    request<Conversation>("POST", "/api/conversations", { path, repo, mode }),
  conversation: (id: string) => request<Conversation>("GET", `/api/conversations/${id}`),
  updateConversation: (id: string, change: { title?: string; mode?: Mode }) =>
    request<Conversation>("PATCH", `/api/conversations/${id}`, change),
  deleteConversation: (id: string) => request<{ deleted: boolean }>("DELETE", `/api/conversations/${id}`),
  clearConversations: (path: string) => request<{ deleted: number }>("DELETE", `/api/conversations?${qs({ path })}`),
  clearHistory: () => request<{ deleted: number }>("DELETE", "/api/history"),
  historySettings: () => request<{ save_history: boolean }>("GET", "/api/history/settings"),
  setHistorySettings: (saveHistory: boolean) =>
    request<{ save_history: boolean }>("PUT", "/api/history/settings", { save_history: saveHistory }),
  prepareDraft: (sessionId: string, turnId: string) =>
    request<Draft>("POST", `/api/sessions/${sessionId}/drafts`, { turn_id: turnId }),
  editDraft: (id: string, title: string, body: string, labels: string[]) =>
    request<Draft>("PUT", `/api/drafts/${id}`, { title, body, labels }),
  similar: (id: string) => request<{ similar: SimilarIssue[]; checked: boolean }>("GET", `/api/drafts/${id}/similar`),
  approve: (id: string, draftHash: string, typed: string, confirmSimilar = false) =>
    request<{ result: SubmissionResult; draft: Draft }>("POST", `/api/drafts/${id}/approve`, {
      draft_hash: draftHash, typed_repo_confirmation: typed, confirm_similar: confirmSimilar,
    }),
  cancelDraft: (id: string) => request<{ cancelled: boolean }>("POST", `/api/drafts/${id}/cancel`),

  // read-only explore endpoints
  projectInfo: (path: string) => request<ProjectInfo>("GET", `/api/project/info?${qs({ path })}`),
  projectFiles: (path: string) => request<ProjectFiles>("GET", `/api/project/files?${qs({ path })}`),
  projectFile: (path: string, file: string, start?: number) =>
    request<FileView>("GET", `/api/project/file?${qs({ path, file, start })}`),
  projectSearch: (path: string, q: string, topK = 6) =>
    request<{ query: string; hits: SearchHit[] }>("GET", `/api/project/search?${qs({ path, q, top_k: topK })}`),
  githubStatus: (repo: string | null) => request<GitHubStatus>("GET", `/api/github/status?${qs({ repo })}`),
  issues: (repo: string | null, q = "", state = "all") =>
    request<{ repo: string; query: string; total_count: number; items: IssueSummary[]; rate_limit: RateLimit | null }>(
      "GET", `/api/github/issues?${qs({ repo, q, state })}`),
  issue: (repo: string | null, number: number) => request<IssueDetail>("GET", `/api/github/issues/${number}?${qs({ repo })}`),
  pulls: (repo: string | null, state = "open") =>
    request<{ repo: string; items: PullSummary[]; rate_limit: RateLimit | null }>("GET", `/api/github/pulls?${qs({ repo, state })}`),
  pull: (repo: string | null, number: number) => request<PullDetail>("GET", `/api/github/pulls/${number}?${qs({ repo })}`),
  audit: () => request<{ entries: AuditEntry[]; error: string | null }>("GET", "/api/activity/audit"),
};
