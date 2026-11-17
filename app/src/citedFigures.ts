import { createContext } from "react";

import type { FigurePlace } from "./api";

/** The figures placed in the element a citation opened at, for the viewer to draw:
 * boxes on its PDF page, or ranges in its text. Empty when it opened from no
 * sentence, or nothing could be placed. */
export const CitedFigures = createContext<FigurePlace[]>([]);

/** What a caption says was highlighted: the figures placed, and only those. */
export function highlighted(places: FigurePlace[]): string | null {
  const shown = places.map((p) => p.text);
  if (shown.length === 0) return null;
  const listed =
    shown.length < 2
      ? shown.join("")
      : `${shown.slice(0, -1).join(", ")} and ${shown[shown.length - 1]}`;
  return `Highlighted: ${listed}`;
}
