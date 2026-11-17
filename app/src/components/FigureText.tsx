import type { ReactElement } from "react";

import type { FigurePlace } from "../api";

/**
 * A cited passage's text, or one line of it starting `offset` characters in, with
 * its placed figures highlighted. Ranges count characters, as the backend does, so
 * the text is cut by characters, never by UTF-16 units.
 */
export function FigureText({
  text,
  places,
  offset = 0,
}: Readonly<{
  text: string;
  places: FigurePlace[];
  offset?: number;
}>): ReactElement {
  const chars = Array.from(text);
  const ranges = places
    .flatMap((p) => (p.kind === "text" ? [p.range] : []))
    .map(([start, end]) => [
      Math.max(start - offset, 0),
      Math.min(end - offset, chars.length),
    ])
    .filter(([start, end]) => start < end)
    .sort((a, b) => a[0] - b[0]);
  const pieces: ReactElement[] = [];
  let at = 0;
  for (const [start, end] of ranges) {
    if (start < at) continue;
    pieces.push(
      <span key={`t${String(at)}`}>{chars.slice(at, start).join("")}</span>,
    );
    pieces.push(
      <mark key={`f${String(start)}`} className="hl-fig">
        {chars.slice(start, end).join("")}
      </mark>,
    );
    at = end;
  }
  pieces.push(<span key={`t${String(at)}`}>{chars.slice(at).join("")}</span>);
  return <>{pieces}</>;
}
