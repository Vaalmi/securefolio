import { useEffect, useRef, useState } from "react";
import type { ReactElement } from "react";
import { FolderPlus } from "lucide-react";

import { addSource, downloadModels, getModels, getSettings } from "../api";
import { FolderSettings } from "../components/FolderSettings";
import type { FolderDraft } from "../components/FolderSettings";
import { LocalModelRow, SearchModelsRow } from "../components/ModelRows";
import { chooseFolder } from "../desktop";
import { isDownloading } from "../downloads";
import { go } from "../router";
import { useAction } from "../useAction";
import { useLoad } from "../useLoad";
import { KeyStep } from "./SetupKey";
import { ReadyStep } from "./SetupReady";
import { SetupShell } from "./SetupShell";

const POLL_MS = 1000;

function ModelsStep(): ReactElement {
  const models = useLoad(getModels, "models", {
    every: POLL_MS,
    while: isDownloading,
  });
  const started = useRef(false);
  const state = models.data?.search.state;
  useEffect(() => {
    if (state === "not_installed" && !started.current) {
      started.current = true;
      void downloadModels().then(models.reload);
    }
  }, [state, models.reload]);
  return (
    <SetupShell at={1}>
      <h1>Download the search models</h1>
      <p style={{ color: "var(--ink-2)" }}>
        These run on this Mac to read scans and find meaning in your documents.
        They download once.
      </p>
      {models.error && <p role="alert">{models.error}</p>}
      {models.data && (
        <>
          <SearchModelsRow models={models.data} onChanged={models.reload} />
          <LocalModelRow models={models.data} onChanged={models.reload} />
        </>
      )}
      <div className="hstack">
        <button
          type="button"
          className="btn ghost"
          onClick={() => {
            go("/setup/key");
          }}
        >
          Back
        </button>
        <span className="spacer" />
        <button
          type="button"
          className="btn primary"
          onClick={() => {
            go("/setup/folders");
          }}
        >
          Continue
        </button>
      </div>
    </SetupShell>
  );
}

function FoldersStep(): ReactElement {
  const settings = useLoad(getSettings, "settings");
  const level = settings.data?.default_sensitivity;
  const [folders, setFolders] = useState<FolderDraft[]>([]);
  const add = useAction(async () => {
    const path = await chooseFolder();
    if (level && path && !folders.some((f) => f.path === path)) {
      setFolders((had) => [
        ...had,
        {
          path,
          sensitivity: level,
          organize: false,
          mcp: level === "standard",
        },
      ]);
    }
  });
  const start = useAction(
    () =>
      Promise.all(
        // Organizing is never on from here: it's offered once the estimate can be shown.
        folders.map((f) => addSource(f.path, f.sensitivity, false, f.mcp)),
      ),
    () => {
      go("/setup/ready");
    },
  );
  return (
    <SetupShell at={2}>
      <h1>Choose folders to watch</h1>
      <p style={{ color: "var(--ink-2)" }}>
        SecureFolio reads them where they are and never changes, moves or
        deletes your files.
      </p>
      {folders.map((folder, i) => (
        <FolderSettings
          key={folder.path}
          f={folder}
          onChange={(next) => {
            setFolders((had) => had.map((f, j) => (j === i ? next : f)));
          }}
        />
      ))}
      <button
        type="button"
        className="btn"
        style={{ alignSelf: "flex-start" }}
        disabled={!level}
        onClick={() => {
          void add.run();
        }}
      >
        <FolderPlus size={14} aria-hidden="true" /> Add folder…
      </button>
      <p className="small" style={{ color: "var(--ink-2)" }}>
        A Standard folder can be organized in the cloud, off by default. The
        next step offers it, with what it sends and an estimate, once files are
        counted.
      </p>
      {(start.error ?? add.error ?? settings.error) && (
        <p role="alert" className="small">
          {start.error ?? add.error ?? settings.error}
        </p>
      )}
      <div className="hstack">
        <button
          type="button"
          className="btn ghost"
          onClick={() => {
            go("/setup/models");
          }}
        >
          Back
        </button>
        <span className="spacer" />
        <button
          type="button"
          className="btn primary"
          disabled={start.busy}
          onClick={() => {
            void start.run();
          }}
        >
          Start indexing
        </button>
      </div>
    </SetupShell>
  );
}

export function Setup({ step }: Readonly<{ step: string }>): ReactElement {
  if (step === "models") return <ModelsStep />;
  if (step === "folders") return <FoldersStep />;
  if (step === "ready") return <ReadyStep />;
  return <KeyStep />;
}
