import { useState } from "react";
import type { ReactElement } from "react";
import {
  ChevronLeft,
  ChevronRight,
  CloudDownload,
  FileSearch,
  RefreshCw,
  Unlink,
  ZoomIn,
  ZoomOut,
} from "lucide-react";

import { downloadDocument, reindexDocument, relocateDocument } from "../api";
import type { DocumentDetail, Locator, PageCost, Rect } from "../api";
import { chooseFile } from "../desktop";
import { fmtMoment, pageOf } from "../format";
import { Modal } from "../ui";
import { useAction } from "../useAction";
import { PdfView } from "../viewers/PdfView";
import { isPicture } from "../viewers/pictures";
import { StoredPage } from "../viewers/StoredText";
import {
  EmailView,
  ImageView,
  LinesView,
  SheetView,
  SlideView,
  StructureView,
} from "../viewers/TextViews";

const MIN_ZOOM = 50;
const MAX_ZOOM = 200;
const ZOOM_STEP = 10;

function rectsOf(locator: Locator | undefined): {
  rects: Rect[];
  precision: string | null;
} {
  if (locator?.kind === "pdf")
    return { rects: locator.rects, precision: locator.precision };
  if (locator?.kind === "slide")
    return { rects: locator.rects, precision: "element" };
  return { rects: [], precision: null };
}

export function Pager({
  page,
  pages,
  unit,
  setPage,
  zoom,
  setZoom,
}: Readonly<{
  page: number;
  pages: number;
  unit: string;
  setPage: (n: number) => void;
  zoom: number | null;
  setZoom: (z: number) => void;
}>): ReactElement {
  const [typed, setTyped] = useState(String(page));
  const commit = (): void => {
    setPage(Number(typed) || page);
  };
  return (
    <div className="viewer-bar">
      <button
        type="button"
        className="icon-btn"
        aria-label={`Previous ${unit.toLowerCase()}`}
        disabled={page <= 1}
        onClick={() => {
          setPage(page - 1);
        }}
      >
        <ChevronLeft size={16} aria-hidden="true" />
      </button>
      <form
        className="hstack small"
        style={{ gap: 6 }}
        onSubmit={(event) => {
          event.preventDefault();
          commit();
        }}
      >
        <label htmlFor="page-jump">{unit}</label>
        <input
          id="page-jump"
          className="input mono page-jump"
          inputMode="numeric"
          value={typed}
          onChange={(event) => {
            setTyped(event.target.value);
          }}
          onBlur={commit}
        />
        <span>
          of <span className="mono">{pages}</span>
        </span>
      </form>
      <button
        type="button"
        className="icon-btn"
        aria-label={`Next ${unit.toLowerCase()}`}
        disabled={page >= pages}
        onClick={() => {
          setPage(page + 1);
        }}
      >
        <ChevronRight size={16} aria-hidden="true" />
      </button>
      <span className="spacer" />
      {zoom !== null && (
        <>
          <button
            type="button"
            className="icon-btn"
            aria-label="Zoom out"
            onClick={() => {
              setZoom(Math.max(MIN_ZOOM, zoom - ZOOM_STEP));
            }}
          >
            <ZoomOut size={16} aria-hidden="true" />
          </button>
          <span className="small mono" aria-live="polite">
            {zoom}%
          </span>
          <button
            type="button"
            className="icon-btn"
            aria-label="Zoom in"
            onClick={() => {
              setZoom(Math.min(MAX_ZOOM, zoom + ZOOM_STEP));
            }}
          >
            <ZoomIn size={16} aria-hidden="true" />
          </button>
        </>
      )}
    </div>
  );
}

export function OriginalGone({
  detail,
  changed,
  onChanged,
  onRemove,
}: Readonly<{
  detail: DocumentDetail;
  changed: boolean;
  onChanged: () => void;
  onRemove: () => void;
}>): ReactElement {
  const doc = detail.document;
  const place = detail.locations[0]?.path ?? doc.file_name;
  const stored = fmtMoment(doc.added_at);
  const reindex = useAction(() => reindexDocument(doc.id), onChanged);
  const locate = useAction(async () => {
    const path = await chooseFile();
    if (path) await relocateDocument(doc.id, path);
  }, onChanged);
  return (
    <div
      className="banner neutral"
      role="status"
      style={{ width: "min(640px, 100%)" }}
    >
      <span className="banner-icon">
        <Unlink size={16} aria-hidden="true" />
      </span>
      <div className="stack" style={{ gap: 6 }}>
        <strong>
          {changed
            ? "The original file changed after it was indexed"
            : "The original file is gone"}
        </strong>
        <p className="small">
          {changed
            ? `You're seeing the text SecureFolio stored on ${stored}. Highlights are off until the new version is indexed.`
            : `It's no longer at ${place}. You're seeing the text SecureFolio stored on ${stored}. Highlights are off so nothing points at the wrong place.`}
        </p>
        {(locate.error ?? reindex.error) && (
          <p role="alert" className="small">
            {locate.error ?? reindex.error}
          </p>
        )}
        <div className="hstack wrap">
          {changed ? (
            <button
              type="button"
              className="btn primary sm"
              onClick={() => {
                void reindex.run();
              }}
            >
              <RefreshCw size={12} aria-hidden="true" /> Index the new version
            </button>
          ) : (
            <button
              type="button"
              className="btn primary sm"
              onClick={() => {
                void locate.run();
              }}
            >
              <FileSearch size={12} aria-hidden="true" /> Locate file…
            </button>
          )}
          <button type="button" className="btn sm" onClick={onRemove}>
            Remove from library
          </button>
        </div>
      </div>
    </div>
  );
}

export function NotDownloaded({
  id,
  onChanged,
}: Readonly<{
  id: string;
  onChanged: () => void;
}>): ReactElement {
  const download = useAction(() => downloadDocument(id), onChanged);
  return (
    <div
      className="banner warn"
      role="status"
      style={{ width: "min(640px, 100%)" }}
    >
      <span className="banner-icon">
        <CloudDownload size={16} aria-hidden="true" />
      </span>
      <div className="stack" style={{ gap: 6 }}>
        <strong>This file is in the cloud and isn't on this Mac yet</strong>
        <p className="small">
          SecureFolio knows its name and folder only. Download it to read and
          index it; the file itself isn't changed.
        </p>
        {download.error && (
          <p role="alert" className="small">
            {download.error}
          </p>
        )}
        <span className="hstack">
          <button
            type="button"
            className="btn primary sm"
            onClick={() => {
              void download.run();
            }}
          >
            <CloudDownload size={12} aria-hidden="true" /> Download & Index
          </button>
        </span>
      </div>
    </div>
  );
}

export function Viewer({
  detail,
  page,
  setPage,
  pages,
  setPages,
  zoom,
  setZoom,
  citedId,
  citedOrdinal,
  locator,
}: Readonly<{
  detail: DocumentDetail;
  page: number;
  setPage: (n: number) => void;
  pages: number;
  setPages: (n: number) => void;
  zoom: number;
  setZoom: (z: number) => void;
  citedId: string | null;
  citedOrdinal: number | null;
  locator: Locator | undefined;
}>): ReactElement {
  const doc = detail.document;
  const [declined, setDeclined] = useState(false);
  const kind = doc.file_kind;
  const onThisPage = pageOf(locator) === page;
  const { rects, precision } = onThisPage
    ? rectsOf(locator)
    : { rects: [], precision: null };
  if (declined) {
    return (
      <>
        <Pager
          key={page}
          page={page}
          pages={pages}
          unit="Page"
          setPage={setPage}
          zoom={null}
          setZoom={setZoom}
        />
        <StoredPage id={doc.id} page={page} />
      </>
    );
  }
  if (kind === "PDF") {
    return (
      <>
        <Pager
          key={page}
          page={page}
          pages={pages}
          unit="Page"
          setPage={setPage}
          zoom={zoom}
          setZoom={setZoom}
        />
        <div className="zoom-wrap">
          <PdfView
            id={doc.id}
            page={page}
            zoom={zoom}
            rects={rects}
            precision={precision}
            label={doc.title}
            onPages={setPages}
            onDeclined={() => {
              setDeclined(true);
            }}
          />
        </div>
      </>
    );
  }
  if (isPicture(kind)) {
    const pictureRects = locator?.kind === "pdf" ? locator.rects : [];
    return (
      <ImageView
        id={doc.id}
        kind={kind}
        rects={pictureRects}
        label={doc.title}
      />
    );
  }
  if (kind === "XLSX" || kind === "CSV")
    return (
      <SheetView
        key={citedId}
        id={doc.id}
        citedId={citedId}
        citedOrdinal={citedOrdinal}
        label={doc.title}
      />
    );
  if (kind === "PPTX") {
    return (
      <>
        <Pager
          key={page}
          page={page}
          pages={doc.pages ?? 1}
          unit="Slide"
          setPage={setPage}
          zoom={null}
          setZoom={setZoom}
        />
        <SlideView
          id={doc.id}
          page={page}
          citedId={citedId}
          label={doc.title}
        />
      </>
    );
  }
  if (kind === "MD" || kind === "TXT")
    return (
      <LinesView
        key={citedId}
        id={doc.id}
        citedId={citedId}
        citedOrdinal={citedOrdinal}
        label={doc.title}
      />
    );
  if (kind === "EML")
    return (
      <EmailView
        key={citedId}
        id={doc.id}
        citedId={citedId}
        citedOrdinal={citedOrdinal}
      />
    );
  return (
    <StructureView
      key={citedId}
      id={doc.id}
      citedId={citedId}
      citedOrdinal={citedOrdinal}
      label={doc.title}
    />
  );
}

function perMillion(cost: PageCost): string {
  if (cost.input_usd === null || cost.output_usd === null) return "";
  return ` ($${cost.input_usd.toFixed(2)} in · $${cost.output_usd.toFixed(2)} out per million tokens)`;
}

/** Reading one page in the cloud: what it sends, to which model, and the most it
 * costs; or, when it's unavailable now, why, and that Re-read this page still works. */
export function CloudPageModal({
  cost,
  page,
  error,
  onClose,
  onSend,
}: Readonly<{
  cost: PageCost;
  page: number;
  error?: string;
  onClose: () => void;
  onSend: () => void;
}>): ReactElement {
  if (cost.unavailable !== null || cost.cost_usd === null) {
    return (
      <Modal
        title="Reading a page in the cloud is unavailable"
        onClose={onClose}
        footer={
          <button type="button" className="btn" onClick={onClose}>
            Close
          </button>
        }
      >
        <div className="modal-body">
          <p>{cost.unavailable}</p>
          <p className="small muted">
            Re-read this page, on this Mac, still works.
          </p>
        </div>
      </Modal>
    );
  }
  return (
    <Modal
      title={`Read page ${String(page)} with Cloud AI?`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn primary" onClick={onSend}>
            Send this page · at most ${cost.cost_usd.toFixed(3)}
          </button>
        </>
      }
    >
      <div className="modal-body">
        <p>
          This sends an image of page {page}, and only that page, to Cloud AI:{" "}
          {cost.model_name}, from {cost.provider}
          {perMillion(cost)}. Its text replaces what SecureFolio read from this
          page. It counts toward your monthly cap.
        </p>
        {error && (
          <p className="small" role="alert">
            {error}
          </p>
        )}
      </div>
    </Modal>
  );
}
