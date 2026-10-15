import { cx } from "./ui";

const KINDS: Record<string, { label: string; className: string }> = {
  py: { label: "py", className: "bg-[#3776ab]/12 text-[#2b5f8c] dark:bg-[#3776ab]/25 dark:text-[#8cc4f2]" },
  md: { label: "md", className: "bg-surface-3 text-muted" },
  ts: { label: "ts", className: "bg-[#3178c6]/12 text-[#245d9c] dark:bg-[#3178c6]/25 dark:text-[#93c3f5]" },
  tsx: { label: "tsx", className: "bg-[#3178c6]/12 text-[#245d9c] dark:bg-[#3178c6]/25 dark:text-[#93c3f5]" },
  js: { label: "js", className: "bg-[#f0db4f]/25 text-[#7a6500] dark:text-[#f3e27a]" },
  json: { label: "{}", className: "bg-[#c48a00]/12 text-[#8a6100] dark:text-[#f6c860]" },
  yaml: { label: "yml", className: "bg-[#cb171e]/10 text-[#a31217] dark:text-[#ff9a9e]" },
  yml: { label: "yml", className: "bg-[#cb171e]/10 text-[#a31217] dark:text-[#ff9a9e]" },
  toml: { label: "tml", className: "bg-surface-3 text-muted" },
  env: { label: "env", className: "bg-warning-soft text-warning" },
  pdf: { label: "pdf", className: "bg-danger-soft text-danger" },
};

export function extensionOf(path: string): string {
  const name = path.split("/").pop() ?? path;
  if (name.startsWith(".env")) return "env";
  return name.includes(".") ? name.split(".").pop()!.toLowerCase() : "";
}

/** A small badge that says what kind of file this is (by extension). */
export function FileIcon({ path, size = "md" }: { path: string; size?: "sm" | "md" }) {
  const ext = extensionOf(path);
  const kind = KINDS[ext] ?? { label: ext.slice(0, 3) || "txt", className: "bg-surface-3 text-muted" };
  return (
    <span aria-hidden className={cx(
      "inline-flex shrink-0 items-center justify-center rounded-[5px] font-mono font-semibold uppercase leading-none",
      size === "sm" ? "h-4 min-w-4 px-0.5 text-[8.5px]" : "h-5 min-w-5 px-1 text-[9.5px]", kind.className,
    )}>
      {kind.label}
    </span>
  );
}
