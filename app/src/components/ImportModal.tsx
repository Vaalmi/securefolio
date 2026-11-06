import { toast } from "../toast";
import { useState } from "react";
import type { ReactElement } from "react";
import { FolderPlus, Upload } from "lucide-react";

import { importPaths } from "../api";
import { getSettings } from "../api";
import { useLoad } from "../useLoad";
import type { Sensitivity } from "../api";
import { chooseFiles } from "../desktop";
import { go, useRoute, withParam } from "../router";
import { Modal } from "../ui";
import { useAction } from "../useAction";
import { SensitivityPicker } from "./FolderSettings";

const MAX_NAMES = 3;

function baseName(path: string): string {
  return path.split("/").pop() ?? path;
}

/** Import files (⌘O): pick files, choose their sensitivity, and they're copied in. */
export function ImportModal(): ReactElement {
  const route = useRoute();
  const [paths, setPaths] = useState<string[]>([]);
  const defaults = useLoad(getSettings, "defaults");
  const [chosen, setLevel] = useState<Sensitivity>();
  const level = chosen ?? defaults.data?.default_sensitivity ?? "private";
  const close = (): void => {
    go(withParam(route, "import", null));
  };
  const save = useAction(
    () => importPaths(paths, level),
    () => {
      close();
      toast(
        `Copying ${String(paths.length)} files into the library as ${level === "private" ? "Private" : "Standard"}. Follow them in the import tray.`,
      );
    },
  );
  const shown = paths.slice(0, MAX_NAMES).map(baseName).join(", ");
  return (
    <Modal
      title="Import files"
      onClose={close}
      footer={
        <>
          <button type="button" className="btn" onClick={close}>
            Cancel
          </button>
          <button
            type="button"
            className="btn primary"
            disabled={paths.length === 0 || save.busy}
            onClick={() => {
              void save.run();
            }}
          >
            <Upload size={14} aria-hidden="true" /> Import {paths.length} files
          </button>
        </>
      }
    >
      <div className="modal-body">
        <div
          className="empty"
          style={{
            border: "1.5px dashed var(--line-strong)",
            borderRadius: 12,
            padding: 20,
          }}
        >
          <p>
            Drop files or folders anywhere in the window, including saved web
            pages
          </p>
          <button
            type="button"
            className="btn"
            onClick={() => {
              void chooseFiles().then(setPaths);
            }}
          >
            <FolderPlus size={14} aria-hidden="true" /> Choose files…
          </button>
          {paths.length > 0 && (
            <p className="small" style={{ color: "var(--ink-2)" }}>
              {paths.length} chosen: {shown}
              {paths.length > MAX_NAMES ? "…" : ""}
            </p>
          )}
        </div>
        <div className="stack" style={{ gap: 6 }}>
          <span className="label" style={{ fontWeight: 500 }}>
            Sensitivity for these files
          </span>
          <SensitivityPicker
            value={level}
            onChange={setLevel}
            name="imported files"
          />
        </div>
        {save.error && (
          <p role="alert" className="small">
            {save.error}
          </p>
        )}
        <p className="small" style={{ color: "var(--ink-2)" }}>
          Imported files are copied into the library, so your originals stay
          where they are. Scanned pages are found and read on this Mac.
        </p>
      </div>
    </Modal>
  );
}
