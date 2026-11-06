import { useEffect, useRef, useState } from "react";
import type { ReactElement, ReactNode, Ref } from "react";
import {
  AlertTriangle,
  BookOpen,
  ChevronDown,
  ChevronRight,
  Folder,
  FolderOpen,
  History,
  LayoutGrid,
  Lock,
  MessageSquare,
  NotebookPen,
  PanelRight,
  Plus,
  Search,
  Settings,
  Star,
  X,
} from "lucide-react";

import { getLibraryCounts, listCollections, listThreads } from "./api";
import { ChatMenu } from "./components/ChatMenu";
import { Logo } from "./components/Logo";
import { useThreadsChanged } from "./useThreadsChanged";
import type { Thread } from "./api";
import { go, useRoute, withParam } from "./router";
import { useJobCounts } from "./useEvents";
import { useLoad } from "./useLoad";

function NavItem({
  to,
  icon,
  label,
  current,
  count,
  lock,
}: Readonly<{
  to: string;
  icon: ReactNode;
  label: string;
  current: boolean;
  count?: string;
  lock?: boolean;
}>): ReactElement {
  return (
    <a
      className="sb-item"
      href={`#${to}`}
      aria-current={current ? "page" : undefined}
      title={label}
    >
      {icon}
      <span className="sb-label">
        {label}
        {lock && <span className="sr-only">, Private</span>}
      </span>
      {lock && <Lock size={12} aria-hidden="true" className="sb-count" />}
      {count && <span className="sb-count">{count}</span>}
    </a>
  );
}

function ImportMeter(): ReactElement {
  const route = useRoute();
  const counts = useJobCounts();
  const total = counts?.total ?? 0;
  const done = counts?.done ?? 0;
  const failed = counts?.failed ?? 0;
  const skipped = counts?.skipped ?? 0;
  const line =
    total > 0
      ? `Indexing ${done.toLocaleString("en-US")} of ${total.toLocaleString("en-US")}`
      : "Nothing indexing";
  return (
    <button
      type="button"
      className="sb-meter"
      onClick={() => {
        go(withParam(route, "tray", total > 0 ? "active" : "all"));
      }}
      title="Open the import tray"
    >
      <span className="small sb-wide" style={{ fontWeight: 500 }}>
        {line}
      </span>
      {total > 0 && (
        <span
          className="progress sb-wide"
          style={{ width: "100%" }}
          aria-hidden="true"
        >
          <span
            style={{ width: `${String(Math.round((done / total) * 100))}%` }}
          />
        </span>
      )}
      {failed + skipped > 0 && (
        <span className="sb-meta hstack" style={{ gap: 4 }}>
          <AlertTriangle size={12} aria-hidden="true" /> {failed} failed ·{" "}
          {skipped} skipped
        </span>
      )}
      <PanelRight size={16} aria-hidden="true" className="sb-narrow" />
      <span className="sr-only sb-narrow-text">
        {line}, {failed} failed, {skipped} skipped.
      </span>
      <span className="sr-only"> Open the import tray</span>
    </button>
  );
}

const RECENT_CHATS = 5;

function ChatsSection({
  current,
  on,
}: Readonly<{ current: string | undefined; on: boolean }>): ReactElement {
  const route = useRoute();
  const threads = useLoad(listThreads, route.path.join("/"));
  useThreadsChanged(threads.reload);
  const all = threads.data ?? [];
  const pinned = all.filter((t) => t.pinned);
  const recent = all.filter((t) => !t.pinned).slice(0, RECENT_CHATS);
  const row = (thread: Thread): ReactElement => (
    <div key={thread.id} className="sb-thread">
      <a
        className="sb-item"
        style={{ flex: 1, minWidth: 0 }}
        href={`#/ask/${thread.id}`}
        aria-current={on && current === thread.id ? "page" : undefined}
      >
        {thread.private_tainted ? (
          <Lock size={14} aria-hidden="true" />
        ) : (
          <MessageSquare size={14} aria-hidden="true" />
        )}
        <span className="sb-label">
          {thread.title}
          {thread.private_tainted && <span className="sr-only">, Private</span>}
        </span>
      </a>
      <ChatMenu thread={thread} label={`Actions for ${thread.title}`} />
    </div>
  );
  return (
    <>
      <div className="sb-section-title">Chats</div>
      <a
        className="sb-item"
        href="#/ask/new"
        title="New chat"
        aria-current={on && current === "new" ? "page" : undefined}
      >
        <Plus size={16} aria-hidden="true" />
        <span className="sb-label">New chat</span>
        <span className="sb-count">⌘N</span>
      </a>
      <div className="sb-threads">
        {pinned.length > 0 && (
          <div
            className="sb-meta"
            style={{ padding: "4px 8px 2px", fontSize: 11 }}
          >
            Pinned
          </div>
        )}
        {pinned.map(row)}
        {recent.map(row)}
        <a className="sb-item" href="#/chats">
          <BookOpen size={14} aria-hidden="true" />
          <span className="sb-label">All chats</span>
        </a>
      </div>
    </>
  );
}

export function Sidebar(): ReactElement {
  const route = useRoute();
  const first = route.path.at(0);
  const second = route.path.at(1);
  const view = route.params.get("view");
  const type = route.params.get("type");
  const [typesOpen, setTypesOpen] = useState(false);
  const counts = useLoad(getLibraryCounts, route.path.join("/"));
  const collections = useLoad(listCollections, route.path.join("/"));
  const libCurrent = (v: string | null, t: string | null): boolean =>
    first === "library" && view === v && type === t;
  const types = Object.entries(counts.data?.types ?? {});
  return (
    <nav className="sidebar" aria-label="SecureFolio">
      <div className="sb-brand">
        <Logo size={22} />
        <span className="sb-brand-text">SecureFolio</span>
      </div>
      <div className="sb-scroll">
        <NavItem
          to="/home"
          icon={<Search size={16} aria-hidden="true" />}
          label="Search & Ask"
          current={first === "home" || first === undefined}
        />
        <div className="sb-section-title">Library</div>
        <NavItem
          to="/library"
          icon={<LayoutGrid size={16} aria-hidden="true" />}
          label="All documents"
          current={libCurrent(null, null)}
          count={counts.data?.total.toLocaleString("en-US") ?? ""}
        />
        <NavItem
          to="/library?view=recents"
          icon={<History size={16} aria-hidden="true" />}
          label="Recents"
          current={libCurrent("recents", null)}
        />
        <NavItem
          to="/library?view=starred"
          icon={<Star size={16} aria-hidden="true" />}
          label="Starred"
          current={libCurrent("starred", null)}
        />
        <button
          type="button"
          className="sb-item sb-wide sb-toggle"
          aria-expanded={typesOpen}
          aria-controls="sb-types"
          onClick={() => {
            setTypesOpen((open) => !open);
          }}
        >
          {typesOpen ? (
            <ChevronDown size={16} aria-hidden="true" />
          ) : (
            <ChevronRight size={16} aria-hidden="true" />
          )}
          <span className="sb-label">Types</span>
        </button>
        {typesOpen && (
          <div
            id="sb-types"
            className="sb-wide"
            style={{ display: "contents" }}
          >
            {types.map(([name, count]) => (
              <NavItem
                key={name}
                to={`/library?type=${encodeURIComponent(name)}`}
                icon={<LayoutGrid size={16} aria-hidden="true" />}
                label={name}
                current={libCurrent(null, name)}
                count={count.toLocaleString("en-US")}
              />
            ))}
          </div>
        )}
        <div className="sb-section-title">Collections</div>
        {(collections.data ?? []).map((c) => (
          <NavItem
            key={c.id}
            to={`/collections/${c.id}`}
            icon={<Folder size={16} aria-hidden="true" />}
            label={c.name}
            current={first === "collections" && second === c.id}
            count={String(c.count)}
          />
        ))}
        <NavItem
          to="/collections"
          icon={<Folder size={16} aria-hidden="true" />}
          label="All collections"
          current={first === "collections" && second === undefined}
        />
        <NavItem
          to="/notes"
          icon={<NotebookPen size={16} aria-hidden="true" />}
          label="Notes"
          current={first === "notes"}
        />
        <ChatsSection current={second} on={first === "ask"} />
      </div>
      <div className="sb-foot">
        <NavItem
          to="/sources"
          icon={<FolderOpen size={16} aria-hidden="true" />}
          label="Sources"
          current={first === "sources"}
        />
        <NavItem
          to="/settings/models"
          icon={<Settings size={16} aria-hidden="true" />}
          label="Settings"
          current={first === "settings"}
        />
        <ImportMeter />
      </div>
    </nav>
  );
}

/** True below 960 px, where the right panel becomes a slide-over. */
function useNarrow(): boolean {
  const q = "(max-width: 959px)";
  const [narrow, setNarrow] = useState(() => window.matchMedia(q).matches);
  useEffect(() => {
    const m = window.matchMedia(q);
    const on = (): void => {
      setNarrow(m.matches);
    };
    m.addEventListener("change", on);
    return () => {
      m.removeEventListener("change", on);
    };
  }, []);
  return narrow;
}

/** Page frame: a top bar, the main pane, and an optional right panel that slides over below 960 px. */
export function Page({
  title,
  titleExtra,
  actions,
  children,
  right,
  rightLabel = "Details",
  paneClass,
  paneRef,
}: Readonly<{
  title: string;
  titleExtra?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  right?: ReactNode;
  rightLabel?: string;
  paneClass?: string;
  paneRef?: Ref<HTMLDivElement>; // the main pane, which is what scrolls
}>): ReactElement {
  const route = useRoute();
  const open = route.params.get("panel") === "1";
  const toggleRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const close = (): void => {
    go(withParam(route, "panel", null));
  };
  const narrow = useNarrow();
  const wasOpen = useRef(open);
  // Below 960 px the panel is a modal slide-over: closed, it is inert and hidden from assistive tech;
  // open, everything behind it is inert, so focus stays in the panel until it closes.
  useEffect(() => {
    const panel = panelRef.current;
    const behind = [
      document.querySelector(".app > a.sr-only"),
      document.querySelector(".sidebar"),
      document.querySelector(".main > .topbar"),
      document.querySelector(".main .pane"),
    ];
    const setInert = (el: Element | null, on: boolean): void => {
      if (!el) return;
      if (on) el.setAttribute("inert", "");
      else el.removeAttribute("inert");
    };
    setInert(panel, narrow && !open);
    behind.forEach((el) => {
      setInert(el, narrow && open);
    });
    if (narrow && open && !panel?.contains(document.activeElement))
      panel
        ?.querySelector<HTMLElement>(
          "button, a, [tabindex='0'], [tabindex='-1']",
        )
        ?.focus();
    // After closing, the content behind is no longer inert, so focus can go back to the toggle.
    if (narrow && !open && wasOpen.current) toggleRef.current?.focus();
    wasOpen.current = open;
    return () => {
      behind.forEach((el) => {
        setInert(el, false);
      });
    };
  }, [open, narrow]);
  return (
    <main className="main" id="main">
      <header className="topbar">
        <h1>{title}</h1>
        {titleExtra}
        <span className="spacer" />
        {actions}
        {right && (
          <button
            ref={toggleRef}
            type="button"
            className="btn right-toggle"
            aria-expanded={open}
            aria-controls="right-panel"
            onClick={() => {
              go(withParam(route, "panel", open ? null : "1"));
            }}
          >
            <PanelRight size={14} aria-hidden="true" />
            {rightLabel}
          </button>
        )}
      </header>
      <div className="content">
        <div ref={paneRef} className={`pane ${paneClass ?? ""}`}>
          {children}
        </div>
        {right && (
          <>
            <div
              className={`scrim ${open ? "open" : ""}`}
              onClick={close}
              aria-hidden="true"
            />
            <div
              ref={panelRef}
              id="right-panel"
              className={`right ${open ? "open" : ""}`}
              aria-label={rightLabel}
              role={narrow ? "dialog" : "complementary"}
              aria-modal={narrow && open ? true : undefined}
              onKeyDown={(e) => {
                if (e.key === "Escape" && open) close();
              }}
            >
              <div className="right-inner">
                <button
                  type="button"
                  className="icon-btn right-toggle"
                  aria-label={`Close ${rightLabel.toLowerCase()}`}
                  onClick={close}
                  style={{
                    position: "absolute",
                    top: 14,
                    right: 14,
                    zIndex: 2,
                  }}
                >
                  <X size={16} aria-hidden="true" />
                </button>
                {right}
              </div>
            </div>
          </>
        )}
      </div>
    </main>
  );
}
