import { toast } from "../toast";
import { useState } from "react";
import type { ReactElement } from "react";
import { FolderPlus, Pencil, X } from "lucide-react";

import {
  addToCollection,
  editDocumentCard,
  listCollections,
  removeFromCollection,
} from "../api";
import type { Card, DocumentDetail } from "../api";
import { MenuButton, PendingBadge } from "../ui";
import { useAction } from "../useAction";
import { useLoad } from "../useLoad";

const TYPES = [
  "Contract",
  "Warranty",
  "Receipt",
  "Statement",
  "Report",
  "Letter",
  "Email",
  "Policy",
  "Manual",
  "Tax return",
  "Note",
  "Image",
  "Other",
];
const SUMMARY_WORDS = 80;
const LABELS: Record<string, string> = {
  title: "Title",
  doc_type: "Type",
  summary: "Summary",
  dates: "Dates",
  people: "People",
  organizations: "Organizations",
  amounts: "Amounts",
  tags: "Tags",
};

interface Draft {
  title: string;
  doc_type: string;
  summary: string;
  dates: string;
  people: string;
  organizations: string;
  amounts: string;
  tags: string;
}

function draftOf(card: Card): Draft {
  return {
    title: card.title,
    doc_type: card.doc_type,
    summary: card.summary,
    dates: card.dates.map((d) => `${d.label}: ${d.value}`).join("\n"),
    people: card.people.join("\n"),
    organizations: card.organizations.join("\n"),
    amounts: card.amounts.join("\n"),
    tags: card.tags.join(", "),
  };
}

function lines(text: string): string[] {
  return text
    .split("\n")
    .map((x) => x.trim())
    .filter(Boolean);
}

function changesOf(draft: Draft, card: Card): Partial<Card> {
  const was = draftOf(card);
  const changed: Partial<Card> = {};
  if (draft.title !== was.title) changed.title = draft.title;
  if (draft.doc_type !== was.doc_type) changed.doc_type = draft.doc_type;
  if (draft.summary !== was.summary) changed.summary = draft.summary;
  if (draft.people !== was.people) changed.people = lines(draft.people);
  if (draft.organizations !== was.organizations)
    changed.organizations = lines(draft.organizations);
  if (draft.amounts !== was.amounts) changed.amounts = lines(draft.amounts);
  if (draft.tags !== was.tags)
    changed.tags = draft.tags
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);
  if (draft.dates !== was.dates) {
    changed.dates = lines(draft.dates).map((line) => {
      const [label, ...value] = line.split(":");
      return { label: label.trim(), value: value.join(":").trim() };
    });
  }
  return changed;
}

function CardEditor({
  card,
  onSaved,
  onCancel,
  documentId,
}: Readonly<{
  card: Card;
  onSaved: () => void;
  onCancel: () => void;
  documentId: string;
}>): ReactElement {
  const [draft, setDraft] = useState<Draft>(() => draftOf(card));
  const save = useAction(async () => {
    const changes = changesOf(draft, card);
    if (Object.keys(changes).length === 0) {
      toast("No changes.");
      return;
    }
    await editDocumentCard(documentId, changes);
    const names = Object.keys(changes).map((k) => LABELS[k] ?? k);
    toast(
      `Card saved: ${names.join(", ")}. Your corrections survive re-indexing.`,
    );
  }, onSaved);
  const area = (
    key: "summary" | "dates" | "people" | "organizations" | "amounts",
    help: string,
    rows = 3,
  ): ReactElement => (
    <div className="field">
      <label htmlFor={`c-${key}`}>{LABELS[key]}</label>
      <textarea
        id={`c-${key}`}
        className="textarea"
        rows={rows}
        value={draft[key]}
        onChange={(event) => {
          setDraft({ ...draft, [key]: event.target.value });
        }}
        aria-describedby={`c-${key}-help`}
      />
      <span id={`c-${key}-help`} className="help">
        {help}
      </span>
    </div>
  );
  return (
    <form
      className="stack"
      aria-label="Edit card"
      onSubmit={(event) => {
        event.preventDefault();
        void save.run();
      }}
    >
      <div className="field">
        <label htmlFor="c-title">Title</label>
        <input
          id="c-title"
          className="input"
          value={draft.title}
          onChange={(event) => {
            setDraft({ ...draft, title: event.target.value });
          }}
        />
      </div>
      <div className="field">
        <label htmlFor="c-type">Type</label>
        <select
          id="c-type"
          className="select"
          value={draft.doc_type}
          onChange={(event) => {
            setDraft({ ...draft, doc_type: event.target.value });
          }}
        >
          {TYPES.map((type) => (
            <option key={type}>{type}</option>
          ))}
        </select>
      </div>
      {area(
        "summary",
        `${String(draft.summary.split(/\s+/).filter(Boolean).length)} of ${String(SUMMARY_WORDS)} words`,
        5,
      )}
      {area("dates", "One per line, as Label: date. Add a line to add a date.")}
      {area("people", "One person per line.")}
      {area("organizations", "One organization per line.")}
      {area("amounts", "One amount per line, in US dollars.")}
      <div className="field">
        <label htmlFor="c-tags">Tags</label>
        <input
          id="c-tags"
          className="input"
          value={draft.tags}
          onChange={(event) => {
            setDraft({ ...draft, tags: event.target.value });
          }}
          aria-describedby="c-tags-help"
        />
        <span id="c-tags-help" className="help">
          Separate tags with commas.
        </span>
      </div>
      {save.error && (
        <p role="alert" className="small">
          {save.error}
        </p>
      )}
      <div className="hstack">
        <button type="submit" className="btn primary">
          Save card
        </button>
        <button type="button" className="btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function CollectionsRow({
  detail,
  onChanged,
}: Readonly<{
  detail: DocumentDetail;
  onChanged: () => void;
}>): ReactElement {
  const documentId = detail.document.id;
  const all = useLoad(listCollections, "collections");
  const add = useAction(
    (id: string) => addToCollection(id, [documentId]),
    onChanged,
  );
  const remove = useAction(
    (id: string) => removeFromCollection(id, [documentId]),
    onChanged,
  );
  const member = new Set(detail.collections.map((c) => c.id));
  return (
    <div>
      <h3 style={{ marginBottom: 6 }}>Collections</h3>
      <div className="hstack wrap">
        {detail.collections.map((collection) => (
          <span
            key={collection.id}
            className="tag hstack"
            style={{ gap: 2, paddingRight: 2 }}
          >
            {collection.name}
            <button
              type="button"
              className="icon-btn"
              style={{ width: 24, height: 24 }}
              aria-label={`Remove from collection ${collection.name}`}
              onClick={() => {
                void remove.run(collection.id);
                toast(
                  `Removed from ${collection.name}. It stays in the library.`,
                );
              }}
            >
              <X size={12} aria-hidden="true" />
            </button>
          </span>
        ))}
        <MenuButton
          label="Add to collection"
          className="btn sm"
          align="left"
          items={(all.data ?? [])
            .filter((c) => !member.has(c.id))
            .map((collection) => ({
              label: collection.name,
              onSelect: () => {
                void add.run(collection.id);
                toast(`Added to ${collection.name}.`);
              },
            }))}
        >
          <FolderPlus size={12} aria-hidden="true" /> Add to collection
        </MenuButton>
      </div>
    </div>
  );
}

export function CardPanel({
  detail,
  onChanged,
}: Readonly<{
  detail: DocumentDetail;
  onChanged: () => void;
}>): ReactElement {
  const [editing, setEditing] = useState(false);
  const card = detail.card;
  if (!card) {
    return (
      <div className="stack">
        <p className="small muted">This document has no card yet.</p>
        <CollectionsRow detail={detail} onChanged={onChanged} />
      </div>
    );
  }
  if (editing) {
    return (
      <CardEditor
        card={card}
        documentId={detail.document.id}
        onCancel={() => {
          setEditing(false);
        }}
        onSaved={() => {
          setEditing(false);
          onChanged();
        }}
      />
    );
  }
  const edited = card.edited_fields.map((field) => LABELS[field] ?? field);
  const pending = card.state === "pending";
  return (
    <div className="stack">
      <div className="hstack wrap">
        <span className="badge outline">{card.doc_type}</span>
        {edited.length > 0 && (
          <span className="badge edited">
            <Pencil size={10} aria-hidden="true" />
            Edited by you: {edited.join(", ")}
          </span>
        )}
        {pending && <PendingBadge />}
        <span className="spacer" />
        <button
          type="button"
          className="btn sm"
          onClick={() => {
            setEditing(true);
          }}
        >
          <Pencil size={12} aria-hidden="true" /> Edit card
        </button>
      </div>
      {pending && (
        <p className="small" style={{ color: "var(--ink-2)" }}>
          A basic card from the file's own details. The local model writes the
          full card once it's installed. You can add dates, people and amounts
          yourself now.
        </p>
      )}
      <p>{card.summary}</p>
      <dl className="meta">
        {card.dates.map((date) => (
          <div
            key={`${date.label}${date.value}`}
            style={{ display: "contents" }}
          >
            <dt>{date.label}</dt>
            <dd>{date.value}</dd>
          </div>
        ))}
        {card.people.length > 0 && (
          <>
            <dt>People</dt>
            <dd>{card.people.join(", ")}</dd>
          </>
        )}
        {card.organizations.length > 0 && (
          <>
            <dt>Organizations</dt>
            <dd>{card.organizations.join(", ")}</dd>
          </>
        )}
        {card.amounts.length > 0 && (
          <>
            <dt>Amounts</dt>
            <dd className="mono">{card.amounts.join("; ")}</dd>
          </>
        )}
        <dt>Card by</dt>
        <dd>{card.written_by}</dd>
        {card.state === "pending" && card.state_reason && (
          <>
            <dt>Waiting</dt>
            <dd>{card.state_reason}</dd>
          </>
        )}
      </dl>
      {card.tags.length > 0 && (
        <div className="tagrow" aria-label="Tags">
          {card.tags.map((tag) => (
            <span key={tag} className="tag">
              {tag}
            </span>
          ))}
        </div>
      )}
      <CollectionsRow detail={detail} onChanged={onChanged} />
    </div>
  );
}
