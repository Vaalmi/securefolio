import type { ReactElement } from "react";
import { Search } from "lucide-react";

import { listNotes, listSavedQueries } from "../api";
import { NoteCard } from "../components/NotesPanel";
import { Page } from "../shell";
import { useLoad } from "../useLoad";

export function Notes(): ReactElement {
  const notes = useLoad(() => listNotes(), "notes");
  const queries = useLoad(listSavedQueries, "queries");
  return (
    <Page title="Notes">
      <div className="stack" style={{ maxWidth: 760 }}>
        {notes.error && <p role="alert">{notes.error}</p>}
        {notes.data?.length === 0 && (
          <p className="muted">
            No notes yet. Save an answer as a note, or write one on a document.
          </p>
        )}
        {(notes.data ?? []).map((note) => (
          <NoteCard key={note.id} note={note} onChanged={notes.reload} />
        ))}
        <p className="small muted">
          Citations in notes still open at their place after a document is
          indexed again.
        </p>
        {(queries.data?.length ?? 0) > 0 && (
          <section aria-labelledby="h-saved">
            <h2 id="h-saved" className="h-sm">
              Saved searches
            </h2>
            {queries.data?.map((query) => (
              <a
                key={query.id}
                className="list-row"
                href={`#/home?q=${encodeURIComponent(query.text)}`}
                style={{ textDecoration: "none", color: "inherit" }}
              >
                <Search size={14} aria-hidden="true" />{" "}
                <span className="title">{query.text}</span>
              </a>
            ))}
          </section>
        )}
      </div>
    </Page>
  );
}
