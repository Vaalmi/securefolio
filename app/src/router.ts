import { useEffect, useMemo } from "react";
import { useLocation, useNavigate } from "react-router";

// The app's routes are hash routes: #/path/segments?key=value. React Router
// reads them; `go` works from anywhere, including a deep link from the shell.

export interface Route {
  path: string[];
  params: URLSearchParams;
}

export function useRoute(): Route {
  const { pathname, search } = useLocation();
  return useMemo(
    () => ({
      path: pathname.split("/").filter(Boolean),
      params: new URLSearchParams(search),
    }),
    [pathname, search],
  );
}

let navigate: ((to: string) => unknown) | undefined;

/** Lets `go` reach the router from outside a component. */
export function useRouterBridge(): void {
  const router = useNavigate();
  useEffect(() => {
    navigate = (to) => router(to);
    return () => {
      navigate = undefined;
    };
  }, [router]);
}

export function go(to: string): void {
  if (navigate) void navigate(to);
  else window.location.hash = `#${to}`;
}

/** The current route with query values set, or removed where a value is null. */
export function withParams(
  route: Route,
  changes: Record<string, string | null>,
): string {
  const params = new URLSearchParams(route.params);
  for (const [key, value] of Object.entries(changes)) {
    if (value === null) params.delete(key);
    else params.set(key, value);
  }
  const query = params.toString();
  const path = `/${route.path.join("/")}`;
  return query ? `${path}?${query}` : path;
}

/** The current route with one query value set or removed. */
export function withParam(
  route: Route,
  key: string,
  value: string | null,
): string {
  return withParams(route, { [key]: value });
}
