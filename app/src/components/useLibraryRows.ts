import { useCallback, useEffect, useRef, useState } from "react";

import { listLibrary, listLibraryIds } from "../api";
import type { DocumentRow, LibraryParams } from "../api";

export type SortKey = "title" | "type" | "date" | "size";
export const BLOCK = 200;

export interface LibraryRows {
  total: number;
  loaded: boolean;
  error: string | undefined;
  at: (index: number) => DocumentRow | undefined;
  byId: (id: string) => DocumentRow | undefined;
  need: (first: number, last: number) => void;
  reload: () => void;
  allIds: () => Promise<string[]>;
}

// Rows arrive a window at a time, so 20,000 of them cost what's on screen.
export function useLibraryRows(
  filters: LibraryParams,
  sort: { key: SortKey; descending: boolean },
): LibraryRows {
  const signature = JSON.stringify({ filters, sort });
  const query = useRef({ filters, sort });
  const blocks = useRef(new Map<number, DocumentRow[]>());
  const asked = useRef(new Set<number>());
  const [total, setTotal] = useState(0);
  const [loadedFor, setLoadedFor] = useState<string>();
  const [error, setError] = useState<string>();
  const [, setVersion] = useState(0);
  const [round, setRound] = useState(0);

  const fetchBlock = useCallback((block: number, stamp: string): void => {
    if (asked.current.has(block)) return;
    asked.current.add(block);
    const { filters: wanted, sort: order } = query.current;
    listLibrary({
      ...wanted,
      sort: order.key,
      descending: order.descending,
      offset: block * BLOCK,
      limit: BLOCK,
    })
      .then((window) => {
        if (stamp !== JSON.stringify(query.current)) return;
        blocks.current.set(block, window.rows);
        setTotal(window.total);
        setLoadedFor(stamp);
        setError(undefined);
        setVersion((v) => v + 1);
      })
      .catch((failure: unknown) => {
        asked.current.delete(block);
        setError(failure instanceof Error ? failure.message : String(failure));
      });
  }, []);

  useEffect(() => {
    query.current = { filters, sort };
    blocks.current = new Map();
    asked.current = new Set();
    fetchBlock(0, JSON.stringify(query.current));
    // The signature stands for the filters and sort.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature, round, fetchBlock]);

  return {
    total,
    loaded: loadedFor === JSON.stringify({ filters, sort }),
    error,
    at: (index) =>
      blocks.current.get(Math.floor(index / BLOCK))?.[index % BLOCK],
    byId: (id) => {
      for (const rows of blocks.current.values()) {
        const found = rows.find((row) => row.id === id);
        if (found) return found;
      }
      return undefined;
    },
    need: (first, last) => {
      const stamp = JSON.stringify(query.current);
      const lastBlock = Math.floor(Math.max(first, last - 1) / BLOCK);
      for (let block = Math.floor(first / BLOCK); block <= lastBlock; block++) {
        if (!blocks.current.has(block)) {
          queueMicrotask(() => {
            fetchBlock(block, stamp);
          });
        }
      }
    },
    reload: () => {
      setRound((r) => r + 1);
    },
    allIds: () =>
      listLibraryIds({
        ...query.current.filters,
        sort: query.current.sort.key,
        descending: query.current.sort.descending,
      }),
  };
}
