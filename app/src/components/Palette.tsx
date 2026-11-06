import { useId, useState } from "react";
import type { KeyboardEvent, ReactElement } from "react";
import {
  FileText,
  FolderPlus,
  History,
  Inbox,
  MessageSquarePlus,
  Search,
  Settings,
  Upload,
  WifiOff,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { listThreads, search } from "../api";
import { go, useRoute, withParam } from "../router";
import { Modal } from "../ui";
import { useLoad } from "../useLoad";

interface Item {
  id: string;
  group: "Recent" | "Actions" | "Documents";
  label: string;
  hint?: string;
  icon: LucideIcon;
  to: string;
}

const ACTIONS: Item[] = [
  {
    id: "new",
    group: "Actions",
    label: "New chat",
    hint: "⌘N",
    icon: MessageSquarePlus,
    to: "/ask/new",
  },
  {
    id: "search",
    group: "Actions",
    label: "Search the library",
    hint: "⌘F",
    icon: Search,
    to: "/home",
  },
  {
    id: "folder",
    group: "Actions",
    label: "Add a watched folder",
    icon: FolderPlus,
    to: "/sources",
  },
  {
    id: "import",
    group: "Actions",
    label: "Import files",
    hint: "⌘O",
    icon: Upload,
    to: "/home?import=1",
  },
  {
    id: "tray",
    group: "Actions",
    label: "Open the import tray",
    hint: "⇧⌘I",
    icon: Inbox,
    to: "/home?tray=all",
  },
  {
    id: "offline",
    group: "Actions",
    label: "Turn on Offline mode",
    icon: WifiOff,
    to: "/settings/local",
  },
  {
    id: "settings",
    group: "Actions",
    label: "Settings",
    hint: "⌘,",
    icon: Settings,
    to: "/settings/models",
  },
];
const GROUPS = ["Recent", "Actions", "Documents"] as const;
const SHOWN = 10;
const RECENT = 2;

// The ⌘K command palette. Private documents stay out, as in the default search.
export function Palette(): ReactElement {
  const route = useRoute();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const id = useId();
  const threads = useLoad(() => listThreads(), "palette-threads");
  const found = useLoad(
    () =>
      query.trim()
        ? search({ q: query, include_private: false })
        : Promise.resolve(null),
    query,
  );
  const recent = (threads.data ?? []).slice(0, RECENT).map((thread): Item => ({
    id: `t-${thread.id}`,
    group: "Recent",
    label: thread.title,
    hint: "Chat",
    icon: History,
    to: `/ask/${thread.id}`,
  }));
  const documents = (found.data?.documents ?? []).map((document): Item => ({
    id: `d-${document.id}`,
    group: "Documents",
    label: document.title,
    icon: FileText,
    to: `/doc/${document.id}`,
  }));
  const words = query.trim().toLowerCase();
  const all = [...recent, ...ACTIONS];
  const shown = words
    ? [
        ...all.filter((item) => item.label.toLowerCase().includes(words)),
        ...documents,
      ].slice(0, SHOWN)
    : all;
  const activeItem = shown.at(active);
  const close = (): void => {
    go(withParam(route, "palette", null));
  };
  const onKey = (event: KeyboardEvent<HTMLInputElement>): void => {
    const chosen = shown.at(active);
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((at) => Math.min(at + 1, shown.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((at) => Math.max(at - 1, 0));
    } else if (event.key === "Enter" && chosen) {
      event.preventDefault();
      go(chosen.to);
    }
  };
  return (
    <Modal
      title="Command palette"
      onClose={close}
      className="palette"
      hideTitle
      initialFocus="input"
    >
      <div className="palette-input">
        <Search size={18} aria-hidden="true" className="muted" />
        <input
          role="combobox"
          aria-expanded="true"
          aria-controls={id}
          aria-activedescendant={
            activeItem ? `${id}-${activeItem.id}` : undefined
          }
          aria-label="Type a command or a document name"
          placeholder="Type a command or a document name"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(0);
          }}
          onKeyDown={onKey}
        />
        <span className="kbd">esc</span>
      </div>
      <div
        id={id}
        role="listbox"
        aria-label="Commands"
        style={{ padding: 8, overflow: "auto" }}
      >
        {GROUPS.map((group) => {
          const items = shown.filter((item) => item.group === group);
          if (items.length === 0) return null;
          return (
            <div key={group} role="group" aria-label={group}>
              <div className="results-group" aria-hidden="true">
                {group}
              </div>
              {items.map((item) => {
                const at = shown.indexOf(item);
                return (
                  <div
                    key={item.id}
                    id={`${id}-${item.id}`}
                    role="option"
                    tabIndex={-1}
                    aria-selected={at === active}
                    className="result"
                    onMouseEnter={() => {
                      setActive(at);
                    }}
                    onMouseDown={(event) => {
                      event.preventDefault();
                      go(item.to);
                    }}
                  >
                    <span className="r-icon">
                      <item.icon size={16} aria-hidden="true" />
                    </span>
                    <span
                      className="r-main r-title"
                      style={{ alignSelf: "center" }}
                    >
                      {item.label}
                    </span>
                    {item.hint && (
                      <span
                        className="small r-meta"
                        style={{ alignSelf: "center" }}
                      >
                        {item.hint}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
      <div className="results-foot" style={{ padding: "8px 16px" }}>
        <span>
          <span className="kbd">↑</span> <span className="kbd">↓</span> move
        </span>
        <span>
          <span className="kbd">↵</span> run
        </span>
        <span>
          <span className="kbd">esc</span> close
        </span>
      </div>
    </Modal>
  );
}
