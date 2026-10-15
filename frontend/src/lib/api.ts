import type { ApiErrorInfo, Draft, Mode, Project, SimilarIssue, Status, SubmissionResult } from "./types";

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

export const api = {
  status: () => request<Status>("GET", "/api/status"),
  projects: () => request<{ projects: Project[] }>("GET", "/api/projects"),
  createSession: (path: string, repo: string | null, mode: Mode) =>
    request<{ id: string; tools: string[]; repo: string | null }>("POST", "/api/sessions", { path, repo, mode }),
  deleteSession: (id: string) => request<{ deleted: boolean }>("DELETE", `/api/sessions/${id}`),
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
};
