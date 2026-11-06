import { useState } from "react";

import { getDocumentText } from "../api";
import { useLoad } from "../useLoad";
import { WINDOW } from "./storedWindowSize";

function windowOf(ordinal: number): number {
  return Math.floor(ordinal / WINDOW) * WINDOW;
}

// Stored text a window at a time, opening on the window that holds the citation.
export function useStoredWindow(id: string, citedOrdinal: number | null) {
  const [offset, setOffset] = useState(
    citedOrdinal === null ? 0 : windowOf(citedOrdinal),
  );
  const text = useLoad(
    () => getDocumentText(id, offset, WINDOW),
    `${id}-${String(offset)}`,
  );
  return {
    offset,
    setOffset,
    elements: text.data?.elements ?? [],
    total: text.data?.total ?? 0,
    loaded: text.data !== undefined,
    error: text.error,
  };
}
