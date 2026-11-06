import { render } from "@testing-library/react";
import { HashRouter } from "react-router";
import { vi } from "vitest";

import { App } from "./App";
import { calls } from "./fakeBackend";
import { AppProvider } from "./store";

export function open(route: string) {
  window.location.hash = route;
  return render(
    <HashRouter>
      <AppProvider>
        <App />
      </AppProvider>
    </HashRouter>,
  );
}

export function posted(path: string): boolean {
  return calls.some(
    (call) => call.method !== "GET" && call.path.startsWith(path),
  );
}

export function installBrowser(narrow = false): void {
  window.matchMedia = vi.fn(
    () =>
      ({
        matches: narrow,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }) as unknown as MediaQueryList,
  );
}
