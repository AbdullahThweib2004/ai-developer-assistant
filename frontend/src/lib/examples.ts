import type { Project } from "./types";

export const DEMO_PROJECT = "fastapi-auth-demo";

export const DEMO_EXAMPLES = [
  "Why do requests to /users/me return 401 even though I send a valid bearer token?",
  "Where does the API read the JWT signing secret from?",
  "How does the server parse the Authorization header?",
  "Are there FastAPI issues about HTTPBearer returning 403 instead of 401?",
];

const SKIP = new Set(["tests", "test", "docs", "examples", "scripts", ".github", "__init__.py", "setup.py"]);

/** Example questions built from the selected project's real (indexed) file names. */
export function exampleQuestions(project: Project | null): string[] {
  if (!project) return [];
  if (project.name === DEMO_PROJECT) return DEMO_EXAMPLES;
  const files = project.file_list ?? [];
  const out: string[] = [];
  const modules = new Map<string, number>();
  for (const file of files) {
    const parts = file.split("/");
    const top = parts.length > 1 ? parts[0] : parts[0].replace(/\.(py|ts|js|go|rs)$/, "");
    if (!top || SKIP.has(top) || top.startsWith(".") || /\.(md|txt|json|ya?ml|toml|lock|cfg|ini)$/i.test(top)) continue;
    modules.set(top, (modules.get(top) ?? 0) + 1);
  }
  const ranked = [...modules.entries()].sort((a, b) => b[1] - a[1]).map(([m]) => m);
  if (ranked[0]) out.push(`How does ${ranked[0]} work?`);
  const config = files.find((f) => /(^|\/)(config|settings)(\.[a-z]+)?$|(^|\/)(config|settings)\.[a-z]+$/i.test(f) || /\.env\.example$/.test(f));
  out.push(config ? `Where is configuration loaded? (see ${config})` : "Where is configuration loaded?");
  if (ranked[1]) out.push(`What does ${ranked[1]} do, and how is it used?`);
  if (files.some((f) => /(^|\/)README\.md$/i.test(f))) out.push("What does this project do, according to the README?");
  else if (files.some((f) => /^tests?\//.test(f))) out.push("How are the tests organized?");
  else out.push("What are the main entry points of this project?");
  return out.slice(0, 4);
}
