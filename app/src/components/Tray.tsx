import { toast } from "../toast";
import { useEffect, useRef, useState } from "react";
import type { ReactElement } from "react";
import { CloudDownload, KeyRound, Minus, RotateCcw, X } from "lucide-react";

import {
  cancelJob,
  downloadDocument,
  listJobs,
  retryJob,
  unlockDocument,
} from "../api";
import type { TrayCounts, TrayRow as Row } from "../api";
import { go, useRoute, withParam } from "../router";
import { ImportBadge, Tabs } from "../ui";
import { useAction } from "../useAction";
import { useJobCounts } from "../useEvents";
import { useLoad } from "../useLoad";

const PAGE = 100;

type TabId = "all" | "active" | "completed" | "failed" | "skipped";

function tabCount(counts: TrayCounts | undefined, tab: TabId): number {
  if (!counts) return 0;
  if (tab === "all") {
    return counts.active + counts.completed + counts.failed + counts.skipped;
  }
  return counts[tab];
}

function UnlockForm({
  row,
  onDone,
}: Readonly<{
  row: Row;
  onDone: () => void;
}>): ReactElement {
  const [password, setPassword] = useState("");
  const unlock = useAction(
    () => unlockDocument(row.document_id, password),
    onDone,
  );
  return (
    <form
      className="why stack"
      style={{
        gap: 8,
        background: "var(--page)",
        padding: 12,
        borderRadius: 8,
      }}
      onSubmit={(event) => {
        event.preventDefault();
        void unlock.run();
      }}
    >
      <label
        htmlFor={`pw-${row.id}`}
        className="small"
        style={{ fontWeight: 500 }}
      >
        Password for {row.file}
      </label>
      <input
        id={`pw-${row.id}`}
        type="password"
        className="input"
        autoComplete="off"
        value={password}
        onChange={(event) => {
          setPassword(event.target.value);
        }}
      />
      <span className="help">
        The password is used once to read this file and isn't saved. The file
        itself is never changed.
      </span>
      {unlock.error && (
        <span role="alert" className="small">
          {unlock.error}
        </span>
      )}
      <span className="hstack">
        <button type="submit" className="btn primary sm" disabled={!password}>
          Unlock & Index
        </button>
        <button type="button" className="btn sm" onClick={onDone}>
          Cancel
        </button>
      </span>
    </form>
  );
}

function RowActions({
  row,
  reload,
  unlocking,
  onUnlock,
}: Readonly<{
  row: Row;
  reload: () => void;
  unlocking: boolean;
  onUnlock: () => void;
}>): ReactElement {
  const act = useAction(async (work: () => Promise<object>, said?: string) => {
    await work();
    if (said) toast(said);
    reload();
  });
  const jobId = row.job_id;
  return (
    <span className="acts">
      {act.error && (
        <span role="alert" className="small">
          {act.error}
        </span>
      )}
      <ImportBadge status={row.status} />
      {(row.status === "Queued" || row.status === "Indexing") && jobId && (
        <button
          type="button"
          className="btn sm"
          aria-label={`Cancel ${row.file}`}
          onClick={() => {
            void act.run(
              () => cancelJob(jobId),
              `Canceled ${row.file}. Nothing was left half-indexed.`,
            );
          }}
        >
          Cancel
        </button>
      )}
      {row.status === "Failed" && jobId && (
        <button
          type="button"
          className="btn sm"
          aria-label={`Retry ${row.file}`}
          onClick={() => {
            void act.run(() => retryJob(jobId));
          }}
        >
          <RotateCcw size={12} aria-hidden="true" /> Retry
        </button>
      )}
      {row.skip_kind === "password" && (
        <button
          type="button"
          className="btn sm"
          aria-label={`Unlock & Index ${row.file}`}
          aria-expanded={unlocking}
          onClick={onUnlock}
        >
          <KeyRound size={12} aria-hidden="true" /> Unlock & Index
        </button>
      )}
      {row.skip_kind === "cloud" && (
        <button
          type="button"
          className="btn sm"
          aria-label={`Download & Index ${row.file}`}
          onClick={() => {
            void act.run(() => downloadDocument(row.document_id));
          }}
        >
          <CloudDownload size={12} aria-hidden="true" /> Download & Index
        </button>
      )}
    </span>
  );
}

/** The import tray: a non-modal panel at the bottom right. */
export function Tray(): ReactElement {
  const route = useRoute();
  const tab = (route.params.get("tray") ?? "all") as TabId;
  const [unlocking, setUnlocking] = useState<string | null>(
    route.params.get("unlock"),
  );
  const counts = useJobCounts();
  const [limit, setLimit] = useState(PAGE);
  const rows = useLoad(
    () => listJobs(tab, limit),
    `${tab}-${String(limit)}-${JSON.stringify(counts ?? {})}`,
  );
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    ref.current
      ?.querySelector<HTMLElement>("[role=tab][aria-selected=true]")
      ?.focus();
  }, []);
  const close = (): void => {
    go(withParam(route, "tray", null));
    window.setTimeout(() => {
      document.querySelector<HTMLElement>(".sb-meter")?.focus();
    }, 0);
  };
  const closing = useRef(close);
  useEffect(() => {
    closing.current = close;
  });
  useEffect(() => {
    const panel = ref.current;
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") closing.current();
    };
    panel?.addEventListener("keydown", onKey);
    return () => {
      panel?.removeEventListener("keydown", onKey);
    };
  }, []);
  const line =
    counts && counts.total > 0
      ? `Indexing ${counts.done.toLocaleString("en-US")} of ${counts.total.toLocaleString("en-US")}`
      : "";
  const shown = rows.data ?? [];
  return (
    <section ref={ref} className="tray" aria-label="Import tray">
      <div className="hstack" style={{ padding: "12px 12px 4px 16px" }}>
        <h2 style={{ flex: 1 }}>Imports</h2>
        <span className="small muted" role="status">
          {line}
        </span>
        <button
          type="button"
          className="icon-btn"
          aria-label="Minimize import tray"
          onClick={close}
        >
          <Minus size={16} aria-hidden="true" />
        </button>
        <button
          type="button"
          className="icon-btn"
          aria-label="Close import tray"
          onClick={close}
        >
          <X size={16} aria-hidden="true" />
        </button>
      </div>
      <Tabs
        idBase="tray"
        label="Import status"
        value={tab}
        onChange={(next) => {
          go(withParam(route, "tray", next));
        }}
        tabs={(
          ["all", "active", "completed", "failed", "skipped"] as const
        ).map((id) => ({
          id,
          label: id.charAt(0).toUpperCase() + id.slice(1),
          count: tabCount(counts, id),
        }))}
      />
      <div
        className="tray-body"
        role="tabpanel"
        id="tray-panel"
        aria-labelledby={`tray-tab-${tab}`}
      >
        {rows.error && (
          <p role="alert" className="small">
            {rows.error}
          </p>
        )}
        {shown.length === 0 && !rows.error && (
          <p className="empty small">Nothing here.</p>
        )}
        {shown.map((row) => (
          <div key={row.id} className="tray-row">
            <span className="file">
              {row.status === "Completed" ? (
                <a
                  href={`#/doc/${row.document_id}`}
                  style={{
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    color: "var(--ink)",
                  }}
                >
                  {row.file}
                </a>
              ) : (
                <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
                  {row.file}
                </span>
              )}
              <span className="small muted" style={{ fontWeight: 400 }}>
                {row.folder}
              </span>
            </span>
            <RowActions
              row={row}
              reload={rows.reload}
              unlocking={unlocking === row.document_id}
              onUnlock={() => {
                setUnlocking(row.document_id);
              }}
            />
            <span className="why">{row.detail}</span>
            {unlocking === row.document_id && (
              <UnlockForm
                row={row}
                onDone={() => {
                  setUnlocking(null);
                  rows.reload();
                }}
              />
            )}
          </div>
        ))}
        {shown.length < tabCount(counts, tab) && (
          <button
            type="button"
            className="btn sm ghost"
            onClick={() => {
              setLimit((n) => n + PAGE);
            }}
          >
            Show more
          </button>
        )}
      </div>
    </section>
  );
}
