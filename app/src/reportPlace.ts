import type { Locator, ReportPlace } from "./api";

// Where in a document the right answer is, by its kind, in the form its citations
// take (backend/reports.py, PLACE_OF). A Word document or a web page has no
// numbered place: the fact typed is what a later answer must hold.
export type PlaceKind = ReportPlace["kind"];
export type Where = "doc" | "none"; // in a document, or not in the library
export const PLACE_OF: Record<string, PlaceKind> = {
  PDF: "page",
  PNG: "page",
  JPEG: "page",
  HEIC: "page",
  WEBP: "page",
  PPTX: "slide",
  XLSX: "cells",
  CSV: "cells",
  EML: "message",
  MD: "lines",
  TXT: "lines",
};
export const LABEL: Record<PlaceKind, string> = {
  page: "Page",
  slide: "Slide",
  message: "Message",
  lines: "Lines",
  cells: "Sheet and cells",
};
export const EXAMPLE: Partial<Record<PlaceKind, string>> = {
  lines: "For example: 13–14",
  cells: "For example: Bills, B4:F4",
};

/** A citation's place, as the form's field starts: its number, or its words. */
export function placeText(locator: Locator | undefined): string {
  if (!locator) return "";
  switch (locator.kind) {
    case "pdf":
      return String(locator.page);
    case "slide":
      return String(locator.slide);
    case "message":
      return String(locator.message);
    case "lines":
      return `${String(locator.start)}–${String(locator.end)}`;
    case "sheet":
      return `${locator.sheet}, ${locator.range}`;
    default:
      return "";
  }
}

/** A number from 1, with nothing after its point: 2.5 is no line or page. */
function whole(n: number): boolean {
  return Number.isInteger(n) && n >= 1;
}

/** What the field says, as the backend takes it, or null when it can't be read. */
export function placeOf(kind: PlaceKind, text: string): ReportPlace | null {
  const said = text.trim();
  if (kind === "lines") {
    const parts = said.replace(/\s/g, "").split(/[-–]/);
    const [start = 0, end = start] = parts.map(Number);
    const read = parts.length <= 2 && whole(start) && whole(end);
    return read && start <= end ? { kind, start, end } : null;
  }
  if (kind === "cells") {
    const at = said.lastIndexOf(",");
    if (at < 1) return null;
    return {
      kind,
      sheet: said.slice(0, at).trim(),
      cells: said.slice(at + 1).trim(),
    };
  }
  const number = Number(said);
  if (!whole(number)) return null;
  if (kind === "page") return { kind, page: number };
  if (kind === "slide") return { kind, slide: number };
  return { kind, message: number };
}

export function passRule(
  where: Where,
  kind: PlaceKind | undefined,
  fact: string,
): string {
  if (where === "none")
    return "Asked again later, the answer passes only if it says it couldn't find enough evidence. A fact you type is kept with the report, for you.";
  if (!kind)
    return "This document has no pages to point to, so the answer passes only if it cites it and holds the fact you type.";
  const holds = fact.trim()
    ? " and holds this fact"
    : ", and holds the fact if you give one";
  return `Asked again later, the answer passes only if it cites this ${LABEL[kind].toLowerCase()}${holds}. Citing the right document isn't enough.`;
}

export function beforeLine(before: number): string {
  if (before === 0) return "";
  const asked = before === 1 ? "question" : `${String(before)} questions`;
  return ` Saved with the ${asked} before it in this chat, so it's asked the same way.`;
}

/** What the form still needs before it can be saved, or undefined. */
export function missingOf(
  where: Where,
  picked: boolean,
  kind: PlaceKind | undefined,
  loc: string,
  fact: string,
): string | undefined {
  if (where === "none") return undefined;
  if (!picked) return "Choose the document that holds the right answer.";
  if (kind && !placeOf(kind, loc))
    return `Say which ${LABEL[kind].toLowerCase()} hold the right answer.`;
  if (!kind && !fact.trim())
    return "Type the fact the answer should have held.";
  return undefined;
}
