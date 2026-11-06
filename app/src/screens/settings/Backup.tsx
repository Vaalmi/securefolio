import { useState } from "react";
import type { ReactElement } from "react";
import { HardDriveDownload, Upload } from "lucide-react";

import {
  chooseFile,
  chooseFolder,
  isOpenAtLogin,
  openAtLogin,
  restartBackend,
  showInFinder,
} from "../../desktop";
import { fmtMoment, fmtSize } from "../../format";
import {
  backUpNow,
  updateSettings,
  listBackups,
  getLogFolder,
  getSettings,
  getV1Import,
  importV1,
  restoreBackup,
} from "../../api";
import type { BackupRow, Backups } from "../../api";
import { toast } from "../../toast";
import { Modal, Switch } from "../../ui";
import { useAction } from "../../useAction";
import { useLoad } from "../../useLoad";
import { Row } from "./Row";

function lastLine(data: Backups | undefined): string {
  const last = data?.last;
  if (!last) return "No backup yet.";
  if (last.error) return last.error;
  return `Last backup: ${fmtMoment(last.at)} · ${fmtSize(last.size_bytes ?? 0)}.`;
}

function RestoreDialog({
  chosen,
  onClose,
}: Readonly<{ chosen: BackupRow; onClose: () => void }>): ReactElement {
  const restore = useAction(
    () => restoreBackup(chosen.id),
    () => {
      toast("Restored. SecureFolio is restarting.");
      void restartBackend();
      onClose();
    },
  );
  const when = fmtMoment(chosen.created_at);
  return (
    <Modal
      title="Restore from a backup?"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn danger"
            disabled={restore.busy}
            onClick={() => {
              void restore.run();
            }}
          >
            Restore
          </button>
        </>
      }
    >
      <div className="modal-body">
        <p>
          The library goes back to the backup from {when}: notes, chats, cards
          and corrections. Anything since then is replaced. The current library
          is saved first, so you can undo this.
        </p>
        <p className="small" style={{ color: "var(--ink-2)" }}>
          Your files on disk aren't touched. The search index is rebuilt from
          the library without reading them again.
        </p>
        {restore.error && (
          <p role="alert" className="small">
            {restore.error}
          </p>
        )}
      </div>
    </Modal>
  );
}

function BackupCard(): ReactElement {
  const settings = useLoad(getSettings, "settings");
  const backups = useLoad(listBackups, "backups");
  const [chosen, setChosen] = useState<BackupRow | null>(null);
  const change = useAction(updateSettings, settings.reload);
  const now = useAction(backUpNow, backups.reload);
  const move = useAction(async () => {
    const folder = await chooseFolder();
    if (folder) await updateSettings({ backup_folder: folder });
    backups.reload();
  });
  const latest = backups.data?.backups[0];
  const failed = backups.data?.last?.error;
  return (
    <div className="card">
      <h2 style={{ marginBottom: 8 }}>Backup</h2>
      {chosen && (
        <RestoreDialog
          chosen={chosen}
          onClose={() => {
            setChosen(null);
          }}
        />
      )}
      <Row
        title="Nightly backup"
        desc={
          <span role={failed ? "alert" : undefined}>
            Your library, files you copied in, and a list of where your watched
            files are. {lastLine(backups.data)}
          </span>
        }
      >
        <Switch
          hideLabel
          label="Nightly backup"
          checked={settings.data?.backup_nightly ?? true}
          onChange={(value) => {
            void change.run({ backup_nightly: value });
          }}
        />
      </Row>
      <Row
        title="Backup folder"
        desc={<span className="mono">{backups.data?.folder}</span>}
      >
        <button
          type="button"
          className="btn sm"
          onClick={() => {
            void move.run();
          }}
        >
          Change…
        </button>
      </Row>
      <Row title="Back up now">
        <button
          type="button"
          className="btn sm"
          disabled={now.busy}
          onClick={() => {
            void now.run();
          }}
        >
          <HardDriveDownload size={12} aria-hidden="true" /> Back up now
        </button>
      </Row>
      <Row
        title="Restore"
        desc="Brings back notes, chats, cards and corrections. The search index is rebuilt from the library without reading your files again."
      >
        <button
          type="button"
          className="btn sm"
          disabled={!latest}
          onClick={() => {
            if (latest) setChosen(latest);
          }}
        >
          <Upload size={12} aria-hidden="true" /> Restore from backup…
        </button>
      </Row>
      {(now.error ?? move.error ?? change.error) && (
        <p role="alert" className="small">
          {now.error ?? move.error ?? change.error}
        </p>
      )}
    </div>
  );
}

function V1Card(): ReactElement {
  const last = useLoad(getV1Import, "v1");
  const run = useAction(async () => {
    const path = await chooseFile();
    if (path) await importV1(path);
    last.reload();
  });
  const line = last.data
    ? `Imported ${fmtMoment(last.data.at)}: ${String(last.data.notes)} notes, ${String(last.data.sessions)} sessions, ${String(last.data.saved_queries)} saved queries. v1's files weren't changed.`
    : "Brings in v1's notes, sessions and saved queries, as Private. v1's files aren't changed.";
  return (
    <div className="card">
      <h2 style={{ marginBottom: 8 }}>DocSynth v1</h2>
      <Row title="Import from v1" desc={line}>
        <button
          type="button"
          className="btn sm"
          onClick={() => {
            void run.run();
          }}
        >
          {last.data ? "Import again" : "Choose metadata.db…"}
        </button>
      </Row>
      {run.error && (
        <p role="alert" className="small">
          {run.error}
        </p>
      )}
    </div>
  );
}

function AppCard(): ReactElement {
  const login = useLoad(isOpenAtLogin, "login");
  const setLogin = useAction(openAtLogin, login.reload);
  const log = useAction(async () => {
    const { path } = await getLogFolder();
    await showInFinder(path);
  });
  return (
    <div className="card">
      <h2 style={{ marginBottom: 8 }}>App</h2>
      <Row title="Open at login">
        <Switch
          hideLabel
          label="Open at login"
          checked={login.data ?? false}
          onChange={(value) => {
            void setLogin.run(value);
          }}
        />
      </Row>
      <Row
        title="Quick search hotkey"
        desc="Opens the quick-search window from anywhere"
      >
        <span className="kbd">⌥ Space</span>
      </Row>
      <Row
        title="Service log"
        desc="For troubleshooting. Holds no document text."
      >
        <button
          type="button"
          className="btn sm"
          onClick={() => {
            void log.run();
          }}
        >
          Show in Finder
        </button>
      </Row>
      {(log.error ?? setLogin.error) && (
        <p role="alert" className="small">
          {log.error ?? setLogin.error}
        </p>
      )}
    </div>
  );
}

export function Backup(): ReactElement {
  return (
    <>
      <BackupCard />
      <V1Card />
      <AppCard />
    </>
  );
}
