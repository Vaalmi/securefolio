import { useEffect } from "react";
import type { ReactElement } from "react";

import {
  getDocumentFile,
  getDocumentPage,
  getDocumentPreview,
  getDocumentSheets,
} from "../api";
import type { Locator, Rect, StoredElement } from "../api";
import { useDataUrl } from "../useDataUrl";
import { useLoad } from "../useLoad";
import { useStoredWindow } from "./useStoredWindow";
import { WindowBar } from "./WindowBar";

interface TextProps {
  id: string;
  citedId: string | null;
  citedOrdinal: number | null;
  label: string;
}

function cited(element: StoredElement, citedId: string | null) {
  const on = element.id === citedId;
  return { on, id: on ? "cited-passage" : undefined };
}

function useCitationInView(citedId: string | null, ready: boolean): void {
  useEffect(() => {
    if (citedId && ready) {
      document
        .getElementById("cited-passage")
        ?.scrollIntoView({ block: "center" });
    }
  }, [citedId, ready]);
}

function sheetOf(locator: Locator): string {
  return locator.kind === "sheet" ? locator.sheet : "";
}

function rowOf(locator: Locator): number {
  if (locator.kind !== "sheet") return 0;
  return Number(/\d+/.exec(locator.range)?.[0] ?? 0);
}

/** A spreadsheet's extracted cells, with the cited row marked. A window of rows can
 * span sheets, so each sheet in it has its own table; the tabs jump to a sheet's start. */
export function SheetView({
  id,
  citedId,
  citedOrdinal,
  label,
}: Readonly<TextProps>): ReactElement {
  const text = useStoredWindow(id, citedOrdinal);
  const sheets = useLoad(() => getDocumentSheets(id), id);
  const here = text.elements.at(0);
  const names = [...new Set(text.elements.map((e) => sheetOf(e.locator)))];
  useCitationInView(citedId, text.loaded);
  return (
    <div className="sheet" role="region" aria-label={label}>
      <div className="sheet-tabs">
        {(sheets.data ?? []).map((start) => {
          const on =
            here !== undefined && sheetOf(here.locator) === start.sheet;
          return (
            <button
              key={start.sheet}
              type="button"
              className={on ? "on" : undefined}
              aria-pressed={on}
              onClick={() => {
                text.setOffset(start.first_ordinal);
              }}
            >
              {start.sheet}
            </button>
          );
        })}
      </div>
      <WindowBar {...text} />
      {names.map((name) => (
        <table key={name} className="sheet-grid">
          <caption className="sr-only">Sheet {name}</caption>
          <tbody>
            {text.elements
              .filter((e) => sheetOf(e.locator) === name)
              .map((element) => {
                const mark = cited(element, citedId);
                return (
                  <tr
                    key={element.id}
                    id={mark.id}
                    className={mark.on ? "hl" : undefined}
                  >
                    <th scope="row">{rowOf(element.locator)}</th>
                    {element.text.split(" | ").map((cell, i) => (
                      <td key={i}>{cell}</td>
                    ))}
                  </tr>
                );
              })}
          </tbody>
        </table>
      ))}
    </div>
  );
}

/** One slide's extracted text. */
export function SlideView({
  id,
  page,
  citedId,
  label,
}: Readonly<{
  id: string;
  page: number;
  citedId: string | null;
  label: string;
}>): ReactElement {
  const text = useLoad(
    () => getDocumentPage(id, page),
    `${id}-${String(page)}`,
  );
  return (
    <div
      className="slide"
      role="document"
      aria-label={`${label}, slide ${String(page)}`}
    >
      {(text.data?.elements ?? []).map((element) => {
        const mark = cited(element, citedId);
        return (
          <p key={element.id}>
            <span className={mark.on ? "hl" : undefined} id={mark.id}>
              {element.text}
            </span>
          </p>
        );
      })}
    </div>
  );
}

/** A text or Markdown file, as numbered lines with the cited lines marked. */
export function LinesView({
  id,
  citedId,
  citedOrdinal,
  label,
}: Readonly<TextProps>): ReactElement {
  const text = useStoredWindow(id, citedOrdinal);
  useCitationInView(citedId, text.loaded);
  return (
    <div className="lines" role="document" aria-label={label}>
      <WindowBar {...text} />
      {text.elements.map((element) => {
        const start =
          element.locator.kind === "lines" ? element.locator.start : 1;
        const mark = cited(element, citedId);
        return element.text.split("\n").map((line, i) => (
          <div
            key={`${element.id}-${String(i)}`}
            className={`line ${mark.on ? "hl" : ""}`}
            id={i === 0 ? mark.id : undefined}
          >
            <span className="ln" aria-hidden="true">
              {start + i}
            </span>
            <span className="mono">{line || " "}</span>
          </div>
        ));
      })}
    </div>
  );
}

/** An email: headers and message text only, so remote images and scripts can't run. */
export function EmailView({
  id,
  citedId,
  citedOrdinal,
}: Readonly<Omit<TextProps, "label">>): ReactElement {
  const text = useStoredWindow(id, citedOrdinal);
  useCitationInView(citedId, text.loaded);
  return (
    <div className="stored-text">
      <WindowBar {...text} />
      {text.elements.map((element) => {
        const mark = cited(element, citedId);
        return (
          <p
            key={element.id}
            style={{ marginBottom: 10, whiteSpace: "pre-wrap" }}
          >
            <span className={mark.on ? "hl" : undefined} id={mark.id}>
              {element.text}
            </span>
          </p>
        );
      })}
      <p className="xs muted" style={{ marginTop: 12 }}>
        Remote images are blocked in emails.
      </p>
    </div>
  );
}

/** DOCX and saved HTML: their extracted structure, never the raw markup. */
export function StructureView({
  id,
  citedId,
  citedOrdinal,
  label,
}: Readonly<TextProps>): ReactElement {
  const text = useStoredWindow(id, citedOrdinal);
  useCitationInView(citedId, text.loaded);
  return (
    <div className="stored-text" role="document" aria-label={label}>
      <WindowBar {...text} />
      {text.elements.map((element) => {
        const mark = cited(element, citedId);
        const anchor =
          element.locator.kind === "anchor"
            ? element.locator.anchor
            : undefined;
        return element.kind === "heading" ? (
          <h2
            key={element.id}
            className="h-sm"
            data-anchor={anchor}
            id={mark.id}
          >
            <span className={mark.on ? "hl" : undefined}>{element.text}</span>
          </h2>
        ) : (
          <p
            key={element.id}
            data-anchor={anchor}
            id={mark.id}
            style={{ whiteSpace: "pre-wrap" }}
          >
            <span className={mark.on ? "hl" : undefined}>{element.text}</span>
          </p>
        );
      })}
    </div>
  );
}

/** A picture, as a data URL, since the file route needs the token. A HEIC comes
 * through its preview, as a web view can't draw one. */
export function ImageView({
  id,
  kind,
  rects,
  label,
}: Readonly<{
  id: string;
  kind: string;
  rects: Rect[];
  label: string;
}>): ReactElement {
  const bytes = kind === "HEIC" ? getDocumentPreview : getDocumentFile;
  const picture = useDataUrl(() => bytes(id), id);
  if (picture.error) {
    return (
      <p role="alert" className="small">
        {picture.error}
      </p>
    );
  }
  return (
    <div className="pdf-page" data-testid="image-page">
      {picture.data && (
        <img
          src={picture.data}
          alt={label}
          style={{ display: "block", maxWidth: "100%" }}
        />
      )}
      {rects.map((rect, i) => (
        <span
          key={i}
          id={i === 0 ? "cited-passage" : undefined}
          className="pdf-area"
          style={{
            left: `${String(rect.x0 * 100)}%`,
            top: `${String(rect.y0 * 100)}%`,
            width: `${String((rect.x1 - rect.x0) * 100)}%`,
            height: `${String((rect.y1 - rect.y0) * 100)}%`,
          }}
        />
      ))}
    </div>
  );
}
