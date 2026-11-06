import { useEffect } from "react";

import { getAskRoute } from "./api";
import type { AskRoute, Mode, RouteQuery, ThreadScope } from "./api";
import { useLoad } from "./useLoad";

// Who answers a question, from the backend's route, and the one way every Ask box
// names it: Cloud AI or Local AI.

export function askLabel(route: AskRoute | undefined, mode: Mode): string {
  if (!route) return "Ask"; // not known yet, so nothing is assumed
  if (route.local) return "Ask Local AI";
  return mode === "deep" ? "Ask Cloud AI · Deep" : "Ask Cloud AI";
}

// A thread's scope, as the route reads it.
export function routeQuery(
  mode: Mode,
  scope: ThreadScope | undefined,
  thread?: string,
): RouteQuery {
  const query: RouteQuery = { mode, ...(thread ? { thread } : {}) };
  if (!scope) return query;
  if (scope.kind === "library")
    return { ...query, include_private: scope.include_private };
  if (scope.kind === "collection") return { ...query, collection: scope.id };
  return { ...query, documents: scope.ids };
}

// How often a route is asked again: soon while it's blocked, so the box opens once
// the model can answer (Ollama started, say), and now and then while it isn't, so a
// key removed, a model changed or Offline turned on, in this window or another, is
// seen without leaving the page. Coming back to the window asks at once.
const BLOCKED_RECHECK_MS = 1500;
const RECHECK_MS = 10_000;

// The route for this query only: while a newer query's answer is on its way, the
// last one doesn't count, so nothing is asked on a route for another scope or mode.
export function useAskRoute(query: RouteQuery): {
  data: AskRoute | undefined;
  error: string | undefined;
} {
  const key = JSON.stringify(query);
  const loaded = useLoad(
    () => getAskRoute(query).then((route) => ({ key, route })),
    key,
    {
      every: (found) =>
        found.route.blocked !== null ? BLOCKED_RECHECK_MS : RECHECK_MS,
      while: () => true,
    },
  );
  const fresh = loaded.data?.key === key ? loaded.data.route : undefined;
  const { reload } = loaded;
  useEffect(() => {
    const again = (): void => {
      if (document.visibilityState !== "hidden") reload();
    };
    window.addEventListener("focus", again);
    document.addEventListener("visibilitychange", again);
    return () => {
      window.removeEventListener("focus", again);
      document.removeEventListener("visibilitychange", again);
    };
  }, [reload]);
  return { data: fresh, error: fresh ? undefined : loaded.error };
}

/** Whether a question may be asked now: the route for it has come, and isn't blocked. */
export function canAsk(route: AskRoute | undefined): route is AskRoute {
  return route !== undefined && !route.blocked;
}
