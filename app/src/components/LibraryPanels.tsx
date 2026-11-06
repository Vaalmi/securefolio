import { toast } from "../toast";
import type { ReactElement } from "react";
import {
  ExternalLink,
  FolderPlus,
  MessageSquare,
  Star,
  Trash2,
  X,
} from "lucide-react";

import {
  addToCollection,
  createCollection,
  getDocument,
  listCollections,
  removeDocuments,
  removeFromCollection,
  setDocumentStar,
} from "../api";
import type { Card } from "../api";
import { go } from "../router";
import {
  KindIcon,
  MenuButton,
  Modal,
  PendingBadge,
  PrivateBadge,
  StateBadge,
} from "../ui";
import { useAction } from "../useAction";
import { useLoad } from "../useLoad";

function CardFacts({
  card,
  place,
}: Readonly<{ card: Card | null; place: string }>): ReactElement {
  const groups = [
    { label: "Organizations", text: card?.organizations.join(", ") },
    { label: "People", text: card?.people.join(", ") },
  ];
  return (
    <>
      {card?.summary && <p>{card.summary}</p>}
      <dl className="meta">
        {(card?.dates ?? []).map((date) => (
          <div
            key={`${date.label}${date.value}`}
            style={{ display: "contents" }}
          >
            <dt>{date.label}</dt>
            <dd>{date.value}</dd>
          </div>
        ))}
        {groups.map(
          (group) =>
            group.text && (
              <div key={group.label} style={{ display: "contents" }}>
                <dt>{group.label}</dt>
                <dd>{group.text}</dd>
              </div>
            ),
        )}
        {card && card.amounts.length > 0 && (
          <>
            <dt>Amounts</dt>
            <dd className="mono">{card.amounts.join("; ")}</dd>
          </>
        )}
        <dt>File</dt>
        <dd className="mono">{place}</dd>
        <dt>Card by</dt>
        <dd>{card?.written_by ?? "No card yet"}</dd>
      </dl>
      {card && card.tags.length > 0 && (
        <div className="tagrow" aria-label="Tags">
          {card.tags.map((tag) => (
            <span key={tag} className="tag">
              {tag}
            </span>
          ))}
        </div>
      )}
    </>
  );
}

export function Inspector({
  id,
  onChanged,
}: Readonly<{
  id: string | null;
  onChanged: () => void;
}>): ReactElement {
  const detail = useLoad(
    () => (id ? getDocument(id) : Promise.resolve(undefined)),
    id ?? "",
  );
  const star = useAction(
    (starred: boolean) =>
      id ? setDocumentStar(id, starred) : Promise.resolve({}),
    () => {
      detail.reload();
      onChanged();
    },
  );
  if (!id || !detail.data) {
    return (
      <div className="empty" style={{ flex: 1 }}>
        <p className="muted">Select a document to see its card.</p>
      </div>
    );
  }
  const { document: doc, card, locations } = detail.data;
  return (
    <>
      <div className="panel-head" style={{ paddingRight: 48 }}>
        <h2>{doc.title}</h2>
      </div>
      <div className="panel-body">
        <div className="hstack wrap">
          <span className="badge outline">
            <KindIcon kind={doc.file_kind} size={12} />
            {doc.doc_type ?? "Document"} · {doc.file_kind}
          </span>
          {doc.sensitivity === "private" && <PrivateBadge />}
          <StateBadge state={doc.status} />
          {card?.state === "pending" && <PendingBadge />}
        </div>
        <CardFacts card={card} place={locations.at(0)?.path ?? doc.file_name} />
        <div className="hstack wrap">
          <button
            type="button"
            className="btn primary"
            onClick={() => {
              go(`/doc/${doc.id}`);
            }}
          >
            <ExternalLink size={14} aria-hidden="true" /> Open
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => {
              go(`/ask/new?scope=sel:${doc.id}`);
            }}
          >
            <MessageSquare size={14} aria-hidden="true" /> Ask this document
          </button>
          <button
            type="button"
            className="btn"
            aria-pressed={doc.starred}
            onClick={() => {
              void star.run(!doc.starred);
            }}
          >
            <Star
              size={14}
              aria-hidden="true"
              fill={doc.starred ? "currentColor" : "none"}
            />
            {doc.starred ? "Starred" : "Star"}
          </button>
        </div>
      </div>
    </>
  );
}

export function SelectionBar({
  selected,
  collectionId,
  includesPrivate,
  onChanged,
  onRemove,
  onClear,
}: Readonly<{
  selected: Set<string>;
  collectionId: string | undefined;
  includesPrivate: boolean;
  onChanged: () => void;
  onRemove: () => void;
  onClear: () => void;
}>): ReactElement {
  const ids = [...selected];
  const collections = useLoad(listCollections, "collections");
  const addTo = useAction(
    (id: string) => addToCollection(id, ids),
    () => {
      collections.reload();
    },
  );
  const makeNew = useAction(async () => {
    const name = `New collection ${new Date().toLocaleDateString("en-US")}`;
    const made = await createCollection(name);
    await addToCollection(made.id, ids);
    toast(`Created ${name} with ${String(ids.length)} documents.`);
  });
  const takeOut = useAction(
    () =>
      collectionId
        ? removeFromCollection(collectionId, ids)
        : Promise.resolve({}),
    onChanged,
  );
  return (
    <div className="selbar" role="toolbar" aria-label="Selected documents">
      <strong>{selected.size} selected</strong>
      <button
        type="button"
        className="btn primary sm"
        onClick={() => {
          go(`/ask/new?scope=sel:${ids.join(",")}`);
        }}
      >
        <MessageSquare size={12} aria-hidden="true" /> Ask about these{" "}
        {selected.size}
      </button>
      <MenuButton
        label="Add to collection"
        className="btn sm"
        align="left"
        items={[
          ...(collections.data ?? []).map((collection) => ({
            label: collection.name,
            onSelect: () => {
              void addTo.run(collection.id);
              toast(
                `Added ${String(ids.length)} documents to ${collection.name}.`,
              );
            },
          })),
          {
            label: "New collection…",
            icon: FolderPlus,
            onSelect: () => {
              void makeNew.run();
            },
          },
        ]}
      >
        <FolderPlus size={12} aria-hidden="true" /> Add to collection
      </MenuButton>
      {collectionId && (
        <button
          type="button"
          className="btn sm"
          onClick={() => {
            void takeOut.run();
            toast(
              `Removed ${String(ids.length)} from this collection. They stay in the library.`,
            );
          }}
        >
          <X size={12} aria-hidden="true" /> Remove from collection
        </button>
      )}
      <button type="button" className="btn sm" onClick={onRemove}>
        <Trash2 size={12} aria-hidden="true" /> Remove from library
      </button>
      {includesPrivate && <PrivateBadge label="Includes Private" />}
      <span className="spacer" />
      <button type="button" className="btn sm ghost" onClick={onClear}>
        Clear selection
      </button>
    </div>
  );
}

export function RemoveModal({
  ids,
  onClose,
  onDone,
}: Readonly<{
  ids: string[];
  onClose: () => void;
  onDone: () => void;
}>): ReactElement {
  const remove = useAction(
    () => removeDocuments(ids),
    () => {
      onClose();
      onDone();
      toast(
        `Removed ${String(ids.length)} documents from the library. The files on your Mac weren't touched.`,
      );
    },
  );
  return (
    <Modal
      title={`Remove ${String(ids.length)} documents from the library?`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn danger"
            onClick={() => {
              void remove.run();
            }}
          >
            Remove from library
          </button>
        </>
      }
    >
      <div className="modal-body">
        <p>
          The files stay where they are on your Mac. SecureFolio stops showing
          them and won't add them again from their folders. Their notes and
          chats are kept.
        </p>
        <p className="small muted">
          To delete a copy SecureFolio made of a file you dragged in, use Move
          copy to Trash on that document.
        </p>
        {remove.error && (
          <p role="alert" className="small">
            {remove.error}
          </p>
        )}
      </div>
    </Modal>
  );
}
