import { useState } from "react";
import type { ReactElement } from "react";
import { MoreHorizontal, Pencil, Pin, Trash2 } from "lucide-react";

import { deleteThread, updateThread } from "../api";
import type { Thread } from "../api";
import { go, useRoute } from "../router";
import { toast } from "../toast";
import { MenuButton, Modal } from "../ui";
import { useAction } from "../useAction";
import { threadsChanged } from "../useThreadsChanged";

export function ChatMenu({
  thread,
  label,
}: Readonly<{ thread: Thread; label: string }>): ReactElement {
  const route = useRoute();
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(thread.title);
  const change = useAction(
    (changes: Parameters<typeof updateThread>[1]) =>
      updateThread(thread.id, changes),
    threadsChanged,
  );
  const remove = useAction(
    () => deleteThread(thread.id),
    () => {
      toast("Deleted the chat.");
      threadsChanged();
      if (route.path.at(0) === "ask" && route.path.at(1) === thread.id) {
        go("/ask/new");
      }
    },
  );
  return (
    <>
      <MenuButton
        floating
        label={label}
        items={[
          {
            label: "Rename",
            icon: Pencil,
            onSelect: () => {
              setName(thread.title);
              setRenaming(true);
            },
          },
          {
            label: thread.pinned ? "Unpin" : "Pin",
            icon: Pin,
            onSelect: () => {
              void change.run({ pinned: !thread.pinned });
            },
          },
          {
            label: "Delete",
            icon: Trash2,
            onSelect: () => {
              void remove.run();
            },
          },
        ]}
      >
        <MoreHorizontal size={16} aria-hidden="true" />
      </MenuButton>
      {(change.error ?? remove.error) && (
        <span role="alert" className="small">
          {change.error ?? remove.error}
        </span>
      )}
      {renaming && (
        <Modal
          title="Rename this chat"
          onClose={() => {
            setRenaming(false);
          }}
          footer={
            <button
              type="button"
              className="btn primary"
              disabled={!name.trim()}
              onClick={() => {
                void change.run({ title: name.trim() });
                setRenaming(false);
              }}
            >
              Save
            </button>
          }
        >
          <div className="modal-body">
            <label htmlFor={`chat-name-${thread.id}`} className="sr-only">
              Chat name
            </label>
            <input
              id={`chat-name-${thread.id}`}
              className="input"
              value={name}
              onChange={(event) => {
                setName(event.target.value);
              }}
            />
          </div>
        </Modal>
      )}
    </>
  );
}
