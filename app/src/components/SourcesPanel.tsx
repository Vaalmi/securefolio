import { useEffect, useRef } from "react";
import type { ReactElement } from "react";
import { ArrowLeft, ExternalLink } from "lucide-react";

import { getDocument, resolveCitation } from "../api";
import type { Answer, Locator, StoredElement, ThreadMessage } from "../api";
import { go } from "../router";
import { Tabs } from "../ui";
import { useLoad } from "../useLoad";
import { PdfView } from "../viewers/PdfView";
import { isPicture } from "../viewers/pictures";
import { ImageView } from "../viewers/TextViews";

const CARD_LOCATOR: Locator = { kind: "card", field: "amounts" };
const CARD_ELEMENT = "card:";

type PdfPlace = Extract<Locator, { kind: "pdf" }>;

interface Source {
  key: string;
  n: number;
  documentId: string;
  elementId: string | null;
  revisionId?: string;
  title: string;
  label: string;
  quote: string;
  locator: Locator | null;
  turn: number;
}

function citedSources(turns: ThreadMessage[]): Source[] {
  return turns.flatMap((message, turn) => {
    const answer = message.answer;
    if (!answer) return [];
    const cited = (answer.citations ?? []).map((c) => ({
      key: `${message.id}-${String(c.n)}`,
      n: c.n,
      documentId: c.document_id,
      elementId: c.element_id,
      revisionId: c.revision_id,
      title: c.title,
      label: c.label,
      quote: c.quote,
      locator: c.locator,
      turn,
    }));
    const rows = (answer.table?.rows ?? []).map((row) => ({
      key: `${message.id}-t${String(row.n)}`,
      n: row.n,
      documentId: row.document_id,
      elementId: null,
      title: row.title,
      label: "Card · Amounts",
      quote: `${row.amount_text}. ${row.summary}`,
      locator: CARD_LOCATOR,
      turn,
    }));
    return [...cited, ...rows];
  });
}

function searched(answers: Answer[]): Source[] {
  return answers.flatMap((answer) =>
    [...(answer.also_searched ?? []), ...(answer.closest ?? [])].map(
      (passage) => ({
        key: `x-${passage.document_id}-${passage.element_id ?? ""}`,
        n: 0,
        documentId: passage.document_id,
        elementId: passage.element_id,
        title: passage.title,
        label: passage.label,
        quote: passage.snippet ?? passage.text ?? "",
        locator: null,
        turn: 0,
      }),
    ),
  );
}

function isOnACard(elementId: string | null): boolean {
  return elementId?.startsWith(CARD_ELEMENT) ?? false;
}

function openingAt(source: Source, moved: StoredElement | null): string {
  const place = isOnACard(source.elementId)
    ? null
    : (moved?.id ?? source.elementId);
  return place
    ? `/doc/${source.documentId}?el=${place}`
    : `/doc/${source.documentId}`;
}

// A saved rectangle only fits the revision it came from, so ask where it is now.
function useCitationPlace(source: Source) {
  const { elementId, revisionId, documentId } = source;
  const place = useLoad(
    () =>
      elementId && revisionId && !isOnACard(elementId)
        ? resolveCitation(documentId, revisionId, elementId)
        : Promise.resolve(null),
    source.key,
  );
  const state = place.data?.state;
  const element = place.data?.element ?? null;
  const placed = state === "current" || state === "moved";
  const locator = placed ? element?.locator : null;
  return {
    element,
    pdf: locator?.kind === "pdf" ? locator : null,
    changed: state === "stored" || state === "gone",
    opens: openingAt(source, placed ? element : null),
  };
}

// A placed citation, drawn on what it was read from: a PDF's page, or the picture
// itself. Until the document says which, nothing is drawn; if it can't, the quote is.
function CitedPage({
  source,
  pdf,
  quote,
}: Readonly<{
  source: Source;
  pdf: PdfPlace;
  quote: string;
}>): ReactElement | null {
  const kind = useLoad(
    () => getDocument(source.documentId).then((d) => d.document.file_kind),
    source.documentId,
  );
  if (kind.error) return <blockquote>{quote}</blockquote>;
  if (kind.data === undefined) return null;
  if (isPicture(kind.data)) {
    return (
      <ImageView
        id={source.documentId}
        kind={kind.data}
        rects={pdf.rects}
        label={source.title}
      />
    );
  }
  return (
    <PdfView
      id={source.documentId}
      page={pdf.page}
      zoom={50}
      rects={pdf.rects}
      precision={pdf.precision}
      label={source.title}
      onPages={() => undefined}
      onDeclined={() => undefined}
    />
  );
}

function Preview({
  source,
  onBack,
}: Readonly<{ source: Source; onBack: () => void }>): ReactElement {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus();
  }, [source.key]);
  const { element, pdf, changed, opens } = useCitationPlace(source);
  const quote = element?.text ?? source.quote;
  return (
    <>
      <div className="panel-head" style={{ paddingRight: 48 }}>
        <button
          type="button"
          className="icon-btn"
          onClick={onBack}
          aria-label="Back to sources"
        >
          <ArrowLeft size={16} aria-hidden="true" />
        </button>
        <h2 ref={heading} tabIndex={-1}>
          Preview
        </h2>
      </div>
      <div className="panel-body">
        <div className="stack" style={{ gap: 4 }}>
          <strong>{source.title}</strong>
          <span className="small muted">
            {source.n > 0 ? `Citation ${String(source.n)}` : "Searched passage"}{" "}
            · {source.label}
          </span>
        </div>
        <div className="zoom-wrap">
          {pdf ? (
            // Drawn afresh for each document, so nothing of the last one shows.
            <CitedPage
              key={source.documentId}
              source={source}
              pdf={pdf}
              quote={quote}
            />
          ) : (
            <blockquote>{quote}</blockquote>
          )}
        </div>
        {changed && (
          <p className="small muted" role="note">
            This file has changed since the answer, so this is the text it read
            then.
          </p>
        )}
        <div className="hstack wrap">
          <button
            type="button"
            className="btn primary"
            onClick={() => {
              go(opens);
            }}
          >
            <ExternalLink size={14} aria-hidden="true" /> Open in viewer
          </button>
        </div>
      </div>
    </>
  );
}

function CitedList({
  sources,
  turns,
  activeKey,
  onCite,
}: Readonly<{
  sources: Source[];
  turns: number;
  activeKey: string | null;
  onCite: (key: string) => void;
}>): ReactElement {
  if (sources.length === 0) {
    return (
      <p className="muted small">
        Citations appear here as the answer is written.
      </p>
    );
  }
  return (
    <>
      {sources.map((source, i) => (
        <div key={source.key} style={{ display: "contents" }}>
          {turns > 1 && (i === 0 || sources[i - 1]?.turn !== source.turn) && (
            <h3
              className="xs muted"
              style={{
                textTransform: "uppercase",
                letterSpacing: "0.04em",
                fontWeight: 600,
              }}
            >
              Answer {source.turn + 1}
            </h3>
          )}
          <div
            className={`passage ${activeKey === source.key ? "active" : ""}`}
          >
            <div className="p-head">
              <span
                className="cite"
                aria-hidden="true"
                style={{ cursor: "default" }}
              >
                {source.n}
              </span>
              <strong
                style={{
                  minWidth: 0,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {source.title}
              </strong>
            </div>
            <blockquote>“{source.quote}”</blockquote>
            <div className="hstack">
              <span className="small muted">{source.label}</span>
              <span className="spacer" />
              <button
                type="button"
                className="btn sm"
                aria-label={`Preview citation ${String(source.n)}, ${source.title}, ${source.label}`}
                onClick={() => {
                  onCite(source.key);
                }}
              >
                Preview
              </button>
            </div>
          </div>
        </div>
      ))}
    </>
  );
}

function AlsoList({
  sources,
  onCite,
}: Readonly<{
  sources: Source[];
  onCite: (key: string) => void;
}>): ReactElement {
  if (sources.length === 0) {
    return <p className="muted small">Nothing else was searched.</p>;
  }
  return (
    <>
      {sources.map((source) => (
        <div key={source.key} className="passage">
          <div className="p-head">
            <strong>{source.title}</strong>
            <span className="muted">· {source.label}</span>
          </div>
          <blockquote>{source.quote}</blockquote>
          <div className="hstack">
            <span className="small muted">
              Searched, not used in the answer
            </span>
            <span className="spacer" />
            <button
              type="button"
              className="btn sm"
              aria-label={`Preview ${source.title}, ${source.label}`}
              onClick={() => {
                onCite(source.key);
              }}
            >
              Preview
            </button>
          </div>
        </div>
      ))}
    </>
  );
}

export function SourcesPanel({
  messages,
  activeKey,
  onCite,
  tab,
  setTab,
}: Readonly<{
  messages: ThreadMessage[];
  activeKey: string | null;
  onCite: (key: string) => void;
  tab: string;
  setTab: (tab: string) => void;
}>): ReactElement {
  const turns = messages.filter((m) => m.answer);
  const answers = turns.flatMap((m) => (m.answer ? [m.answer] : []));
  const cited = citedSources(turns);
  const also = searched(answers);
  const active = [...cited, ...also].find((source) => source.key === activeKey);
  if (active) {
    return (
      <Preview
        source={active}
        onBack={() => {
          onCite("");
          window.setTimeout(() => {
            document
              .querySelector<HTMLElement>(
                "#right-panel [role=tab][aria-selected=true]",
              )
              ?.focus();
          }, 0);
        }}
      />
    );
  }
  return (
    <>
      <div className="panel-head" style={{ paddingRight: 48 }}>
        <h2>Sources</h2>
      </div>
      <Tabs
        idBase="src"
        label="Sources"
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "cited", label: "Cited", count: cited.length },
          { id: "also", label: "Also searched", count: also.length },
          { id: "activity", label: "Activity" },
        ]}
      />
      <div
        className="panel-body"
        role="tabpanel"
        id="src-panel"
        aria-labelledby={`src-tab-${tab}`}
      >
        {tab === "cited" && (
          <CitedList
            sources={cited}
            turns={turns.length}
            activeKey={activeKey}
            onCite={onCite}
          />
        )}
        {tab === "also" && <AlsoList sources={also} onCite={onCite} />}
        {tab === "activity" && (
          <ol className="activity small" style={{ paddingLeft: 18, margin: 0 }}>
            {answers
              .flatMap((a) => a.activity)
              .map((line, i) => (
                <li key={i}>{line}</li>
              ))}
          </ol>
        )}
      </div>
    </>
  );
}
