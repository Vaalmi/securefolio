import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent, ReactElement, RefObject, UIEvent } from "react";
import {
  ArrowDown,
  ArrowUp,
  Check,
  RotateCcw,
  Star,
  XCircle,
} from "lucide-react";

import type { DocumentRow } from "../api";
import { fmtDate, fmtSize } from "../format";
import { go } from "../router";
import { KindIcon, PrivateBadge, Skeleton, StateBadge } from "../ui";
import type { LibraryRows, SortKey } from "./useLibraryRows";

export const ROW = 32;
const RECENTS_TITLE = "Recents stay in the order you opened them";

interface GridProps {
  title: string;
  list: LibraryRows;
  error: string | undefined;
  sort: { key: SortKey; descending: boolean };
  setSort: (sort: { key: SortKey; descending: boolean }) => void;
  selected: Set<string>;
  setSelected: (selected: Set<string>) => void;
  active: number;
  setActive: (index: number) => void;
  first: number;
  last: number;
  height: number;
  bodyRef: RefObject<HTMLDivElement | null>;
  allIds: () => Promise<string[]>;
  onScroll: (event: UIEvent<HTMLDivElement>) => void;
  onClearFilters: () => void;
}

function Header({
  label,
  column,
  sort,
  setSort,
  recents,
  className,
}: Readonly<{
  label: string;
  column: SortKey;
  sort: GridProps["sort"];
  setSort: GridProps["setSort"];
  recents: boolean;
  className?: string;
}>): ReactElement {
  const sorted = sort.key === column && !recents;
  const direction = sort.descending ? "descending" : "ascending";
  return (
    <span
      role="columnheader"
      aria-sort={sorted ? direction : "none"}
      className={`cell ${className ?? ""}`}
    >
      <button
        type="button"
        disabled={recents}
        title={recents ? RECENTS_TITLE : undefined}
        onClick={() => {
          setSort({
            key: column,
            descending:
              sort.key === column ? !sort.descending : column === "date",
          });
        }}
      >
        {label}{" "}
        {sorted &&
          (sort.descending ? (
            <ArrowDown size={12} aria-hidden="true" />
          ) : (
            <ArrowUp size={12} aria-hidden="true" />
          ))}
      </button>
    </span>
  );
}

function Row({
  row,
  index,
  isActive,
  selected,
  onClick,
  onToggle,
  onFocus,
  rowRef,
}: Readonly<{
  row: DocumentRow | undefined;
  index: number;
  isActive: boolean;
  selected: boolean;
  onClick: (event: {
    metaKey: boolean;
    ctrlKey: boolean;
    shiftKey: boolean;
  }) => void;
  onToggle: (event: { shiftKey: boolean }) => void;
  onFocus: () => void;
  rowRef: (element: HTMLDivElement | null) => void;
}>): ReactElement {
  if (!row) {
    return (
      <div
        role="row"
        aria-rowindex={index + 2}
        className="grid-row"
        style={{ top: index * ROW }}
        ref={rowRef}
      >
        <span role="gridcell" className="cell">
          <Skeleton w={16} h={16} />
        </span>
        <span role="gridcell" className="cell">
          <Skeleton w="60%" />
        </span>
      </div>
    );
  }
  return (
    <div
      ref={rowRef}
      role="row"
      aria-rowindex={index + 2}
      aria-selected={selected}
      tabIndex={isActive ? 0 : -1}
      className={`grid-row row-focus ${isActive ? "active" : ""}`}
      style={{ top: index * ROW }}
      onClick={onClick}
      onDoubleClick={() => {
        go(`/doc/${row.id}`);
      }}
      onKeyDown={(event) => {
        if (event.key === " ") {
          event.preventDefault();
          onToggle({ shiftKey: false });
        } else if (event.key === "Enter") {
          event.preventDefault();
          go(`/doc/${row.id}`);
        }
      }}
      onFocus={onFocus}
    >
      <span role="gridcell" className="cell">
        <button
          type="button"
          tabIndex={-1}
          className={`cbox ${selected ? "on" : ""}`}
          aria-label={`Select ${row.title}`}
          onClick={(event) => {
            event.stopPropagation();
            onToggle(event);
          }}
        >
          {selected && <Check size={12} aria-hidden="true" />}
        </button>
      </span>
      <span role="gridcell" className="cell title">
        <KindIcon kind={row.file_kind} />
        <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
          {row.title}
        </span>
        {row.sensitivity === "private" && <PrivateBadge />}
        <StateBadge state={row.status} />
        {row.starred && (
          <Star
            size={12}
            aria-label="Starred"
            fill="currentColor"
            style={{ color: "var(--warning)", flex: "none" }}
          />
        )}
      </span>
      <span role="gridcell" className="cell col-type">
        {row.doc_type ?? row.file_kind}
      </span>
      <span role="gridcell" className="cell mono small">
        {fmtDate(row.doc_date)}
      </span>
      <span role="gridcell" className="cell col-folder">
        {row.folder ?? ""}
      </span>
      <span role="gridcell" className="cell mono small">
        {fmtSize(row.size_bytes)}
      </span>
    </div>
  );
}

function GridHead({
  total,
  selected,
  sort,
  setSort,
  recents,
  onAll,
}: Readonly<{
  total: number;
  selected: Set<string>;
  sort: GridProps["sort"];
  setSort: GridProps["setSort"];
  recents: boolean;
  onAll: (all: boolean) => void;
}>): ReactElement {
  const allBox = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (allBox.current) {
      allBox.current.indeterminate = selected.size > 0 && selected.size < total;
    }
  });
  const column = { sort, setSort, recents };
  return (
    <div className="grid-head" role="row" aria-rowindex={1}>
      <span role="columnheader" className="cell">
        <input
          ref={allBox}
          type="checkbox"
          className="all-box"
          aria-label={`Select all ${total.toLocaleString("en-US")} documents`}
          checked={selected.size === total}
          onChange={(event) => {
            onAll(event.target.checked);
          }}
        />
      </span>
      <Header label="Name" column="title" {...column} />
      <Header label="Type" column="type" className="col-type" {...column} />
      <Header label="Date" column="date" {...column} />
      <span role="columnheader" className="cell col-folder">
        Folder
      </span>
      <Header label="Size" column="size" {...column} />
    </div>
  );
}

function Message({
  list,
  error,
  onClearFilters,
}: Readonly<{
  list: LibraryRows;
  error: string | undefined;
  onClearFilters: () => void;
}>): ReactElement | null {
  if (error) {
    return (
      <div className="empty" role="alert">
        <span className="empty-icon">
          <XCircle size={20} aria-hidden="true" />
        </span>
        <strong>Couldn't load the library</strong>
        <p className="small muted">
          SecureFolio's library service didn't answer. Your files are safe.
        </p>
        <button type="button" className="btn primary sm" onClick={list.reload}>
          <RotateCcw size={12} aria-hidden="true" /> Try again
        </button>
      </div>
    );
  }
  if (!list.loaded) {
    return (
      <div
        style={{ padding: "4px 12px" }}
        role="status"
        aria-busy="true"
        aria-label="Loading documents"
      >
        {Array.from({ length: 14 }, (_, i) => (
          <div key={i} className="hstack" style={{ height: ROW, gap: 12 }}>
            <Skeleton w={16} h={16} />
            <Skeleton w={`${String(40 + ((i * 13) % 40))}%`} />
            <Skeleton w={80} />
          </div>
        ))}
      </div>
    );
  }
  if (list.total > 0) return null;
  return (
    <div className="empty">
      <p>No documents match these filters.</p>
      <button type="button" className="btn sm" onClick={onClearFilters}>
        Clear filters
      </button>
    </div>
  );
}

function GridFoot(): ReactElement {
  return (
    <div className="grid-foot" aria-hidden="true">
      <span>
        <span className="kbd">↑</span> <span className="kbd">↓</span> move
      </span>
      <span>
        <span className="kbd">space</span> select
      </span>
      <span>
        <span className="kbd">⇧↓</span> add to selection
      </span>
      <span>
        <span className="kbd">↵</span> open
      </span>
    </div>
  );
}

export function Grid(props: Readonly<GridProps>): ReactElement {
  const { title, list, error, sort, setSort, selected, setSelected } = props;
  const { active, setActive, first, last, height, bodyRef } = props;
  const [anchor, setAnchor] = useState(0);
  const rowRefs = useRef(new Map<number, HTMLDivElement>());
  const focusPending = useRef(false);
  const total = list.total;
  const recents = title === "Recents";
  const showGrid = !error && list.loaded && total > 0;

  useEffect(() => {
    if (focusPending.current) {
      rowRefs.current.get(active)?.focus();
      focusPending.current = false;
    }
  });

  const rangeIds = (from: number, to: number): string[] => {
    const ids: string[] = [];
    for (let i = Math.min(from, to); i <= Math.max(from, to); i++) {
      const id = list.at(i)?.id;
      if (id) ids.push(id);
    }
    return ids;
  };
  const moveTo = (target: number, extend = false): void => {
    const next = Math.max(0, Math.min(total - 1, target));
    setActive(next);
    if (extend) setSelected(new Set(rangeIds(anchor, next)));
    else setAnchor(next);
    const element = bodyRef.current;
    if (element) {
      if (next * ROW < element.scrollTop) element.scrollTop = next * ROW;
      if ((next + 1) * ROW > element.scrollTop + element.clientHeight) {
        element.scrollTop = (next + 1) * ROW - element.clientHeight;
      }
    }
    focusPending.current = true;
  };
  const toggle = (id: string): void => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };
  const selectAll = (): void => {
    void props.allIds().then((ids) => {
      setSelected(new Set(ids));
    });
  };
  const page = Math.floor(height / ROW);
  const moves = new Map<string, number>([
    ["Home", 0],
    ["End", total - 1],
    ["PageDown", active + page],
    ["PageUp", active - page],
  ]);
  const onKey = (event: KeyboardEvent<HTMLDivElement>): void => {
    const move = moves.get(event.key);
    if (move !== undefined) {
      event.preventDefault();
      moveTo(move);
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      moveTo(active + (event.key === "ArrowDown" ? 1 : -1), event.shiftKey);
    } else if (event.key === "a" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      selectAll();
    } else if (event.key === "Escape" && selected.size) {
      event.preventDefault();
      setSelected(new Set());
    }
  };

  const message = (
    <Message list={list} error={error} onClearFilters={props.onClearFilters} />
  );
  const blocked = Boolean(error) || !list.loaded || total === 0;
  const indexes = Array.from(
    { length: Math.max(0, last - first) },
    (_, k) => first + k,
  );
  return (
    <div
      role={showGrid ? "grid" : undefined}
      tabIndex={showGrid ? -1 : undefined}
      onKeyDown={showGrid ? onKey : undefined}
      aria-label={showGrid ? `${title}, ${String(total)} documents` : undefined}
      aria-rowcount={showGrid ? total + 1 : undefined}
      aria-multiselectable={showGrid ? "true" : undefined}
      style={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        minHeight: 0,
      }}
    >
      {showGrid && (
        <GridHead
          total={total}
          selected={selected}
          sort={sort}
          setSort={setSort}
          recents={recents}
          onAll={(all) => {
            if (all) selectAll();
            else setSelected(new Set());
          }}
        />
      )}
      {blocked ? (
        message
      ) : (
        <div
          className="grid-body"
          ref={bodyRef}
          onScroll={props.onScroll}
          role="rowgroup"
        >
          <div style={{ height: total * ROW, position: "relative" }}>
            {indexes.map((index) => {
              const row = list.at(index);
              return (
                <Row
                  key={row?.id ?? `pending-${String(index)}`}
                  row={row}
                  index={index}
                  isActive={index === active}
                  selected={row ? selected.has(row.id) : false}
                  rowRef={(element) => {
                    if (element) rowRefs.current.set(index, element);
                    else rowRefs.current.delete(index);
                  }}
                  onFocus={() => {
                    setActive(index);
                  }}
                  onToggle={(event) => {
                    setActive(index);
                    if (event.shiftKey) {
                      setSelected(new Set(rangeIds(anchor, index)));
                    } else if (row) {
                      setAnchor(index);
                      toggle(row.id);
                    }
                  }}
                  onClick={(event) => {
                    setActive(index);
                    if ((event.metaKey || event.ctrlKey) && row) {
                      setAnchor(index);
                      toggle(row.id);
                    } else if (event.shiftKey) {
                      setSelected(new Set(rangeIds(anchor, index)));
                    } else if (row) {
                      setAnchor(index);
                      setSelected(new Set([row.id]));
                    }
                  }}
                />
              );
            })}
          </div>
        </div>
      )}
      <GridFoot />
    </div>
  );
}
