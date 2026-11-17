import { useState } from "react";
import type { ReactElement } from "react";
import { Flag, Lock } from "lucide-react";

import { search } from "../api";
import type { Answer, DocumentRow } from "../api";
import { beforeLine, EXAMPLE, LABEL, passRule } from "../reportPlace";
import type { PlaceKind, Where } from "../reportPlace";
import { PrivateBadge } from "../ui";
import { useLoad } from "../useLoad";

// The parts of "This was wrong" (WrongReport.tsx), each one field of its form.

interface Named {
  id: string;
  title: string;
}

function unique(rows: Named[]): Named[] {
  return rows.filter((row, i) => rows.findIndex((r) => r.id === row.id) === i);
}

function Choices({
  label,
  rows,
}: Readonly<{ label: string; rows: Named[] }>): ReactElement | null {
  if (rows.length === 0) return null;
  return (
    <optgroup label={label}>
      {rows.map((d) => (
        <option key={d.id} value={d.id}>
          {d.title}
        </option>
      ))}
    </optgroup>
  );
}

/** The document: the cited ones first, then those searched, then any found. */
export function DocumentPicker({
  id,
  answer,
  docId,
  onPick,
}: Readonly<{
  id: string;
  answer: Answer;
  docId: string;
  onPick: (docId: string) => void;
}>): ReactElement {
  const [finding, setFinding] = useState("");
  // Keyed by what's typed, as the palette's search is, so a late reply to earlier
  // typing never replaces the current one's.
  const results = useLoad(
    () =>
      finding.trim().length > 1
        ? search({ q: finding, include_private: true })
        : Promise.resolve(null),
    finding,
  );
  const found: DocumentRow[] = (results.data?.documents ?? []).slice(0, 20);
  // A document picked from a search stays listed when the next search leaves it
  // out, so the list shows what the form will save.
  const [pinned, setPinned] = useState<Named | null>(null);
  // A list cites its documents from its table's rows, not in its citations.
  const cited = unique(
    [...(answer.citations ?? []), ...(answer.table?.rows ?? [])].map((c) => ({
      id: c.document_id,
      title: c.title,
    })),
  );
  const searched = unique(
    [...(answer.also_searched ?? []), ...(answer.closest ?? [])].map((p) => ({
      id: p.document_id,
      title: p.title,
    })),
  ).filter((d) => !cited.some((c) => c.id === d.id));
  const kept = pinned?.id === docId ? [pinned] : [];
  const others = unique([...kept, ...found]).filter(
    (d) =>
      !cited.some((c) => c.id === d.id) && !searched.some((x) => x.id === d.id),
  );
  const refusal = answer.kind === "no_evidence";
  return (
    <div className="stack" style={{ gap: 4 }}>
      <label
        htmlFor={`${id}-doc`}
        className="small"
        style={{ fontWeight: 600 }}
      >
        Document
      </label>
      <select
        id={`${id}-doc`}
        className="select"
        value={docId}
        onChange={(e) => {
          const next = e.target.value;
          const row = others.find((d) => d.id === next);
          setPinned(row ? { id: row.id, title: row.title } : null);
          onPick(next);
        }}
      >
        <option value="" disabled>
          Choose a document
        </option>
        <Choices label="Cited in this answer" rows={cited} />
        <Choices
          label={refusal ? "Closest passages" : "Searched for this answer"}
          rows={searched}
        />
        <Choices label="Elsewhere in your library" rows={others} />
      </select>
      <label htmlFor={`${id}-find`} className="xs muted">
        Another document
      </label>
      <input
        id={`${id}-find`}
        className="input"
        value={finding}
        placeholder="Find it by its words"
        onChange={(e) => {
          setFinding(e.target.value);
        }}
      />
    </div>
  );
}

export function PlaceField({
  id,
  kind,
  pages,
  loc,
  onChange,
}: Readonly<{
  id: string;
  kind: PlaceKind;
  pages: number | null;
  loc: string;
  onChange: (loc: string) => void;
}>): ReactElement {
  const words = kind === "lines" || kind === "cells";
  return (
    <div className="stack" style={{ gap: 4 }}>
      <label
        htmlFor={`${id}-loc`}
        className="small"
        style={{ fontWeight: 600 }}
      >
        {LABEL[kind]}
      </label>
      <input
        id={`${id}-loc`}
        className={words ? "input" : "input mono"}
        style={{ width: kind === "cells" ? 180 : 88 }}
        value={loc}
        placeholder={EXAMPLE[kind]}
        onChange={(e) => {
          onChange(e.target.value);
        }}
      />
      <span className="xs muted">
        {kind === "page" && pages
          ? `of ${String(pages)}`
          : "as its citations name it"}
      </span>
    </div>
  );
}

export function Footer({
  why,
  before,
  busy,
  onCancel,
}: Readonly<{
  why: string | null | undefined;
  before: number;
  busy: boolean;
  onCancel: () => void;
}>): ReactElement {
  return (
    <>
      <p className="small" style={{ color: "var(--ink-2)" }}>
        {why ? (
          <span className="hstack wrap" style={{ gap: 6 }}>
            <PrivateBadge /> Private, because {why}. It's answered and scored
            only on this Mac, and never judged by a cloud model.
          </span>
        ) : (
          <span className="hstack" style={{ gap: 6 }}>
            <Lock size={12} aria-hidden="true" /> Saved on this Mac, in your
            library's evaluation folder, as a question in your own set.
          </span>
        )}{" "}
        It's asked again only when you run your own set.
        {beforeLine(before)}
      </p>
      <div className="hstack wrap">
        <button type="submit" className="btn primary" disabled={busy}>
          <Flag size={14} aria-hidden="true" /> Save report
        </button>
        <button type="button" className="btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </>
  );
}

/** Whether the right answer is in a document, or not in the library at all. */
export function WhereChoice({
  id,
  where,
  onChange,
}: Readonly<{
  id: string;
  where: Where;
  onChange: (where: Where) => void;
}>): ReactElement {
  return (
    <fieldset
      className="stack"
      style={{ gap: 6, border: 0, padding: 0, margin: 0 }}
    >
      <legend className="small" style={{ fontWeight: 600, marginBottom: 4 }}>
        Where is the right answer?
      </legend>
      <label className="hstack small" style={{ gap: 6 }}>
        <input
          type="radio"
          name={`${id}-where`}
          checked={where === "doc"}
          onChange={() => {
            onChange("doc");
          }}
        />{" "}
        In a document in my library
      </label>
      <label className="hstack small" style={{ gap: 6 }}>
        <input
          type="radio"
          name={`${id}-where`}
          checked={where === "none"}
          onChange={() => {
            onChange("none");
          }}
        />{" "}
        It isn't in my library
      </label>
    </fieldset>
  );
}

/** The fact the answer should have held, and what a later answer must do to pass. */
export function FactField({
  id,
  where,
  kind,
  fact,
  onChange,
}: Readonly<{
  id: string;
  where: Where;
  kind: PlaceKind | undefined;
  fact: string;
  onChange: (fact: string) => void;
}>): ReactElement {
  const optional = kind !== undefined || where === "none";
  return (
    <div className="stack" style={{ gap: 4 }}>
      <label
        htmlFor={`${id}-fact`}
        className="small"
        style={{ fontWeight: 600 }}
      >
        The fact it should have held{" "}
        {optional && (
          <span className="muted" style={{ fontWeight: 400 }}>
            (optional)
          </span>
        )}
      </label>
      <input
        id={`${id}-fact`}
        className="input"
        value={fact}
        placeholder="For example: April 22, 2026"
        aria-describedby={`${id}-fact-help`}
        onChange={(e) => {
          onChange(e.target.value);
        }}
      />
      <span id={`${id}-fact-help`} className="xs muted">
        {passRule(where, kind, fact)}
      </span>
    </div>
  );
}

export function NoteField({
  id,
  note,
  onChange,
}: Readonly<{
  id: string;
  note: string;
  onChange: (note: string) => void;
}>): ReactElement {
  return (
    <div className="stack" style={{ gap: 4 }}>
      <label
        htmlFor={`${id}-note`}
        className="small"
        style={{ fontWeight: 600 }}
      >
        Note{" "}
        <span className="muted" style={{ fontWeight: 400 }}>
          (optional)
        </span>
      </label>
      <textarea
        id={`${id}-note`}
        className="input"
        rows={2}
        value={note}
        placeholder="Anything to remember about it"
        onChange={(e) => {
          onChange(e.target.value);
        }}
      />
    </div>
  );
}
