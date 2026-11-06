import type { ReactElement } from "react";
import { FileText, TextQuote } from "lucide-react";

import type { SearchResults } from "../api";
import { docMeta, highlightParts } from "../format";
import { openHit } from "../searchHits";
import type { Hit } from "../searchHits";
import { KindIcon, PrivateBadge, StateBadge } from "../ui";
import type { ScopeOption } from "./ScopeChip";

function scopeNote(scope: ScopeOption): string {
  if (scope.collection) {
    return `Searching ${scope.label.replace("Collection: ", "")} only`;
  }
  return scope.private
    ? "Private included: answers on this Mac"
    : "Private excluded from this scope";
}

function HitRow({
  fresh,
  hit,
  query,
  active,
  moved,
  id,
}: Readonly<{
  fresh: boolean;
  hit: Hit;
  query: string;
  active: boolean; // the row Command-Return opens, or Return after the arrows
  moved: boolean; // the arrows moved to it: selected, and Return opens it
  id: string;
}>): ReactElement {
  const marked = (text: string) =>
    highlightParts(text, query).map((part, i) =>
      part.m ? <mark key={i}>{part.t}</mark> : <span key={i}>{part.t}</span>,
    );
  const isPrivate = hit.doc.sensitivity === "private";
  return (
    <div
      id={id}
      role="option"
      tabIndex={-1}
      aria-selected={active && moved}
      className={`result${active && !moved ? " first" : ""}`}
      onMouseDown={(event) => {
        event.preventDefault();
        if (fresh) openHit(hit);
      }}
    >
      <span className="r-icon">
        {hit.kind === "doc" ? (
          <KindIcon kind={hit.doc.file_kind} />
        ) : (
          <TextQuote size={16} aria-hidden="true" />
        )}
      </span>
      <span className="r-main">
        {hit.kind === "doc" ? (
          <>
            <span
              className="r-title"
              style={{ display: "flex", gap: 6, alignItems: "center" }}
            >
              <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
                {marked(hit.doc.title)}
              </span>
              {isPrivate && <PrivateBadge />}
              <StateBadge state={hit.doc.status} />
            </span>
            <span className="r-meta">{docMeta(hit.doc)}</span>
          </>
        ) : (
          <>
            <span className="r-snippet" style={{ display: "block" }}>
              {marked(hit.passage.snippet)}
            </span>
            <span
              className="r-meta"
              style={{ display: "flex", gap: 6, alignItems: "center" }}
            >
              <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
                {hit.doc.title} · {hit.passage.label}
              </span>
              {isPrivate && <PrivateBadge />}
            </span>
          </>
        )}
      </span>
      {active && (
        <span
          className="kbd r-open"
          aria-hidden="true"
          title={moved ? "Return opens this" : "Command-Return opens this"}
        >
          {moved ? "↵" : "⌘↵"}
        </span>
      )}
    </div>
  );
}

function HiddenNote({ hidden }: Readonly<{ hidden: number }>): ReactElement {
  return (
    <p className="small muted" style={{ padding: "4px 12px" }}>
      {hidden === 1
        ? "1 Private document matches."
        : `${String(hidden)} Private documents match.`}{" "}
      Include Private folders in the scope to see them.
    </p>
  );
}

function Waiting({
  results,
  empty,
  query,
  error,
}: Readonly<{
  results: SearchResults | undefined;
  empty: boolean;
  query: string;
  error: string | undefined;
}>): ReactElement | null {
  if (error) {
    return (
      <p className="empty" role="alert">
        {error}
      </p>
    );
  }
  if (!results) {
    return (
      <p className="small muted" style={{ padding: 12 }}>
        Searching…
      </p>
    );
  }
  if (!empty) return null;
  return (
    <div className="empty" style={{ padding: 20 }}>
      <p>No documents or passages match “{query}”.</p>
    </div>
  );
}

// Which key does what, at the foot of the list.
function ResultsFoot({
  hasHits,
  moved,
  asks,
  scope,
}: Readonly<{
  hasHits: boolean;
  moved: boolean;
  asks: string | null;
  scope: ScopeOption;
}>): ReactElement {
  return (
    <div className="results-foot" aria-hidden="true">
      {hasHits && (
        <span>
          <span className="kbd">↑</span> <span className="kbd">↓</span> move
        </span>
      )}
      {(moved || asks) && (
        <span>
          <span className="kbd">↵</span> {moved ? "open" : asks}
        </span>
      )}
      {hasHits && (
        <span>
          <span className="kbd">⌘↵</span> open {moved ? "selected" : "first"}
        </span>
      )}
      <span className="spacer" />
      <span>{scopeNote(scope)}</span>
    </div>
  );
}

export function Results({
  fresh,
  listId,
  results,
  hits,
  active,
  moved,
  asks,
  query,
  scope,
  error,
}: Readonly<{
  fresh: boolean;
  listId: string;
  results: SearchResults | undefined;
  hits: Hit[];
  active: number;
  moved: boolean;
  asks: string | null; // what Return does before the arrows: "ask Cloud AI", or none
  query: string;
  scope: ScopeOption;
  error: string | undefined;
}>): ReactElement {
  const documents = results?.documents.length ?? 0;
  const passages = results?.passages.length ?? 0;
  const hidden = results?.hidden_private ?? 0;
  const row = (hit: Hit, i: number): ReactElement => (
    <HitRow
      fresh={fresh}
      key={hit.id}
      hit={hit}
      query={query}
      active={i === active}
      moved={moved}
      id={`${listId}-${hit.id}`}
    />
  );
  return (
    <div
      className="results inline"
      id={listId}
      role={hits.length ? "listbox" : "status"}
      aria-label="Search results"
    >
      <Waiting
        results={results}
        empty={hits.length === 0}
        query={query}
        error={error}
      />
      {hits.length > 0 && (
        <>
          {documents > 0 && (
            <div role="group" aria-label={`Documents, ${String(documents)}`}>
              <div className="results-group" aria-hidden="true">
                <FileText size={12} /> Documents{" "}
                <span className="mono">{documents}</span>
              </div>
              {hits.slice(0, documents).map((hit, i) => row(hit, i))}
            </div>
          )}
          {passages > 0 && (
            <div role="group" aria-label={`Passages, ${String(passages)}`}>
              <div className="results-group" aria-hidden="true">
                <TextQuote size={12} /> Passages{" "}
                <span className="mono">{passages}</span>
              </div>
              {hits.slice(documents).map((hit, i) => row(hit, documents + i))}
            </div>
          )}
        </>
      )}
      {hidden > 0 && <HiddenNote hidden={hidden} />}
      <ResultsFoot
        hasHits={hits.length > 0}
        moved={moved}
        asks={asks}
        scope={scope}
      />
    </div>
  );
}
