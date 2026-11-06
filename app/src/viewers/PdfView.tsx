import { useEffect, useRef, useState } from "react";
import type { ReactElement } from "react";
import { getDocument as openPdf, GlobalWorkerOptions } from "pdfjs-dist";
import type { PDFDocumentProxy } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

import { getDocumentFile } from "../api";
import type { Rect } from "../api";
import { useLoad } from "../useLoad";

GlobalWorkerOptions.workerSrc = workerUrl;
const CSS_DPI_SCALE = 1.3;

interface Password {
  submit: (password: string) => void;
  wrong: boolean;
}

function PasswordPrompt({
  prompt,
  onDecline,
}: Readonly<{
  prompt: Password;
  onDecline: () => void;
}>): ReactElement {
  const [value, setValue] = useState("");
  return (
    <form
      className="stack"
      style={{ width: "min(420px, 100%)" }}
      onSubmit={(event) => {
        event.preventDefault();
        prompt.submit(value);
      }}
    >
      <label htmlFor="pdf-password" style={{ fontWeight: 500 }}>
        This PDF asks for its password to open
      </label>
      <input
        id="pdf-password"
        type="password"
        className="input"
        autoComplete="off"
        value={value}
        onChange={(event) => {
          setValue(event.target.value);
        }}
      />
      {prompt.wrong && <span role="alert">That password didn't open it.</span>}
      <span className="hstack">
        <button type="submit" className="btn primary sm">
          Open
        </button>
        <button type="button" className="btn sm" onClick={onDecline}>
          Show the stored text
        </button>
      </span>
    </form>
  );
}

/** A PDF page, with citations drawn over it from their stored rectangles: a passage
 * highlight only for `words` precision, an area otherwise. */
export function PdfView({
  id,
  page,
  zoom,
  rects,
  precision,
  label,
  onPages,
  onDeclined,
}: Readonly<{
  id: string;
  page: number;
  zoom: number;
  rects: Rect[];
  precision: string | null;
  label: string;
  onPages: (pages: number) => void;
  onDeclined: () => void;
}>): ReactElement {
  const canvas = useRef<HTMLCanvasElement>(null);
  const file = useLoad(() => getDocumentFile(id), id);
  const [pdf, setPdf] = useState<PDFDocumentProxy>();
  const [prompt, setPrompt] = useState<Password>();
  const [failed, setFailed] = useState<string>();

  useEffect(() => {
    if (!file.data) return;
    const task = openPdf({
      data: new Uint8Array(file.data.slice(0)),
      useSystemFonts: true,
    });
    task.onPassword = (submit: (password: string) => void, reason: number) => {
      setPrompt({ submit, wrong: reason === 2 });
    };
    task.promise
      .then((opened) => {
        setPrompt(undefined);
        setPdf(opened);
        onPages(opened.numPages);
      })
      .catch((failure: unknown) => {
        setFailed(failure instanceof Error ? failure.message : String(failure));
      });
    return () => {
      void task.destroy();
    };
    // onPages comes from the parent's state setter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file.data]);

  useEffect(() => {
    const target = canvas.current;
    if (!pdf || !target) return;
    let cancel: (() => void) | undefined;
    let current = true;
    void pdf.getPage(page).then((loaded) => {
      if (!current) return;
      const viewport = loaded.getViewport({
        scale: (CSS_DPI_SCALE * zoom) / 100,
      });
      target.width = viewport.width;
      target.height = viewport.height;
      const task = loaded.render({ canvas: target, viewport });
      cancel = () => {
        task.cancel();
      };
      task.promise.catch(() => undefined);
    });
    return () => {
      current = false;
      cancel?.();
    };
  }, [pdf, page, zoom]);

  if (file.error) {
    return (
      <p role="alert" className="small">
        {file.error}
      </p>
    );
  }
  if (failed) {
    return (
      <p role="alert" className="small">
        {failed}
      </p>
    );
  }
  if (prompt) return <PasswordPrompt prompt={prompt} onDecline={onDeclined} />;
  const words = precision === "words";
  return (
    <div
      className="pdf-page"
      role="document"
      aria-label={`${label}, page ${String(page)}${rects.length ? ", cited passage marked" : ""}`}
      data-testid="pdf-page"
    >
      <canvas ref={canvas} data-testid="pdf-canvas" />
      {rects.map((rect, i) => (
        <span
          key={i}
          id={i === 0 ? "cited-passage" : undefined}
          data-testid="pdf-highlight"
          data-precision={precision ?? "page"}
          className={words ? "pdf-hl" : "pdf-area"}
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
