import { isComposing, isOpenKey, isSendKey } from "../keys";
import { useEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent, ReactElement } from "react";
import { Search } from "lucide-react";

import { listCollections, search, warmSearch } from "../api";
import type { SearchResults } from "../api";
import { askLabel, canAsk, useAskRoute } from "../askRoute";
import { go } from "../router";
import { hitsOf, openHit } from "../searchHits";
import { useApp } from "../storeContext";
import { useLoad } from "../useLoad";
import { AskButton, RouteNote } from "./AskButton";
import { ModeSwitch, ScopeChip } from "./ScopeChip";
import type { ScopeOption } from "./ScopeChip";
import { Results } from "./SearchResults";

const TYPING_PAUSE_MS = 120;
const MERGE_AGAIN_MS = 600;

const LIBRARY_SCOPES: ScopeOption[] = [
  {
    id: "all",
    label: "All · Private excluded",
    desc: "Standard folders. Answers can use Cloud AI.",
  },
  {
    id: "all-private",
    label: "All · Private included",
    desc: "Local AI answers on this Mac.",
    private: true,
  },
];

interface Found {
  text: string;
  scopeId: string;
  results: SearchResults;
}

/** Results as you type. Lexical results come at once; once the model has loaded, the
 * next request merges in meaning, without anyone having to type again. Results from
 * another scope are never shown, so Private can't linger after it's left out; those
 * for earlier typing stay on screen, but Return won't open them. */
function useSearch(text: string, scope: ScopeOption) {
  const [found, setFound] = useState<Found>();
  const [failed, setFailed] = useState<{ text: string; error: string }>();
  useEffect(() => {
    if (!text.trim()) return;
    let current = true;
    let timer: ReturnType<typeof setTimeout>;
    const ask = (): void => {
      search({
        q: text,
        include_private: scope.private === true,
        ...(scope.collection ? { collection: scope.collection } : {}),
      })
        .then((results) => {
          if (!current) return;
          setFound({ text, scopeId: scope.id, results });
          setFailed(undefined);
          if (!results.semantic) timer = setTimeout(ask, MERGE_AGAIN_MS);
        })
        .catch((failure: unknown) => {
          if (current) {
            setFailed({
              text,
              error:
                failure instanceof Error ? failure.message : String(failure),
            });
          }
        });
    };
    timer = setTimeout(ask, TYPING_PAUSE_MS);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [text, scope]);
  const error = failed?.text === text ? failed.error : undefined;
  const shown =
    text.trim() && !error && found?.scopeId === scope.id ? found : undefined;
  return {
    results: shown?.results,
    fresh: shown?.text === text,
    error,
  };
}

/** Which key does what, beside the Ask button: after the arrows Return opens the
 * result moved to; before, Return asks (while answering isn't blocked) and
 * Command-Return opens the first result. */
function KeyHint({
  id,
  returnOpens,
  canAsk: asks,
  hasHits,
}: Readonly<{
  id: string;
  returnOpens: boolean;
  canAsk: boolean;
  hasHits: boolean;
}>): ReactElement {
  if (returnOpens) {
    return (
      <span id={id} className="small muted" aria-live="polite">
        <span className="kbd">↵</span> open this result · type to ask
      </span>
    );
  }
  return (
    <span id={id} className="small muted" aria-live="polite">
      {asks && (
        <>
          <span className="kbd">↵</span> ask
        </>
      )}
      {hasHits && (
        <>
          {asks && " · "}
          <span className="kbd">⌘↵</span> open first result
        </>
      )}
    </span>
  );
}

/** The one Search-or-Ask box. */
export function SearchAskBox({
  initial = "",
  focusOnOpen,
}: Readonly<{
  initial?: string;
  focusOnOpen?: boolean;
}>): ReactElement {
  const app = useApp();
  const [query, setQuery] = useState(initial);
  const [scope, setScope] = useState<ScopeOption>(
    () => LIBRARY_SCOPES[app.includePrivate ? 1 : 0],
  );
  // Return asks until the arrows move the selection; then it opens the result moved
  // to. Typing, or another scope, puts it back to asking.
  const [chosen, setActive] = useState(0);
  const [arrowed, setMoved] = useState(false);
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const openWhenFresh = useRef(false);
  const collections = useLoad(listCollections, "collections");
  const options: ScopeOption[] = [
    ...LIBRARY_SCOPES,
    ...(collections.data ?? [])
      .filter((c) => c.count > 0)
      .map((c): ScopeOption => ({
        id: c.id,
        label: `Collection: ${c.name}`,
        desc: `${String(c.count)} documents${c.has_private ? ". Holds Private documents, so the local model answers." : ""}`,
        private: c.has_private,
        collection: c.id,
      })),
  ];
  const { results, fresh, error } = useSearch(query, scope);
  const hits = hitsOf(results);
  // The selection as it stands: one past the results now there (another scope, say)
  // counts as none, so the keys and the hints always agree.
  const inRange = chosen < hits.length;
  const active = inRange ? chosen : 0;
  const moved = arrowed && inRange;
  const open = query.trim().length > 0;
  const includePrivate = scope.private === true;
  const route = useAskRoute({
    mode: app.mode,
    include_private: includePrivate,
    ...(scope.collection ? { collection: scope.collection } : {}),
  });
  useEffect(() => {
    if (focusOnOpen) inputRef.current?.focus();
  }, [focusOnOpen]);

  const ask = (): void => {
    if (!query.trim() || !canAsk(route.data)) return;
    const wanted = scope.collection ?? (includePrivate ? "all-private" : "all");
    go(`/ask/new?q=${encodeURIComponent(query.trim())}&scope=${wanted}`);
  };
  // The selected result, or once the results for what was typed have come.
  const openSelected = (): void => {
    const selected = hits.at(active);
    if (!fresh) openWhenFresh.current = true;
    else if (selected) openHit(selected);
  };
  const onKey = (event: KeyboardEvent<HTMLInputElement>): void => {
    // Keys that pick an input-method candidate never move, open or ask.
    if (isComposing(event)) return;
    if (isOpenKey(event)) {
      event.preventDefault();
      openSelected();
    } else if (event.key === "ArrowDown" && hits.length) {
      event.preventDefault();
      openWhenFresh.current = false; // another selection: its own open, if any
      setActive((a) => (moved ? (a + 1) % hits.length : 0));
      setMoved(true);
    } else if (event.key === "ArrowUp" && hits.length) {
      event.preventDefault();
      openWhenFresh.current = false;
      setActive((a) => (a - 1 + hits.length) % hits.length);
      setMoved(true);
    } else if (isSendKey(event)) {
      event.preventDefault();
      if (moved && hits.at(active)) openSelected();
      else ask();
    } else if (event.key === "Escape" && query) {
      event.preventDefault();
      openWhenFresh.current = false; // the open asked for goes with the query
      setQuery("");
      setMoved(false);
    }
  };
  const selected = hits.at(active);
  const returnOpens = moved && selected !== undefined;
  // Command-Return, or Return after the arrows, before the results for what was typed have come: open once they do.
  useEffect(() => {
    if (!fresh || !openWhenFresh.current) return;
    openWhenFresh.current = false;
    if (selected) openHit(selected);
  }, [fresh, selected]);
  return (
    <div className="composer-wrap">
      <div className="composer">
        <div className="hstack" style={{ padding: "10px 14px 2px" }}>
          <Search size={18} aria-hidden="true" className="muted" />
          <label htmlFor={`${listId}-input`} className="sr-only">
            Search or ask your library
          </label>
          <input
            ref={inputRef}
            id={`${listId}-input`}
            role="combobox"
            aria-expanded={open}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={
              open && moved && selected ? `${listId}-${selected.id}` : undefined
            }
            aria-describedby={`${listId}-hint`}
            value={query}
            onChange={(event) => {
              openWhenFresh.current = false;
              setQuery(event.target.value);
              setActive(0);
              setMoved(false);
            }}
            onKeyDown={onKey}
            onFocus={() => {
              void warmSearch().catch(() => undefined);
            }}
            placeholder="Search or ask your library"
            style={{
              flex: 1,
              border: 0,
              outline: "none",
              background: "none",
              fontSize: 16,
              height: 36,
            }}
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        <div className="composer-bar">
          <ScopeChip
            placement="down"
            value={scope}
            options={options}
            onChange={(o) => {
              // An open asked for in the last scope isn't one in this scope.
              openWhenFresh.current = false;
              setScope(o);
              setActive(0);
              setMoved(false);
              app.setIncludePrivate(o.private === true);
            }}
          />
          <ModeSwitch
            value={includePrivate ? "private" : app.mode}
            onChange={app.setMode}
            lockedPrivate={includePrivate}
          />
          <RouteNote route={route.data} error={route.error} />
          <span className="spacer" />
          <KeyHint
            id={`${listId}-hint`}
            returnOpens={returnOpens}
            canAsk={canAsk(route.data)}
            hasHits={hits.length > 0}
          />
          <AskButton
            route={route.data}
            mode={app.mode}
            disabled={!query.trim()}
            onClick={ask}
            returnOpens={returnOpens}
          />
        </div>
      </div>
      {open && (
        <Results
          fresh={fresh}
          listId={listId}
          results={results}
          hits={hits}
          active={active}
          moved={moved}
          asks={
            canAsk(route.data)
              ? askLabel(route.data, app.mode).replace("Ask", "ask")
              : null
          }
          query={query}
          scope={scope}
          error={error}
        />
      )}
    </div>
  );
}
