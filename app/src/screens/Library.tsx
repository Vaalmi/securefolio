import { useEffect, useRef, useState } from "react";
import type { ReactElement, UIEvent } from "react";
import { MessageSquare, X } from "lucide-react";

import {
  createThread,
  getLibraryFacets,
  listCollections,
  listLibraryIds,
} from "../api";
import type { Facets, LibraryParams } from "../api";
import { Grid, ROW } from "../components/LibraryGrid";
import {
  Inspector,
  RemoveModal,
  SelectionBar,
} from "../components/LibraryPanels";
import { Facet } from "../components/LibraryFacet";
import { useLibraryRows } from "../components/useLibraryRows";
import type { SortKey } from "../components/useLibraryRows";
import { go, useRoute, withParam } from "../router";
import type { Route } from "../router";
import { Page } from "../shell";
import { useApp } from "../storeContext";
import { useAction } from "../useAction";
import { useLoad } from "../useLoad";

const THIS_YEAR = new Date().getFullYear();
const DATE_RANGES: Record<string, { date_from?: string; date_to?: string }> = {
  "This year": { date_from: `${String(THIS_YEAR)}-01-01` },
  "Last year": {
    date_from: `${String(THIS_YEAR - 1)}-01-01`,
    date_to: `${String(THIS_YEAR - 1)}-12-31`,
  },
  Older: { date_to: `${String(THIS_YEAR - 2)}-12-31` },
};

function paramsOf(
  params: URLSearchParams,
  collectionId: string | undefined,
  name: string,
): LibraryParams {
  const found: LibraryParams = { ...DATE_RANGES[params.get("date") ?? ""] };
  const collection = collectionId ?? params.get("collection");
  const view = params.get("view");
  const docType = params.get("type");
  const source = params.get("folder");
  const tag = params.get("tag");
  if (view) found.view = view;
  if (docType) found.doc_type = docType;
  if (source) found.source = source;
  if (tag) found.tag = tag;
  if (collection) found.collection = collection;
  if (name.trim()) found.name = name.trim();
  return found;
}

function describeFilters(params: URLSearchParams, name: string): string {
  const parts = ["type", "date", "tag"].flatMap((key) => params.get(key) ?? []);
  if (name.trim()) parts.push(name.trim());
  return parts.join(", ") || "filtered list";
}

function titleOf(
  params: URLSearchParams,
  collectionName: string | undefined,
): string {
  if (collectionName) return collectionName;
  const view = params.get("view");
  if (view === "recents") return "Recents";
  if (view === "starred") return "Starred";
  return params.get("type") ?? "Library";
}

function LibraryActions({
  route,
  collectionId,
}: Readonly<{ route: Route; collectionId: string | undefined }>): ReactElement {
  return (
    <>
      {collectionId && (
        <button
          type="button"
          className="btn primary"
          onClick={() => {
            go(`/ask/new?scope=${collectionId}`);
          }}
        >
          <MessageSquare size={14} aria-hidden="true" /> Ask this collection
        </button>
      )}
      <button
        type="button"
        className="btn"
        onClick={() => {
          go(withParam(route, "import", "1"));
        }}
        aria-keyshortcuts="Meta+O"
      >
        Import files
      </button>
    </>
  );
}

function choices(
  options: { value: string; label?: string }[] | undefined,
): { value: string; label: string }[] {
  return (options ?? []).map((o) => ({
    value: o.value,
    label: o.label ?? o.value,
  }));
}

function AskFiltered({
  filters,
  total,
  params,
  name,
}: Readonly<{
  filters: LibraryParams;
  total: number;
  params: URLSearchParams;
  name: string;
}>): ReactElement | null {
  const app = useApp();
  const ask = useAction(async () => {
    const ids = await listLibraryIds(filters);
    const label = `Search: ${describeFilters(params, name)}`;
    const thread = await createThread({ kind: "search", ids, label }, app.mode);
    go(`/ask/${thread.id}`);
  });
  const filtered = ["type", "folder", "date", "tag"].some((key) =>
    params.has(key),
  );
  if ((!filtered && !name.trim()) || total === 0) return null;
  return (
    <div className="selbar" role="toolbar" aria-label="Filtered documents">
      <button
        type="button"
        className="btn sm"
        onClick={() => {
          void ask.run();
        }}
      >
        <MessageSquare size={12} aria-hidden="true" /> Ask about these{" "}
        {total.toLocaleString("en-US")}
      </button>
    </div>
  );
}

function LibraryFilters({
  params,
  route,
  facets,
  collectionId,
  name,
  setName,
  onClear,
}: Readonly<{
  params: URLSearchParams;
  route: Route;
  facets: Facets | undefined;
  collectionId: string | undefined;
  name: string;
  setName: (name: string) => void;
  onClear: () => void;
}>): ReactElement {
  const pick = (key: string) => (value: string | null) => {
    go(withParam(route, key, value));
  };
  const filtered = ["type", "folder", "date", "tag", "collection"].some((key) =>
    params.has(key),
  );
  return (
    <div className="facets" role="group" aria-label="Filters">
      <label htmlFor="name-filter" className="sr-only">
        Filter by name
      </label>
      <input
        id="name-filter"
        className="input filter-input"
        placeholder="Filter by name"
        value={name}
        onChange={(event) => {
          setName(event.target.value);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") setName("");
        }}
      />
      <Facet
        label="Type"
        value={params.get("type")}
        options={choices(facets?.types)}
        onPick={pick("type")}
      />
      <Facet
        label="Date"
        value={params.get("date")}
        options={Object.keys(DATE_RANGES).map((d) => ({ value: d, label: d }))}
        onPick={pick("date")}
      />
      <Facet
        label="Folder"
        value={params.get("folder")}
        options={choices(facets?.folders)}
        onPick={pick("folder")}
      />
      {!collectionId && (
        <Facet
          label="Collection"
          value={params.get("collection")}
          options={choices(facets?.collections)}
          onPick={pick("collection")}
        />
      )}
      <Facet
        label="Tag"
        value={params.get("tag")}
        options={choices(facets?.tags)}
        onPick={pick("tag")}
      />
      {(filtered || name) && (
        <button type="button" className="btn sm ghost" onClick={onClear}>
          <X size={12} aria-hidden="true" /> Clear filters
        </button>
      )}
    </div>
  );
}

function targetsOf(
  selected: Set<string>,
  active: string | undefined,
): string[] {
  if (selected.size > 0) return [...selected];
  return active ? [active] : [];
}

function useCollectionTitle(
  params: URLSearchParams,
  collectionId: string | undefined,
): string {
  const collections = useLoad(listCollections, "collections");
  const collection = collections.data?.find((c) => c.id === collectionId);
  return titleOf(params, collection?.name);
}

export function Library({
  collectionId,
}: Readonly<{
  collectionId?: string;
}>): ReactElement {
  const route = useRoute();
  const [name, setName] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; descending: boolean }>({
    key: "date",
    descending: true,
  });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [active, setActive] = useState(0);
  const [scrollTop, setScrollTop] = useState(0);
  const [height, setHeight] = useState(600);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  const params = route.params;
  const filters = paramsOf(params, collectionId, name);
  const list = useLibraryRows(filters, sort);
  // A new query starts at the top: its grid mounts scrolled to zero.
  const querySignature = JSON.stringify({ filters, sort });
  const [shownQuery, setShownQuery] = useState(querySignature);
  if (querySignature !== shownQuery) {
    setShownQuery(querySignature);
    setScrollTop(0);
    setActive(0);
  }
  const facets = useLoad(
    () => getLibraryFacets(filters),
    JSON.stringify(filters),
  );
  const title = useCollectionTitle(params, collectionId);

  const hasRows = list.total > 0;
  useEffect(() => {
    const element = bodyRef.current;
    if (!element) return;
    const observer = new ResizeObserver(() => {
      setHeight(element.clientHeight);
    });
    observer.observe(element);
    return () => {
      observer.disconnect();
    };
  }, [hasRows]);

  const first = Math.max(0, Math.floor(scrollTop / ROW) - 8);
  const last = Math.min(list.total, Math.ceil((scrollTop + height) / ROW) + 8);
  list.need(first, last);

  const onlyId = selected.size === 1 ? [...selected].at(0) : undefined;
  const inspected = onlyId ?? list.at(active)?.id ?? null;
  const refresh = (): void => {
    setSelected(new Set());
    list.reload();
    facets.reload();
  };
  const clearFilters = (): void => {
    setName("");
    go(collectionId ? `/collections/${collectionId}` : "/library");
  };
  const activeRow = list.at(active);
  const removalTargets = targetsOf(selected, activeRow?.id);
  return (
    <Page
      title={title}
      titleExtra={
        <span className="small muted mono">
          {list.loaded ? `${list.total.toLocaleString("en-US")} documents` : ""}
        </span>
      }
      right={
        list.total === 0 ? (
          <div className="empty" style={{ flex: 1 }}>
            <p className="muted">No document selected.</p>
          </div>
        ) : (
          <Inspector
            id={inspected}
            key={inspected ?? ""}
            onChanged={list.reload}
          />
        )
      }
      rightLabel="Inspector"
      actions={<LibraryActions route={route} collectionId={collectionId} />}
    >
      <div className="lib">
        <LibraryFilters
          params={params}
          route={route}
          facets={facets.data}
          collectionId={collectionId}
          name={name}
          setName={setName}
          onClear={clearFilters}
        />
        <div className="grid-wrap">
          {selected.size === 0 && (
            <AskFiltered
              filters={filters}
              total={list.total}
              params={params}
              name={name}
            />
          )}
          {selected.size > 0 && (
            <SelectionBar
              selected={selected}
              collectionId={collectionId}
              includesPrivate={[...selected].some(
                (id) => list.byId(id)?.sensitivity === "private",
              )}
              onChanged={refresh}
              onRemove={() => {
                setConfirmRemove(true);
              }}
              onClear={() => {
                setSelected(new Set());
              }}
            />
          )}
          <Grid
            title={title}
            list={list}
            error={list.error}
            sort={sort}
            setSort={setSort}
            selected={selected}
            setSelected={setSelected}
            active={active}
            setActive={setActive}
            first={first}
            last={last}
            height={height}
            bodyRef={bodyRef}
            allIds={list.allIds}
            onScroll={(event: UIEvent<HTMLDivElement>) => {
              setScrollTop(event.currentTarget.scrollTop);
            }}
            onClearFilters={clearFilters}
          />
        </div>
      </div>
      {confirmRemove && (
        <RemoveModal
          ids={removalTargets}
          onClose={() => {
            setConfirmRemove(false);
          }}
          onDone={refresh}
        />
      )}
    </Page>
  );
}
