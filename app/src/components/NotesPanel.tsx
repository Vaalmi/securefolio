import { useEffect, useRef, useState } from "react";
import type { ReactElement } from "react";
import { Pencil, Trash2 } from "lucide-react";

import { createNote, deleteNote, editNote, listNotes } from "../api";
import type { Note } from "../api";
import { fmtMoment } from "../format";
import { go } from "../router";
import { toast } from "../toast";
import { PrivateBadge } from "../ui";
import { useAction } from "../useAction";
import { useLoad } from "../useLoad";

function NoteEditForm({
  note,
  onDone,
}: Readonly<{ note: Note; onDone: (saved: boolean) => void }>): ReactElement {
  const [title, setTitle] = useState(note.title);
  const [body, setBody] = useState(note.body);
  const [tried, setTried] = useState(false);
  const headingField = useRef<HTMLInputElement>(null);
  useEffect(() => {
    headingField.current?.focus();
  }, []);
  const save = useAction(
    () => editNote(note.id, title, body),
    () => {
      toast("Note saved. Its citations are kept.");
      onDone(true);
    },
  );
  const noTitle = tried && !title.trim();
  const noBody = tried && !body.trim();
  return (
    <form
      className="stack"
      style={{ gap: 6 }}
      aria-label={`Edit note ${note.title}`}
      onSubmit={(event) => {
        event.preventDefault();
        setTried(true);
        if (title.trim() && body.trim()) void save.run();
      }}
    >
      <div className="field">
        <label htmlFor={`nt-${note.id}`}>Heading</label>
        <input
          ref={headingField}
          id={`nt-${note.id}`}
          className="input"
          value={title}
          onChange={(event) => {
            setTitle(event.target.value);
          }}
          aria-invalid={noTitle}
          aria-describedby={`nt-${note.id}-err`}
        />
        <span
          id={`nt-${note.id}-err`}
          className="small"
          role="status"
          style={{ color: "var(--ink-2)" }}
        >
          {noTitle ? "A note needs a heading." : ""}
        </span>
      </div>
      <div className="field">
        <label htmlFor={`nb-${note.id}`}>Text</label>
        <textarea
          id={`nb-${note.id}`}
          className="textarea"
          value={body}
          onChange={(event) => {
            setBody(event.target.value);
          }}
          aria-invalid={noBody}
          aria-describedby={`nb-${note.id}-err`}
        />
        <span
          id={`nb-${note.id}-err`}
          className="small"
          role="status"
          style={{ color: "var(--ink-2)" }}
        >
          {noBody ? "A note needs some text." : ""}
        </span>
      </div>
      {note.sensitivity === "private" && (
        <span className="small muted">
          This note stays Private. Editing it doesn't change that.
        </span>
      )}
      {save.error && (
        <p role="alert" className="small">
          {save.error}
        </p>
      )}
      <span className="hstack">
        <button type="submit" className="btn primary sm">
          Save
        </button>
        <button
          type="button"
          className="btn sm"
          onClick={() => {
            onDone(false);
          }}
        >
          Cancel
        </button>
      </span>
    </form>
  );
}

function NoteBody({ note }: Readonly<{ note: Note }>): ReactElement {
  return (
    <p className="small">
      {note.body}
      {note.citations.map((citation, i) => {
        const target = citation.opens.element;
        return (
          <button
            key={`${citation.element_id ?? "card"}${String(i)}`}
            type="button"
            className="cite"
            aria-label={`Citation ${String(i + 1)}: ${target?.label ?? "stored text"}. Open`}
            onClick={() => {
              const place = target?.id ?? citation.element_id;
              const within = place ? `el=${place}` : "tab=card";
              go(`/doc/${citation.document_id}?${within}`);
            }}
          >
            {i + 1}
          </button>
        );
      })}
    </p>
  );
}

/** A note, with its heading and text edited together in one form. */
export function NoteCard({
  note,
  onChanged,
}: Readonly<{
  note: Note;
  onChanged: () => void;
}>): ReactElement {
  const [editing, setEditing] = useState(false);
  const editButton = useRef<HTMLButtonElement>(null);
  const remove = useAction(
    () => deleteNote(note.id),
    () => {
      toast(`Deleted note “${note.title}”.`);
      onChanged();
    },
  );
  return (
    <div className="passage">
      {!editing && (
        <span className="hstack" style={{ gap: 6 }}>
          <strong>{note.title}</strong>
          {note.sensitivity === "private" && <PrivateBadge />}
        </span>
      )}
      {editing ? (
        <NoteEditForm
          note={note}
          onDone={(saved) => {
            setEditing(false);
            if (saved) onChanged();
            window.setTimeout(() => editButton.current?.focus(), 0);
          }}
        />
      ) : (
        <NoteBody note={note} />
      )}
      <span className="hstack">
        <span className="xs muted">
          {note.source_label} · {fmtMoment(note.created_at)}
        </span>
        <span className="spacer" />
        {!editing && (
          <>
            <button
              ref={editButton}
              type="button"
              className="btn sm ghost"
              onClick={() => {
                setEditing(true);
              }}
              aria-label={`Edit note ${note.title}`}
            >
              <Pencil size={12} aria-hidden="true" /> Edit
            </button>
            <button
              type="button"
              className="btn sm ghost"
              onClick={() => {
                void remove.run();
              }}
              aria-label={`Delete note ${note.title}`}
            >
              <Trash2 size={12} aria-hidden="true" /> Delete
            </button>
          </>
        )}
      </span>
    </div>
  );
}

export function NotesPanel({
  documentId,
  isPrivate = false,
}: Readonly<{
  documentId: string;
  isPrivate?: boolean;
}>): ReactElement {
  const notes = useLoad(() => listNotes(documentId), documentId);
  const [draft, setDraft] = useState("");
  const [heading, setHeading] = useState("");
  const save = useAction(
    () => createNote(draft, documentId, heading),
    () => {
      setDraft("");
      setHeading("");
      toast("Note saved.");
      notes.reload();
    },
  );
  return (
    <div className="stack">
      {notes.data?.length === 0 && (
        <p className="muted small">No notes on this document yet.</p>
      )}
      {(notes.data ?? []).map((note) => (
        <NoteCard key={note.id} note={note} onChanged={notes.reload} />
      ))}
      <h3 className="small" style={{ fontWeight: 600 }}>
        New note
      </h3>
      <div className="field">
        <label htmlFor="note-new-heading">Heading (optional)</label>
        <input
          id="note-new-heading"
          className="input"
          value={heading}
          onChange={(event) => {
            setHeading(event.target.value);
          }}
          placeholder={
            draft.trim()
              ? draft.trim().slice(0, 40)
              : "From the first words of the note"
          }
          aria-describedby="note-new-help"
        />
        <span id="note-new-help" className="help">
          Left empty, the heading is the first 40 characters of the text.
        </span>
      </div>
      <div className="field">
        <label htmlFor="note-new">Text</label>
        <textarea
          id="note-new"
          className="textarea"
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value);
          }}
        />
      </div>
      {isPrivate && (
        <span className="small muted">
          Notes on this document are Private, like the document.
        </span>
      )}
      {save.error && (
        <p role="alert" className="small">
          {save.error}
        </p>
      )}
      <button
        type="button"
        className="btn"
        style={{ alignSelf: "flex-start" }}
        disabled={!draft.trim()}
        onClick={() => {
          void save.run();
        }}
      >
        Save note
      </button>
    </div>
  );
}
