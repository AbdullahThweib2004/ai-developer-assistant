export type ThemeChoice = "system" | "light" | "dark";

function systemDark(): boolean {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? true;
}

export function readTheme(): ThemeChoice {
  try {
    const saved = localStorage.getItem("theme");
    return saved === "dark" || saved === "system" ? saved : "light";  // light is the default
  } catch {
    return "light";
  }
}

export function applyTheme(choice: ThemeChoice): void {
  const dark = choice === "dark" || (choice === "system" && systemDark());
  document.documentElement.classList.toggle("dark", dark);
  try {
    localStorage.setItem("theme", choice);
  } catch {
    /* storage unavailable: the choice still applies for this page */
  }
}
