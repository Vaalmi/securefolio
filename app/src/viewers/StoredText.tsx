import type { ReactElement } from "react";

import { getDocumentPage } from "../api";
import { useLoad } from "../useLoad";
import { useStoredWindow } from "./useStoredWindow";
import { WindowBar } from "./WindowBar";

function Paragraphs({
  elements,
}: Readonly<{ elements: { id: string; text: string }[] }>): ReactElement {
  return (
    <>
      {elements.map((element) => (
        <p key={element.id} style={{ whiteSpace: "pre-wrap", marginBottom: 8 }}>
          {element.text}
        </p>
      ))}
    </>
  );
}

/** The text SecureFolio stored for one page, when the original can't be shown. Nothing is
 * highlighted here, so nothing points at the wrong place. */
export function StoredPage({
  id,
  page,
}: Readonly<{ id: string; page: number }>): ReactElement {
  const text = useLoad(
    () => getDocumentPage(id, page),
    `${id}-${String(page)}`,
  );
  return (
    <div className="stored-text" aria-label="Stored text">
      <h2 className="h-sm" style={{ marginBottom: 12 }}>
        Stored text · page {page}
      </h2>
      <Paragraphs elements={text.data?.elements ?? []} />
    </div>
  );
}

/** All of a document's stored text, a window at a time. */
export function StoredTextView({ id }: Readonly<{ id: string }>): ReactElement {
  const text = useStoredWindow(id, null);
  return (
    <div className="stored-text" aria-label="Stored text">
      <h2 className="h-sm" style={{ marginBottom: 12 }}>
        Stored text
      </h2>
      <WindowBar {...text} />
      <Paragraphs elements={text.elements} />
    </div>
  );
}
