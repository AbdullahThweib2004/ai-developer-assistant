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
  indexedAt?: number;
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
