export type Mode = "auto" | "project" | "github";

export interface Status {
  demo: boolean;
  llm: { provider: string; model: string | null };
  embedding_model: string;
  github: {
    default_repo: string | null;
    read_repos: string[];
    authenticated_reads: boolean;
    write_enabled: boolean;
    write_repos: string[];
  };
  limits: { max_tool_calls: number; max_iterations: number; max_github_calls: number };
  allowed_roots: string[];
  versions: Record<string, string | null>;
}

export interface Project {
  path: string;
  name: string;
  indexed: boolean;
  chunks: number;
  files: number;
  model: string | null;
  display_path?: string;
  indexed_at?: number | null;
  file_types?: Record<string, number>;
  file_list?: string[];
}

export interface Source {
  cite_id: string;
  kind: "project" | "github";
  label: string;
  cited: boolean;
  file_path?: string | null;
  location?: string | null;
  score?: number | null;
  start_line?: number | null;
  end_line?: number | null;
  language?: string | null;
  excerpt?: string | null;
  repo?: string | null;
  number?: number | null;
  url?: string | null;
  is_pull_request?: boolean | null;
  state?: string | null;
  labels: string[];
  title?: string | null;
}

export interface TraceStep {
  step: number;
  iteration: number;
  kind: "llm" | "tool";
  name: string;
  status: string;
  duration_ms: number;
  args?: Record<string, unknown>;
  result_chars?: number;
  error_category?: string;
  input_tokens?: number;
  output_tokens?: number;
  tool_calls_requested?: number;
}

export interface Cause {
  rank: number;
  kind: "evidence" | "hypothesis" | "unlabeled";
  markdown: string;
}

export interface Answer {
  turn_id?: string;
  answer_markdown: string;
  sections: { heading: string; markdown: string }[];
  causes: Cause[];
  project_sources: Source[];
  github_sources: Source[];
  invalid_citations: string[];
  limits_hit?: string[];
  github_disabled?: boolean;
  totals?: {
    tool_calls: number; cached_calls: number; rejected_calls: number; llm_calls: number;
    input_tokens: number; output_tokens: number; latency_ms: number; llm_ms: number; tools_ms: number;
  };
  trace: TraceStep[];
  model?: string | null;
  prompt_version?: string;
  insufficient_evidence?: boolean;
  suggestions?: string[];
}

export interface Step {
  callId: string;
  name: string;
  args: Record<string, unknown>;
  status: "running" | "ok" | "error" | "cached" | "rejected";
  durationMs?: number;
  resultCount?: number | null;
  errorCategory?: string | null;
}

export interface ApiErrorInfo {
  category: string;
  message: string;
  hint?: string | null;
}

export interface Message {
  id: string;
  question: string;
  kind: "investigate" | "quick";
  status: "running" | "done" | "error" | "cancelled";
  steps: Step[];
  llmCalls: number;
  tokens: { input: number; output: number };
  answer?: Answer;
  error?: ApiErrorInfo;
  startedAt: number;
  finishedAt?: number;
  repo?: string | null;
  mode?: Mode;
  sessionId?: string;
}

export interface Draft {
  id: string;
  repo: string;
  title: string;
  body: string;
  labels: string[];
  draft_hash: string;
  errors: string[];
  warnings: string[];
  attempted: boolean;
  result: SubmissionResult | null;
}

export interface SimilarIssue {
  number: number;
  title: string;
  html_url: string;
  similarity: number;
}

export interface SubmissionResult {
  status: "created" | "failed" | "unknown" | "blocked" | "needs_confirmation";
  number?: number | null;
  html_url?: string | null;
  error_category?: string | null;
  message: string;
  similar_issues: SimilarIssue[];
  possible_matches: { number: number; title: string; html_url: string }[];
}

export interface RateLimit {
  limit: number | null;
  remaining: number | null;
  used: number | null;
  reset_at: string | null;
  resource: string | null;
  authenticated: boolean;
}

export interface GitHubStatus {
  configured: boolean;
  reachable: boolean;
  authenticated: boolean;
  repo: string | null;
  message: string | null;
  rate_limit: RateLimit | null;
  repository?: { description: string; default_branch: string; stars: number; open_issues_count: number; html_url: string; visibility: string | null };
}

export interface ProjectInfo { path: string; name: string; display_path: string; git: { branch: string; detached: boolean } | null }

export interface ProjectFile { path: string; file_type: string; size_bytes: number; indexed: boolean }
export interface ProjectFiles {
  project_name: string; total_files: number; truncated: boolean; skipped_by_reason: Record<string, number>;
  file_types: Record<string, number>; files: ProjectFile[];
}
export interface FileView {
  file_path: string; start_line: number; end_line: number; total_lines: number; truncated: boolean;
  lines: string[]; language: string | null;
}
export interface SearchHit { file_path: string; score: number; start_line: number | null; end_line: number | null; excerpt: string; language: string | null }

export interface IssueSummary {
  number: number; title: string; state: string; is_pull_request: boolean; labels: string[]; author: string | null;
  created_at: string; updated_at: string; comments: number; html_url: string; body_excerpt: string;
}
export interface IssueComment { position: number; author: string | null; author_association: string | null; created_at: string; body: string; html_url: string }
export interface IssueDetail extends Omit<IssueSummary, "comments" | "body_excerpt"> {
  repo: string; state_reason: string | null; closed_at: string | null; body: string; body_truncated: boolean;
  comments_count: number; comments: IssueComment[]; omitted_comments: number; omitted_after_position: number | null;
}
export interface PullSummary {
  number: number; title: string; state: string; draft: boolean; author: string | null; base_branch: string;
  head_branch: string; created_at: string; updated_at: string; merged_at: string | null; html_url: string;
}
export interface PullDetail extends PullSummary {
  repo: string; merged: boolean; closed_at: string | null; body: string; commits: number | null; additions: number | null;
  deletions: number | null; changed_files: number | null; files_truncated: boolean;
  files: { filename: string; status: string; additions: number; deletions: number; previous_filename: string | null }[];
  closing_references: { keyword: string; repo: string; number: number }[];
}
export interface AuditEntry {
  timestamp: string; action: string; repo: string; channel: string; draft_hash: string; title_length: number;
  issue_number?: number | null; issue_url?: string | null; error_category?: string | null;
}
