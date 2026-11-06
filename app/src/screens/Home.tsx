import type { ReactElement } from "react";
import {
  AlertTriangle,
  ArrowRight,
  FolderPlus,
  Loader2,
  RotateCcw,
  Star,
  Upload,
  XCircle,
} from "lucide-react";

import {
  downloadDocument,
  getHealth,
  getLibraryCounts,
  listJobs,
  listLibrary,
  restartWorker,
  retryJob,
} from "../api";
import type { DocumentRow, TrayRow } from "../api";
import { SearchAskBox } from "../components/Composer";
import { fmtDate } from "../format";
import { go, useRoute, withParam } from "../router";
import { Page } from "../shell";
import { ImportBadge, KindIcon, Skeleton, StateBadge } from "../ui";
import { useAction } from "../useAction";
import { useJobCounts } from "../useEvents";
import { useLoad } from "../useLoad";

const RECENTS = 6;
const ATTENTION_ROWS = 4;

function actionLabel(row: TrayRow): string {
  if (row.status === "Failed") return "Retry";
  if (row.skip_kind === "password") return "Unlock & Index";
  return row.skip_kind === "cloud" ? "Download & Index" : "Details";
}

function Attention({
  rows,
  reload,
}: Readonly<{
  rows: TrayRow[];
  reload: () => void;
}>): ReactElement {
  const route = useRoute();
  const act = useAction(async (row: TrayRow) => {
    if (row.status === "Failed" && row.job_id) await retryJob(row.job_id);
    else if (row.skip_kind === "cloud") await downloadDocument(row.document_id);
    reload();
  });
  const failed = rows.filter((r) => r.status === "Failed").length;
  return (
    <>
      <div className="section-title">
        <AlertTriangle size={16} aria-hidden="true" />
        <h2>Needs attention</h2>
        <span className="small muted">
          {failed} failed · {rows.length - failed} skipped
        </span>
        <span className="spacer" />
        <button
          type="button"
          className="btn sm ghost"
          onClick={() => {
            go(withParam(route, "tray", "all"));
          }}
        >
          See all {rows.length} in imports{" "}
          <ArrowRight size={12} aria-hidden="true" />
        </button>
      </div>
      <div className="card" style={{ padding: 8 }}>
        {rows.slice(0, ATTENTION_ROWS).map((row) => {
          const label = actionLabel(row);
          return (
            <div key={row.id} className="list-row">
              <ImportBadge status={row.status} />
              <span style={{ minWidth: 0, flex: 1 }}>
                <span className="title">{row.file}</span>
                <span
                  className="small"
                  style={{ display: "block", color: "var(--ink-2)" }}
                >
                  {row.detail}
                </span>
              </span>
              <button
                type="button"
                className="btn sm"
                aria-label={`${label} ${row.file}`}
                onClick={() => {
                  if (row.skip_kind === "password")
                    go(
                      `${withParam(route, "tray", "skipped")}&unlock=${row.document_id}`,
                    );
                  else if (label === "Details")
                    go(withParam(route, "tray", "skipped"));
                  else void act.run(row);
                }}
              >
                {label}
              </button>
            </div>
          );
        })}
      </div>
    </>
  );
}

function DocList({
  rows,
  starred,
}: Readonly<{
  rows: DocumentRow[];
  starred?: boolean;
}>): ReactElement {
  return (
    <div className="card" style={{ padding: 8 }}>
      {rows.length === 0 && (
        <p className="small muted" style={{ padding: 8 }}>
          Nothing yet.
        </p>
      )}
      {rows.map((row) => (
        <a
          key={row.id}
          href={`#/doc/${row.id}`}
          className="list-row"
          style={{ textDecoration: "none", color: "inherit" }}
        >
          {starred ? (
            <Star
              size={16}
              aria-label="Starred"
              fill="currentColor"
              style={{ color: "var(--warning)" }}
            />
          ) : (
            <KindIcon kind={row.file_kind} />
          )}
          <span style={{ minWidth: 0, flex: 1 }}>
            <span
              className="title"
              style={{
                display: "block",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {row.title}
            </span>
            <span className="small muted">
              {row.doc_type ?? row.file_kind} · {fmtDate(row.doc_date)}
            </span>
          </span>
          <StateBadge state={row.status} />
        </a>
      ))}
    </div>
  );
}

function Empty(): ReactElement {
  return (
    <Page title="Search & Ask">
      <div className="empty" style={{ marginTop: "10vh" }}>
        <span className="empty-icon">
          <FolderPlus size={22} aria-hidden="true" />
        </span>
        <h2>Your library is empty</h2>
        <p className="muted" style={{ maxWidth: 420 }}>
          Add a folder to watch, or drop files here. SecureFolio reads them
          where they are and never changes them.
        </p>
        <div className="hstack">
          <button
            type="button"
            className="btn primary"
            onClick={() => {
              go("/sources?add=1");
            }}
          >
            <FolderPlus size={14} aria-hidden="true" /> Add a folder
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => {
              go("/library?import=1");
            }}
          >
            <Upload size={14} aria-hidden="true" /> Import files
          </button>
        </div>
      </div>
    </Page>
  );
}

function ServiceStopped({
  onRestart,
}: Readonly<{
  onRestart: () => void;
}>): ReactElement {
  return (
    <Page title="Search & Ask">
      <div className="composer-wrap" style={{ marginTop: 24 }}>
        <div className="banner err" role="alert">
          <span className="banner-icon">
            <XCircle size={16} aria-hidden="true" />
          </span>
          <div className="stack" style={{ gap: 6 }}>
            <strong>SecureFolio's library service stopped</strong>
            <p className="small">
              Search and answers are paused. Your files and notes are safe.
              Restart the service to carry on.
            </p>
            <div className="hstack">
              <button
                type="button"
                className="btn primary sm"
                onClick={onRestart}
              >
                <RotateCcw size={12} aria-hidden="true" /> Restart service
              </button>
            </div>
          </div>
        </div>
      </div>
    </Page>
  );
}

function IndexingLine(): ReactElement | null {
  const route = useRoute();
  const counts = useJobCounts();
  if (!counts || counts.total === 0) return null;
  return (
    <p
      className="small muted hstack"
      style={{ marginTop: 10, gap: 6, justifyContent: "center" }}
      role="status"
    >
      <Loader2 size={12} aria-hidden="true" className="spin" />
      <span>
        Indexing{" "}
        <span className="mono">{counts.done.toLocaleString("en-US")}</span> of{" "}
        <span className="mono">{counts.total.toLocaleString("en-US")}</span>.
        Search already works on the files read so far.{" "}
        <button
          type="button"
          className="doc-link small"
          style={{ color: "var(--link)", fontWeight: 500 }}
          onClick={() => {
            go(withParam(route, "tray", "active"));
          }}
        >
          Show imports
        </button>
      </span>
    </p>
  );
}

function HomeLists({
  starred,
  recents,
  ready,
}: Readonly<{
  starred: DocumentRow[];
  recents: DocumentRow[];
  ready: boolean;
}>): ReactElement {
  return (
    <div className="grid2" style={{ marginTop: 8 }}>
      <section aria-labelledby="h-recents">
        <div className="section-title">
          <h2 id="h-recents">Recents</h2>
          <span className="spacer" />
          <a className="small" href="#/library?view=recents">
            See all
          </a>
        </div>
        {ready ? (
          <DocList rows={recents} />
        ) : (
          <div
            className="card"
            style={{ padding: 8 }}
            role="status"
            aria-busy="true"
            aria-label="Loading documents"
          >
            {Array.from({ length: 5 }, (_, i) => (
              <div key={i} className="list-row">
                <Skeleton w={28} h={28} />
                <span style={{ flex: 1 }} className="stack">
                  <Skeleton w="70%" />
                  <Skeleton w="40%" h={10} />
                </span>
              </div>
            ))}
          </div>
        )}
      </section>
      <section aria-labelledby="h-starred">
        <div className="section-title">
          <h2 id="h-starred">Starred</h2>
          <span className="spacer" />
          <a className="small" href="#/library?view=starred">
            See all
          </a>
        </div>
        <DocList rows={starred} starred />
      </section>
    </div>
  );
}

function useHome() {
  const recents = useLoad(
    () =>
      listLibrary({ view: "recents", limit: RECENTS, include_private: false }),
    "recents",
  );
  const newest = useLoad(
    () => listLibrary({ sort: "date", limit: RECENTS, include_private: false }),
    "newest",
  );
  const starred = useLoad(
    () => listLibrary({ view: "starred", limit: RECENTS }),
    "starred",
  );
  // Every document, Private ones too: a library of only Private folders isn't empty.
  const counts = useLoad(getLibraryCounts, "counts");
  const failed = useLoad(() => listJobs("failed"), "failed");
  const skipped = useLoad(() => listJobs("skipped"), "skipped");
  const opened = recents.data?.rows ?? [];
  return {
    empty: counts.data?.total === 0,
    ready: newest.data !== undefined,
    recents: opened.length > 0 ? opened : (newest.data?.rows ?? []),
    starred: starred.data?.rows ?? [],
    attention: [...(failed.data ?? []), ...(skipped.data ?? [])],
    reloadAttention: () => {
      failed.reload();
      skipped.reload();
    },
  };
}

export function Home(): ReactElement {
  const route = useRoute();
  const query = route.params.get("q") ?? "";
  const health = useLoad(getHealth, "health");
  const restart = useAction(restartWorker, health.reload);
  const home = useHome();
  if (health.data?.worker === "stopped") {
    return (
      <ServiceStopped
        onRestart={() => {
          void restart.run();
        }}
      />
    );
  }
  if (home.empty) return <Empty />;
  return (
    <Page title="Search & Ask">
      <div style={{ maxWidth: 760, margin: "8px auto 0" }}>
        <SearchAskBox key={query} initial={query} focusOnOpen />
        <IndexingLine />
      </div>
      <div style={{ maxWidth: 1040, margin: "0 auto" }}>
        {home.attention.length > 0 && (
          <Attention rows={home.attention} reload={home.reloadAttention} />
        )}
        <HomeLists
          starred={home.starred}
          recents={home.recents}
          ready={home.ready}
        />
      </div>
    </Page>
  );
}
