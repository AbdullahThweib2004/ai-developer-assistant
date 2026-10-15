export function seconds(ms?: number | null): string {
  if (ms == null) return "";
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)}s`;
}

export function compact(n?: number | null): string {
  if (n == null) return "–";
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n);
}

export function plural(n: number, word: string, many?: string): string {
  if (n === 1) return `${n} ${word}`;
  return `${n} ${many ?? (/(s|x|ch|sh)$/.test(word) ? `${word}es` : `${word}s`)}`;
}

/** Remove internal untrusted-content wrapper tags for display. */
export function unwrap(text?: string | null): string {
  return (text ?? "").replace(/<\/?untrusted-[a-z-]+[^>]*>\n?/g, "").trim();
}

/** Human description of a tool step, before and after it ran. */
export function describeStep(name: string, args: Record<string, unknown>, repo?: string | null): string {
  const q = typeof args.query === "string" ? `“${args.query}”` : "";
  switch (name) {
    case "search_project": return `Searching project for ${q}`;
    case "read_project_file": return `Reading ${String(args.file_path ?? "a file")}`;
    case "list_project_files": return "Listing project files";
    case "get_index_stats": return "Checking the index";
    case "search_github_issues": return `Searching ${repo ?? "GitHub"} issues for ${q}`;
    case "get_github_issue": return `Opening ${repo ?? ""}#${String(args.number ?? "")}`;
    case "get_github_pull_request": return `Opening pull request #${String(args.number ?? "")}`;
    case "list_github_pull_requests": return `Listing ${repo ?? ""} pull requests`;
    case "get_github_repository": return `Looking up ${repo ?? "the repository"}`;
    case "prepare_github_issue": return "Drafting a GitHub issue";
    default: return name.replace(/_/g, " ");
  }
}

export function stepResult(name: string, count?: number | null): string | null {
  if (count == null) return null;
  if (name === "search_project") return `Found ${plural(count, "match")}`;
  if (name === "search_github_issues") return `Found ${plural(count, "issue")}`;
  if (name === "list_project_files") return plural(count, "file");
  if (name === "list_github_pull_requests") return plural(count, "pull request");
  return null;
}
