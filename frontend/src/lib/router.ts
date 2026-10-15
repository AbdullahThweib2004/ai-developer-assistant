import { useSyncExternalStore } from "react";

export type Route = "assistant" | "overview" | "knowledge" | "code" | "issues" | "pulls" | "activity" | "settings";

export const PATHS: Record<Route, string> = {
  assistant: "/", overview: "/overview", knowledge: "/knowledge", code: "/code",
  issues: "/issues", pulls: "/pulls", activity: "/activity", settings: "/settings",
};

const EVENT = "app:navigate";

function current(): string {
  return window.location.pathname + window.location.search;
}

function subscribe(callback: () => void): () => void {
  window.addEventListener("popstate", callback);
  window.addEventListener(EVENT, callback);
  return () => {
    window.removeEventListener("popstate", callback);
    window.removeEventListener(EVENT, callback);
  };
}

export function routeOf(pathname: string): Route {
  const found = (Object.entries(PATHS) as [Route, string][]).find(([, p]) => p !== "/" && pathname.startsWith(p));
  return found ? found[0] : "assistant";
}

/** Client-side routing over the History API (the server serves index.html for every page path). */
export function useLocation(): { route: Route; params: URLSearchParams } {
  const location = useSyncExternalStore(subscribe, current, current);
  const url = new URL(location, window.location.origin);
  return { route: routeOf(url.pathname), params: url.searchParams };
}

export function navigate(route: Route, params?: Record<string, string | number | null | undefined>): void {
  const query = params
    ? new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== "").map(([k, v]) => [k, String(v)])).toString()
    : "";
  const target = PATHS[route] + (query ? `?${query}` : "");
  if (target === current()) return;
  window.history.pushState(null, "", target);
  window.dispatchEvent(new Event(EVENT));
}
