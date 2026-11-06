import type { ReactElement } from "react";
import { CheckCircle2, Cpu, Download } from "lucide-react";

import { downloadLocalModel, downloadModels } from "../api";
import type { ModelGroup, ModelsStatus } from "../api";
import { LocalBadge } from "../ui";
import { useAction } from "../useAction";

const GB = 1024 ** 3;

function gigabytes(bytes: number): string {
  return `${(bytes / GB).toFixed(1)} GB`;
}

function percent(done: number, total: number): number {
  return total > 0 ? Math.round((done / total) * 100) : 0;
}

function Progress({
  label,
  done,
  total,
}: Readonly<{ label: string; done: number; total: number }>): ReactElement {
  const value = percent(done, total);
  return (
    <div
      className="progress"
      role="progressbar"
      aria-label={label}
      aria-valuenow={value}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <span style={{ width: `${String(value)}%` }} />
    </div>
  );
}

function groupLine(group: ModelGroup): string {
  switch (group.state) {
    case "installed":
      return "Downloaded and checked.";
    case "verifying":
      return "Checking what was downloaded.";
    case "failed":
      return group.reason ?? "The download stopped.";
    case "downloading":
      return "You can carry on; it finishes in the background.";
    default:
      return "They download once.";
  }
}

const WORST_FIRST = ["failed", "not_installed", "downloading", "verifying"];

function worstOf(first: ModelGroup, second: ModelGroup): ModelGroup {
  for (const state of WORST_FIRST) {
    const found = [first, second].find((group) => group.state === state);
    if (found) return found;
  }
  return first;
}

// The search, OCR and page-reading models, as one line.
export function SearchModelsRow({
  models,
  onChanged,
}: Readonly<{ models: ModelsStatus; onChanged: () => void }>): ReactElement {
  const done = models.search.bytes_done + models.page.bytes_done;
  const total = models.search.bytes_total + models.page.bytes_total;
  const group = worstOf(models.search, models.page);
  const installed = group.state === "installed";
  const start = useAction(downloadModels, onChanged);
  const waiting = group.state === "not_installed" || group.state === "failed";
  return (
    <div className="folder-row">
      <div className="hstack">
        <strong style={{ flex: 1 }}>Search and OCR models</strong>
        <span className="small mono">
          {installed
            ? gigabytes(total)
            : `${gigabytes(done)} of ${gigabytes(total)}`}
        </span>
      </div>
      <Progress label="Search and OCR models" done={done} total={total} />
      <span className="small" style={{ color: "var(--ink-2)" }}>
        {groupLine(group)}
      </span>
      {waiting && (
        <button
          type="button"
          className="btn"
          style={{ alignSelf: "flex-start" }}
          onClick={() => {
            void start.run();
          }}
        >
          <Download size={14} aria-hidden="true" />{" "}
          {group.state === "failed" ? "Try again" : "Download"}
        </button>
      )}
      {start.error && (
        <span role="alert" className="small">
          {start.error}
        </span>
      )}
    </div>
  );
}

function OllamaNotes({
  models,
}: Readonly<{ models: ModelsStatus }>): ReactElement | null {
  const { ollama } = models;
  if (ollama.refused) {
    return (
      <p role="alert" className="small">
        {ollama.refused}
      </p>
    );
  }
  if (!ollama.running) {
    return (
      <p className="small" style={{ color: "var(--ink-2)" }}>
        Ollama isn't running on this Mac. Install it from ollama.com and open
        it, then come back here.
      </p>
    );
  }
  if (ollama.no_cloud) return null;
  return (
    <p className="small" style={{ color: "var(--ink-2)" }}>
      Please turn off Ollama's cloud features: run{" "}
      <span className="mono">launchctl setenv OLLAMA_NO_CLOUD 1</span>, then
      restart Ollama. It's Ollama's own setting, so SecureFolio can't set it for
      you.
    </p>
  );
}

export function LocalModelRow({
  models,
  onChanged,
}: Readonly<{ models: ModelsStatus; onChanged: () => void }>): ReactElement {
  const { local, ollama } = models;
  const start = useAction(downloadLocalModel, onChanged);
  const pulling = local.pull.state === "downloading";
  return (
    <div className="folder-row">
      <div className="hstack">
        <Cpu size={16} aria-hidden="true" />
        <strong style={{ flex: 1 }}>Local model: {local.tag}</strong>
        {local.installed && <LocalBadge />}
      </div>
      <p className="small" style={{ color: "var(--ink-2)" }}>
        Optional. Answers Private and Offline questions and writes document
        cards on this Mac. Until it's installed, cards are basic and marked
        pending. Runs through Ollama.
      </p>
      <OllamaNotes models={models} />
      {local.installed && (
        <span className="hstack small" style={{ gap: 4 }}>
          <CheckCircle2
            size={14}
            aria-hidden="true"
            style={{ color: "var(--success)" }}
          />{" "}
          Installed
        </span>
      )}
      {!local.installed && pulling && (
        <>
          <Progress
            label="Local model"
            done={local.pull.done}
            total={local.pull.total}
          />
          <span className="small" style={{ color: "var(--ink-2)" }}>
            {gigabytes(local.pull.done)} of {gigabytes(local.pull.total)}
          </span>
        </>
      )}
      {!local.installed && !pulling && (
        <button
          type="button"
          className="btn"
          style={{ alignSelf: "flex-start" }}
          disabled={!ollama.running}
          onClick={() => {
            void start.run();
          }}
        >
          <Download size={14} aria-hidden="true" /> Download local model
        </button>
      )}
      {(start.error ?? local.pull.reason) && (
        <span role="alert" className="small">
          {start.error ?? local.pull.reason}
        </span>
      )}
    </div>
  );
}
