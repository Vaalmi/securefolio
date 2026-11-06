import type { ReactElement } from "react";
import {
  FileSearch,
  RefreshCw,
  ScanLine,
  Sparkles,
  Trash2,
} from "lucide-react";

import {
  reindexDocument,
  removeDocuments,
  rereadPage,
  trashManagedCopy,
} from "../api";
import type { DocumentDetail, PageCost } from "../api";
import { showInFinder } from "../desktop";
import { go } from "../router";
import { toast } from "../toast";
import { MenuButton, Modal } from "../ui";
import { useAction } from "../useAction";

const PAGE_READS = new Set(["PDF", "PNG", "JPEG", "WEBP", "HEIC"]);

export type Confirm = "remove" | "trash" | "cloud" | null;

export function MoreMenu({
  detail,
  page,
  cost,
  onConfirm,
  onChanged,
}: Readonly<{
  detail: DocumentDetail;
  page: number;
  cost: PageCost | undefined;
  onConfirm: (confirm: Confirm) => void;
  onChanged: () => void;
}>): ReactElement {
  const doc = detail.document;
  const act = useAction(async (work: () => Promise<object>, said: string) => {
    await work();
    toast(said);
    onChanged();
  });
  const canReadPages =
    PAGE_READS.has(doc.file_kind) && doc.status === "indexed";
  const location = detail.locations.at(0);
  const copied = detail.locations.some((l) => l.storage_mode === "managed");
  const items = [
    ...(location
      ? [
          {
            label: "Show in Finder",
            icon: FileSearch,
            onSelect: () => {
              void showInFinder(location.path);
            },
          },
        ]
      : []),
    {
      label: "Re-index",
      icon: RefreshCw,
      onSelect: () => {
        void act.run(
          () => reindexDocument(doc.id),
          "Queued to index again. Notes and chats stay linked.",
        );
      },
    },
    ...(canReadPages
      ? [
          {
            label: "Re-read this page",
            icon: ScanLine,
            onSelect: () => {
              void act.run(
                () => rereadPage(doc.id, page),
                "Reading this page again on this Mac.",
              );
            },
          },
        ]
      : []),
    ...(canReadPages && detail.cloud_page_allowed && cost
      ? [
          {
            // Unavailable for now, it still opens, to say why.
            label: cost.unavailable
              ? "Read this page with Cloud AI (unavailable)…"
              : "Read this page with Cloud AI…",
            icon: Sparkles,
            onSelect: () => {
              onConfirm("cloud");
            },
          },
        ]
      : []),
    {
      label: "Remove from library…",
      icon: Trash2,
      onSelect: () => {
        onConfirm("remove");
      },
    },
    ...(copied
      ? [
          {
            label: "Move copy to Trash…",
            icon: Trash2,
            onSelect: () => {
              onConfirm("trash");
            },
          },
        ]
      : []),
  ];
  return (
    <>
      {act.error && (
        <span role="alert" className="small">
          {act.error}
        </span>
      )}
      <MenuButton label="More document actions" className="btn" items={items}>
        More
      </MenuButton>
    </>
  );
}

export function ConfirmLeaving({
  id,
  kind,
  onClose,
}: Readonly<{
  id: string;
  kind: "remove" | "trash";
  onClose: () => void;
}>): ReactElement {
  const trash = kind === "trash";
  const finish = useAction(
    () => (trash ? trashManagedCopy(id) : removeDocuments([id])),
    () => {
      toast(
        trash
          ? "Moved SecureFolio's copy to the Trash. You can put it back from the Trash."
          : "Removed from the library. The file on your Mac wasn't touched.",
      );
      go("/library");
    },
  );
  return (
    <Modal
      title={
        trash
          ? "Move SecureFolio's copy to the Trash?"
          : "Remove this document from the library?"
      }
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
              void finish.run();
            }}
          >
            {trash ? "Move copy to Trash" : "Remove from library"}
          </button>
        </>
      }
    >
      <div className="modal-body">
        <p>
          {trash
            ? "This deletes the copy SecureFolio made when you dragged the file in. It goes to the Trash, so you can put it back. Notes and chats that cite it keep their stored text."
            : "The file stays where it is on your Mac. SecureFolio stops showing it and won't add it again from its folder. Its notes and chats are kept."}
        </p>
        {finish.error && (
          <p role="alert" className="small">
            {finish.error}
          </p>
        )}
      </div>
    </Modal>
  );
}
