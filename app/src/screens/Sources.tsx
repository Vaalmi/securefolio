import { toast } from "../toast";
import { useEffect, useRef, useState } from "react";
import type { ReactElement } from "react";
import {
  AlertTriangle,
  FolderMinus,
  FolderPlus,
  Lock,
  MoreHorizontal,
  Pause,
  Play,
  RefreshCw,
} from "lucide-react";

import {
  listSources,
  pauseSource,
  rescanSource,
  resumeSource,
  updateSource,
} from "../api";
import type { Source } from "../api";
import {
  AddDialog,
  OrganizeDialog,
  SensitivityDialog,
  StopDialog,
} from "../components/SourceDialogs";
import { chooseFolder } from "../desktop";
import { fmtMoment } from "../format";
import { go, useRoute } from "../router";
import { Page } from "../shell";
import { MenuButton, PrivateBadge, Switch } from "../ui";
import { useAction } from "../useAction";
import { getSettings } from "../api";
import { useLoad } from "../useLoad";

const POLL_MS = 1500;
const STATUS_LABEL: Record<string, string> = {
  watching: "Watching",
  scanning: "Scanning",
  paused: "Paused",
  needs_attention: "Needs attention",
};

type Dialog = {
  kind: "sensitivity" | "stop" | "organize";
  source: Source;
} | null;

function StatusCell({ source }: Readonly<{ source: Source }>): ReactElement {
  const label = STATUS_LABEL[source.status] ?? source.status;
  return (
    <td>
      <span className="hstack" style={{ gap: 4 }}>
        {source.status === "needs_attention" && (
          <AlertTriangle
            size={14}
            aria-hidden="true"
            style={{ color: "var(--warning)" }}
          />
        )}
        {source.status === "scanning" && (
          <RefreshCw size={14} aria-hidden="true" className="spin" />
        )}
        {source.status === "paused" && <Pause size={14} aria-hidden="true" />}
        {label}
      </span>
      {source.status_reason && (
        <button
          type="button"
          className="doc-link small"
          style={{ color: "var(--link)" }}
          onClick={() => {
            go("/sources?tray=skipped");
          }}
        >
          {source.status_reason}
        </button>
      )}
    </td>
  );
}

function SourceRow({
  source,
  reload,
  onDialog,
}: Readonly<{
  source: Source;
  reload: () => void;
  onDialog: (dialog: Dialog) => void;
}>): ReactElement {
  const act = useAction(async (work: () => Promise<object>) => {
    await work();
    reload();
  });
  const isPrivate = source.sensitivity === "private";
  const paused = source.status === "paused";
  return (
    <tr>
      <td>
        <div style={{ fontWeight: 500 }}>{source.name}</div>
        <div className="small muted mono">
          {source.builtin ? "SecureFolio library" : source.path}
        </div>
        {act.error && (
          <div role="alert" className="small">
            {act.error}
          </div>
        )}
      </td>
      <td>{isPrivate ? <PrivateBadge /> : <span>Standard</span>}</td>
      <td className="num col-items">{source.items.toLocaleString("en-US")}</td>
      <StatusCell source={source} />
      <td className="small col-scan">
        {source.status === "scanning"
          ? "Scanning now"
          : fmtMoment(source.last_scan_at)}
      </td>
      <td>
        {isPrivate ? (
          <span className="small muted">Not offered</span>
        ) : (
          <Switch
            hideLabel
            label={`Organize ${source.name} in the cloud`}
            checked={source.auto_organize}
            onChange={(value) => {
              if (value) onDialog({ kind: "organize", source });
              else {
                void act.run(() =>
                  updateSource(source.id, { auto_organize: false }),
                );
                toast(
                  `Cloud organizing is off for ${source.name}. New cards come from Local AI.`,
                );
              }
            }}
          />
        )}
      </td>
      <td>
        {isPrivate ? (
          <span className="small muted">Never</span>
        ) : (
          <Switch
            hideLabel
            label={`MCP access for ${source.name}`}
            checked={source.mcp_enabled}
            onChange={(value) => {
              void act.run(() =>
                updateSource(source.id, { mcp_enabled: value }),
              );
            }}
          />
        )}
      </td>
      <td>
        {source.builtin ? null : (
          <span className="hstack" style={{ gap: 4 }}>
            <button
              type="button"
              className="btn sm"
              aria-label={`${paused ? "Resume" : "Pause"} ${source.name}`}
              onClick={() => {
                void act.run(() =>
                  paused ? resumeSource(source.id) : pauseSource(source.id),
                );
              }}
            >
              {paused ? (
                <Play size={12} aria-hidden="true" />
              ) : (
                <Pause size={12} aria-hidden="true" />
              )}
              {paused ? "Resume" : "Pause"}
            </button>
            <button
              type="button"
              className="btn sm"
              aria-label={`Rescan ${source.name}`}
              onClick={() => {
                void act.run(() => rescanSource(source.id));
                toast(`Rescanning ${source.name}.`);
              }}
            >
              <RefreshCw size={12} aria-hidden="true" /> Rescan
            </button>
            <MenuButton
              label={`More actions for ${source.name}`}
              items={[
                {
                  label: "Change sensitivity…",
                  icon: Lock,
                  onSelect: () => {
                    onDialog({ kind: "sensitivity", source });
                  },
                },
                {
                  label: "Stop watching…",
                  icon: FolderMinus,
                  onSelect: () => {
                    onDialog({ kind: "stop", source });
                  },
                },
              ]}
            >
              <MoreHorizontal size={14} aria-hidden="true" />
            </MenuButton>
          </span>
        )}
      </td>
    </tr>
  );
}

export function Sources(): ReactElement {
  const defaults = useLoad(getSettings, "defaults");
  const route = useRoute();
  const sources = useLoad(listSources, "sources", {
    every: POLL_MS,
    while: (rows) => rows.some((s) => s.status === "scanning"),
  });
  const [dialog, setDialog] = useState<Dialog>(null);
  const [adding, setAdding] = useState<string | null>(null);
  const startAdding = (): void => {
    void chooseFolder().then((path) => {
      if (path) setAdding(path);
    });
  };
  const wantAdd = route.params.get("add") === "1" && Boolean(defaults.data);
  // From Home's "Add a folder": straight to the picker, once.
  const asked = useRef(false);
  useEffect(() => {
    if (wantAdd && !asked.current) {
      asked.current = true;
      void chooseFolder().then((path) => {
        if (path) setAdding(path);
      });
    }
  }, [wantAdd]);
  const closeDialog = (): void => {
    setDialog(null);
    setAdding(null);
    if (wantAdd) go("/sources");
    sources.reload();
  };
  return (
    <Page
      title="Sources"
      actions={
        <button
          type="button"
          className="btn primary"
          disabled={!defaults.data}
          onClick={startAdding}
        >
          <FolderPlus size={14} aria-hidden="true" /> Add folder
        </button>
      }
    >
      <p className="small" style={{ color: "var(--ink-2)", marginBottom: 12 }}>
        Watched folders are read where they are. SecureFolio rescans at start,
        every hour and after your Mac wakes. Files you drag in are copied into
        the library.
      </p>
      {sources.error && <p role="alert">{sources.error}</p>}
      <div className="card sources-table" style={{ padding: 0 }}>
        <div className="table-scroll">
          <table className="table">
            <caption className="sr-only">Watched folders</caption>
            <thead>
              <tr>
                <th scope="col">Folder</th>
                <th scope="col">Sensitivity</th>
                <th scope="col" className="num col-items">
                  Items
                </th>
                <th scope="col">Status</th>
                <th scope="col" className="col-scan">
                  Last scan
                </th>
                <th scope="col">Cloud organizing</th>
                <th scope="col">MCP</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {(sources.data ?? []).map((source) => (
                <SourceRow
                  key={source.id}
                  source={source}
                  reload={sources.reload}
                  onDialog={setDialog}
                />
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {dialog?.kind === "sensitivity" && (
        <SensitivityDialog source={dialog.source} onClose={closeDialog} />
      )}
      {dialog?.kind === "organize" && (
        <OrganizeDialog source={dialog.source} onClose={closeDialog} />
      )}
      {dialog?.kind === "stop" && (
        <StopDialog source={dialog.source} onClose={closeDialog} />
      )}
      {adding !== null && (
        <AddDialog
          level={defaults.data?.default_sensitivity ?? "private"}
          path={adding}
          onClose={closeDialog}
        />
      )}
    </Page>
  );
}
