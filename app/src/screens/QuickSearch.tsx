import { useEffect } from "react";
import type { ReactElement } from "react";

import { SearchAskBox } from "../components/Composer";
import { hideQuickSearch } from "../desktop";

// The window the global hotkey opens. What it opens goes to the main window.
export function QuickSearch(): ReactElement {
  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") void hideQuickSearch();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, []);
  return (
    <main className="qs" aria-label="SecureFolio quick search">
      <h1 className="sr-only">Quick search</h1>
      <div style={{ padding: 10 }}>
        <SearchAskBox focusOnOpen />
      </div>
      <p className="xs muted" style={{ padding: "0 16px 12px" }}>
        Opened with <span className="kbd">⌥ Space</span> from anywhere.{" "}
        <span className="kbd">esc</span> closes.
      </p>
    </main>
  );
}
