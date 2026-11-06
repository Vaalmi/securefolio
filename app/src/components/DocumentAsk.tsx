import { useState } from "react";
import type { ReactElement } from "react";

import { getRelatedDocuments } from "../api";
import type { DocumentDetail } from "../api";
import { fmtDate } from "../format";
import { isSendKey } from "../keys";
import { go } from "../router";
import { useApp } from "../storeContext";
import { KindIcon, PrivateBadge } from "../ui";
import { useLoad } from "../useLoad";
import { canAsk, useAskRoute } from "../askRoute";
import { AskButton, RouteNote } from "./AskButton";
import { ModeSwitch } from "./ScopeChip";

const TRY = [
  "Summarize the key terms",
  "List every date and deadline",
  "What does it say about cancellation?",
];

export function AskPanel({
  detail,
}: Readonly<{ detail: DocumentDetail }>): ReactElement {
  const app = useApp();
  const [question, setQuestion] = useState("");
  const doc = detail.document;
  const isPrivate = doc.sensitivity === "private";
  const route = useAskRoute({ mode: app.mode, documents: [doc.id] });
  const send = (): void => {
    if (question.trim() && canAsk(route.data)) {
      go(
        `/ask/new?scope=sel:${doc.id}&q=${encodeURIComponent(question.trim())}`,
      );
    }
  };
  return (
    <div className="stack">
      <span
        className="chip"
        style={{ alignSelf: "flex-start", cursor: "default" }}
      >
        <KindIcon kind={doc.file_kind} size={14} /> Current document
      </span>
      <div className="composer">
        <label htmlFor="ask-doc" className="sr-only">
          Ask this document
        </label>
        <textarea
          id="ask-doc"
          rows={3}
          value={question}
          placeholder="Ask this document"
          onChange={(event) => {
            setQuestion(event.target.value);
          }}
          onKeyDown={(event) => {
            if (isSendKey(event)) {
              event.preventDefault();
              send();
            }
          }}
        />
        <div className="composer-bar">
          <ModeSwitch
            value={isPrivate ? "private" : app.mode}
            onChange={app.setMode}
            lockedPrivate={isPrivate}
          />
          {isPrivate && <PrivateBadge />}
          <RouteNote route={route.data} error={route.error} />
          <span className="spacer" />
          <AskButton
            route={route.data}
            mode={app.mode}
            disabled={!question.trim()}
            onClick={send}
          />
        </div>
      </div>
      <div className="stack" style={{ gap: 6 }}>
        <span className="small muted">Try</span>
        {TRY.map((text) => (
          <button
            key={text}
            type="button"
            className="chip"
            style={{ alignSelf: "flex-start" }}
            onClick={() => {
              setQuestion(text);
            }}
          >
            {text}
          </button>
        ))}
      </div>
    </div>
  );
}

export function RelatedPanel({ id }: Readonly<{ id: string }>): ReactElement {
  const related = useLoad(() => getRelatedDocuments(id), id);
  return (
    <div className="stack" style={{ gap: 4 }}>
      <p className="small muted">Found from their cards.</p>
      {related.data?.map((row) => (
        <a
          key={row.document_id}
          href={`#/doc/${row.document_id}`}
          className="list-row"
          style={{ textDecoration: "none", color: "inherit" }}
        >
          <KindIcon kind={row.file_kind} />
          <span style={{ flex: 1, minWidth: 0 }}>
            <span className="title" style={{ display: "block" }}>
              {row.title}
            </span>
            <span className="small muted">
              {row.doc_type ?? row.file_kind} · {fmtDate(row.doc_date)}
            </span>
          </span>
        </a>
      ))}
      {related.data?.length === 0 && (
        <p className="muted small">No related documents yet.</p>
      )}
    </div>
  );
}
