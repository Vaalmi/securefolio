import { toast } from "../toast";
import { useState } from "react";
import type { ReactElement } from "react";
import { Folder, FolderPlus, MessageSquare } from "lucide-react";

import { createCollection, listCollections } from "../api";
import { fmtMoment } from "../format";
import { go } from "../router";
import { Page } from "../shell";
import {} from "../ui";
import { useAction } from "../useAction";
import { useLoad } from "../useLoad";

export function Collections(): ReactElement {
  const [name, setName] = useState("");
  const collections = useLoad(listCollections, "collections");
  const create = useAction(
    () => createCollection(name),
    () => {
      toast(`Created ${name.trim()}.`);
      setName("");
      collections.reload();
    },
  );
  return (
    <Page title="Collections">
      <p className="small" style={{ color: "var(--ink-2)", marginBottom: 12 }}>
        Collections you make yourself. Add documents from the library list or
        the viewer. A collection can be the scope of a chat.
      </p>
      {collections.error && <p role="alert">{collections.error}</p>}
      <div className="grid2">
        {(collections.data ?? []).map((collection) => (
          <div key={collection.id} className="card stack" style={{ gap: 8 }}>
            <div className="hstack">
              <Folder size={16} aria-hidden="true" />
              <a
                href={`#/collections/${collection.id}`}
                style={{ fontWeight: 600, color: "var(--ink)" }}
              >
                {collection.name}
              </a>
            </div>
            <span className="small muted">
              {collection.count} documents · updated{" "}
              {fmtMoment(collection.updated_at)}
            </span>
            {collection.count === 0 ? (
              <p className="small" style={{ color: "var(--ink-2)" }}>
                Empty. Select documents in the library and choose Add to
                collection.
              </p>
            ) : (
              <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
                {collection.titles.map((title) => (
                  <li key={title}>{title}</li>
                ))}
              </ul>
            )}
            <button
              type="button"
              className="btn sm"
              style={{ alignSelf: "flex-start" }}
              disabled={collection.count === 0}
              onClick={() => {
                go(`/ask/new?scope=${collection.id}`);
              }}
            >
              <MessageSquare size={12} aria-hidden="true" /> Ask this collection
            </button>
          </div>
        ))}
        <form
          className="card stack"
          style={{ gap: 8 }}
          onSubmit={(event) => {
            event.preventDefault();
            if (name.trim()) void create.run();
          }}
        >
          <label htmlFor="new-coll" style={{ fontWeight: 600 }}>
            New collection
          </label>
          <input
            id="new-coll"
            className="input"
            value={name}
            onChange={(event) => {
              setName(event.target.value);
            }}
            placeholder="Name"
          />
          {create.error && (
            <p role="alert" className="small">
              {create.error}
            </p>
          )}
          <button
            type="submit"
            className="btn sm"
            style={{ alignSelf: "flex-start" }}
            disabled={!name.trim()}
          >
            <FolderPlus size={12} aria-hidden="true" /> Create
          </button>
        </form>
      </div>
    </Page>
  );
}
