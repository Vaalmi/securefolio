import type { ReactElement } from "react";
import { AlertTriangle, Info, Lock } from "lucide-react";

import type { Answer } from "../api";
import { fmtDate } from "../format";
import { AnswerBody } from "./AskParts";
import { money } from "../money";

export function NoEvidence({
  answer,
  scopeLabel,
  onPreview,
  onAskLater,
  onIncludePrivate,
}: Readonly<{
  answer: Answer;
  scopeLabel: string;
  onPreview: (key: string) => void;
  onAskLater: () => void;
  onIncludePrivate: (() => void) | null;
}>): ReactElement {
  const coverage = answer.coverage ?? {
    searched: 0,
    indexing: 0,
    private_folders: 0,
  };
  return (
    <div className="stack" style={{ gap: 12 }}>
      <div className="banner info" role="status">
        <span className="banner-icon">
          <Info size={16} aria-hidden="true" />
        </span>
        <div className="stack" style={{ gap: 4 }}>
          <strong>I couldn't find enough evidence in this scope.</strong>
          <p className="small">
            Searched{" "}
            <span className="mono">
              {coverage.searched.toLocaleString("en-US")}
            </span>{" "}
            documents in {scopeLabel}.
            {coverage.indexing > 0 &&
              ` ${coverage.indexing.toLocaleString("en-US")} more are still indexing.`}
            {coverage.private_folders > 0 &&
              ` ${String(coverage.private_folders)} Private folders weren't searched.`}
          </p>
        </div>
      </div>
      {(answer.closest?.length ?? 0) > 0 && (
        <div>
          <h2 className="h-sm" style={{ marginBottom: 8 }}>
            Closest passages
          </h2>
          <div className="stack" style={{ gap: 8 }}>
            {answer.closest?.map((passage) => (
              <div
                key={`${passage.document_id}${passage.element_id ?? ""}`}
                className="passage"
              >
                <div className="p-head">
                  <strong>{passage.title}</strong>
                  <span className="muted">· {passage.label}</span>
                  <span className="spacer" />
                  <button
                    type="button"
                    className="btn sm"
                    aria-label={`Preview ${passage.title}, ${passage.label}`}
                    onClick={() => {
                      onPreview(
                        `x-${passage.document_id}-${passage.element_id ?? ""}`,
                      );
                    }}
                  >
                    Preview
                  </button>
                </div>
                <blockquote>{passage.text}</blockquote>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="hstack wrap">
        {coverage.indexing > 0 && (
          <button type="button" className="btn" onClick={onAskLater}>
            Ask again when indexing finishes
          </button>
        )}
        {onIncludePrivate && coverage.private_folders > 0 && (
          <button type="button" className="btn" onClick={onIncludePrivate}>
            <Lock size={12} aria-hidden="true" /> Include Private folders
          </button>
        )}
      </div>
    </div>
  );
}

export function ListAnswer({
  answer,
  messageId,
  activeCite,
  onCite,
}: Readonly<{
  answer: Answer;
  messageId: string;
  activeCite: string | null;
  onCite: (key: string) => void;
}>): ReactElement {
  const table = answer.table;
  if (!table) return <p>{answer.parts?.map((p) => p.text).join("")}</p>;
  return (
    <div className="stack" style={{ gap: 12 }}>
      {table.wants_total && (
        <p>
          The total is <strong className="mono">{money(table.total)}</strong>,
          across {table.rows.length} documents, worked out from their cards.
        </p>
      )}
      {(answer.parts?.length ?? 0) > 0 && (
        <AnswerBody
          answer={answer}
          messageId={messageId}
          activeCite={activeCite}
          onCite={onCite}
        />
      )}
      <div className="card" style={{ padding: 0 }}>
        <table className="table">
          <caption className="sr-only">
            The documents behind this answer
          </caption>
          <thead>
            <tr>
              <th scope="col">Document</th>
              <th scope="col">Date</th>
              <th scope="col" className="num">
                Amount
              </th>
              <th scope="col">Source</th>
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row) => {
              const key = `${messageId}-t${String(row.n)}`;
              return (
                <tr key={row.document_id}>
                  <td>{row.title}</td>
                  <td>{fmtDate(row.doc_date)}</td>
                  <td className="num">{row.amount_text}</td>
                  <td>
                    <button
                      type="button"
                      className="cite"
                      aria-pressed={activeCite === key}
                      aria-label={`Citation ${String(row.n)}: ${row.title}, card. Open preview`}
                      onClick={() => {
                        onCite(key);
                      }}
                    >
                      {row.n}
                    </button>
                  </td>
                </tr>
              );
            })}
            {table.wants_total && (
              <tr>
                <td>
                  <strong>Total</strong>
                </td>
                <td />
                <td className="num">
                  <strong>{money(table.total)}</strong>
                </td>
                <td />
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {table.unchecked_pending > 0 && (
        <p className="small muted" role="note">
          {table.unchecked_pending} more cards in this scope are still pending,
          so they weren't checked.
        </p>
      )}
      {table.pending.length > 0 && (
        <div className="banner warn">
          <span className="banner-icon">
            <AlertTriangle size={16} aria-hidden="true" />
          </span>
          <div className="stack" style={{ gap: 4 }}>
            <strong>{table.pending.length} documents aren't counted yet</strong>
            <ul style={{ margin: 0, paddingLeft: 18 }} className="small">
              {table.pending.map((row) => (
                <li key={row.document_id}>
                  <a href={`#/doc/${row.document_id}`}>{row.title}</a> (
                  {row.reason === "pending"
                    ? "its card is pending"
                    : "its card has no amount"}
                  )
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}
