import type { ReactElement } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { WINDOW } from "./storedWindowSize";

export function WindowBar({
  offset,
  total,
  setOffset,
}: Readonly<{
  offset: number;
  total: number;
  setOffset: (offset: number) => void;
}>): ReactElement | null {
  if (total <= WINDOW) return null;
  const last = Math.min(offset + WINDOW, total);
  return (
    <div className="viewer-bar small">
      <button
        type="button"
        className="icon-btn"
        aria-label="Earlier text"
        disabled={offset === 0}
        onClick={() => {
          setOffset(Math.max(0, offset - WINDOW));
        }}
      >
        <ChevronLeft size={16} aria-hidden="true" />
      </button>
      <span role="status">
        {(offset + 1).toLocaleString("en-US")}–{last.toLocaleString("en-US")} of{" "}
        {total.toLocaleString("en-US")}
      </span>
      <button
        type="button"
        className="icon-btn"
        aria-label="Later text"
        disabled={last >= total}
        onClick={() => {
          setOffset(offset + WINDOW);
        }}
      >
        <ChevronRight size={16} aria-hidden="true" />
      </button>
    </div>
  );
}
