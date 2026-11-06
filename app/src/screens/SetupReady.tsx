import { useState } from "react";
import type { ReactElement } from "react";
import { Cpu, Plug, Upload } from "lucide-react";

import { getJobCounts, listSources, listTaskModels } from "../api";
import type { Source, TaskModels } from "../api";
import { OrganizeDialog } from "../components/SourceDialogs";
import { bridgePath, chooseFile, installExtension } from "../desktop";
import { go } from "../router";
import { updateSettings, importV1 } from "../api";
import { toast } from "../toast";
import { useAction } from "../useAction";
import { useLoad } from "../useLoad";
import { TASKS } from "./settings/keyLooks";
import { SetupShell } from "./SetupShell";

const POLL_MS = 1000;

function OrganizeOffer(): ReactElement | null {
  const sources = useLoad(listSources, "sources");
  const [chosen, setChosen] = useState<Source | null>(null);
  const offered = (sources.data ?? []).filter(
    (source) =>
      !source.builtin &&
      source.sensitivity === "standard" &&
      !source.auto_organize,
  );
  if (offered.length === 0) return null;
  return (
    <div className="folder-row">
      <strong>Organize a folder in the cloud</strong>
      <p className="small" style={{ color: "var(--ink-2)" }}>
        Off by default. Without it, cards come from the local model.
      </p>
      {offered.map((source) => (
        <div key={source.id} className="hstack">
          <span className="mono small" style={{ flex: 1 }}>
            {source.name}
          </span>
          <button
            type="button"
            className="btn sm"
            onClick={() => {
              setChosen(source);
            }}
          >
            See what it sends and costs
          </button>
        </div>
      ))}
      {chosen && (
        <OrganizeDialog
          source={chosen}
          onClose={() => {
            setChosen(null);
            sources.reload();
          }}
        />
      )}
    </div>
  );
}

function article(name: string): string {
  return /^[AEIOU]/i.test(name) ? "an" : "a";
}

// The tasks still set to a model whose provider has no ready key, in words.
function unkeyed(all: TaskModels): string[] {
  return all.tasks.flatMap((list) => {
    const model = list.models.find((m) => m.id === list.chosen);
    const provider = all.providers.find((p) => p.id === model?.provider);
    if (!model || !provider || provider.key_ready) return [];
    const needs = provider.needs_id
      ? `whose ${provider.name} key needs its ${provider.id_label ?? "ID"}`
      : `which needs ${article(provider.name)} ${provider.name} key`;
    return [`${TASKS[list.task].title}: ${model.name}, ${needs}`];
  });
}

// With no ready key, answers are local, and Ready says so. Either way, any task still
// set to a provider without its key is named, with the way to change it.
function KeyNote({
  onChoose,
}: Readonly<{ onChoose: () => void }>): ReactElement | null {
  const lists = useLoad(listTaskModels, "task-models");
  const all = lists.data;
  if (!all) return null;
  const anyKey = all.providers.some((p) => p.key_ready);
  const tasks = unkeyed(all);
  return (
    <>
      {!anyKey && (
        <p className="small hstack" style={{ gap: 6 }}>
          <Cpu size={14} aria-hidden="true" /> No cloud key: answers come from
          the local model on this Mac until you add one in Settings, Keys and
          models.
        </p>
      )}
      {tasks.length > 0 && (
        <div className="folder-row">
          <strong className="small">
            Some tasks are still set to models you have no key for
          </strong>
          <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
            {tasks.map((words) => (
              <li key={words}>{words}</li>
            ))}
          </ul>
          <span className="small" style={{ color: "var(--ink-2)" }}>
            Until then they run on this Mac, and reading a page in the cloud is
            unavailable.{" "}
            {anyKey
              ? "Choose a model for each task from the key you added."
              : "Add a key, or choose models, in Settings."}
          </span>
          <button
            type="button"
            className="btn sm"
            style={{ alignSelf: "flex-start" }}
            onClick={onChoose}
          >
            Choose models
          </button>
        </div>
      )}
    </>
  );
}

export function ReadyStep(): ReactElement {
  const counts = useLoad(getJobCounts, "counts", {
    every: POLL_MS,
    while: (found) => found.active > 0,
  });
  const done = counts.data?.done ?? 0;
  const total = counts.data?.total ?? 0;
  const [imported, setImported] = useState<string>();
  const v1 = useAction(async () => {
    const path = await chooseFile();
    if (!path) return;
    const found = await importV1(path);
    setImported(
      `Imported ${String(found.notes)} notes, ${String(found.sessions)} sessions and ${String(found.saved_queries)} saved queries, as Private. v1's files weren't changed.`,
    );
  });
  // Setup is done either way, so Settings opens rather than sending back here.
  const finish = useAction(async (to: string) => {
    await updateSettings({ setup_done: true });
    go(to);
  });
  return (
    <SetupShell at={3}>
      <h1>Indexing has started</h1>
      <p style={{ color: "var(--ink-2)" }}>
        Search works on the first files within minutes. Indexing carries on in
        the background while your Mac is on power or above 20% battery.
      </p>
      <div className="folder-row">
        <strong>
          Indexing <span className="mono">{done}</span> of{" "}
          <span className="mono">{total}</span>
        </strong>
        <div
          className="progress"
          role="progressbar"
          aria-label="Indexing"
          aria-valuenow={total ? Math.round((done / total) * 100) : 0}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <span
            style={{ width: `${String(total ? (done / total) * 100 : 0)}%` }}
          />
        </div>
      </div>
      <KeyNote
        onChoose={() => {
          void finish.run("/settings/models");
        }}
      />
      <OrganizeOffer />
      <div className="folder-row">
        <div className="hstack">
          <Plug size={16} aria-hidden="true" />
          <strong style={{ flex: 1 }}>
            Use your library from Claude Desktop and Claude Code
          </strong>
        </div>
        <p className="small" style={{ color: "var(--ink-2)" }}>
          Adds SecureFolio to Claude Desktop as an extension. Claude Desktop and
          Claude Code can search the folders you allowed, read-only, and never
          see Private folders.
        </p>
        <div className="hstack">
          <button
            type="button"
            className="btn"
            onClick={() => {
              void installExtension().then(() => {
                toast(
                  "Opened the SecureFolio extension in Claude Desktop. Confirm there to install it.",
                );
              });
            }}
          >
            Install for Claude Desktop
          </button>
          <button
            type="button"
            className="btn ghost"
            onClick={() => {
              void bridgePath().then((path) =>
                navigator.clipboard
                  .writeText(`claude mcp add securefolio -- ${path}`)
                  .then(() => {
                    toast("Copied the Claude Code command.");
                  }),
              );
            }}
          >
            Copy Claude Code command
          </button>
        </div>
      </div>
      <div className="folder-row">
        <div className="hstack">
          <Upload size={16} aria-hidden="true" />
          <strong style={{ flex: 1 }}>Import from DocSynth v1</strong>
        </div>
        <p className="small" style={{ color: "var(--ink-2)" }}>
          Choose v1's metadata.db to bring in its notes, sessions and saved
          queries, as Private. Its files aren't changed.
        </p>
        <div>
          <button
            type="button"
            className="btn"
            onClick={() => {
              void v1.run();
            }}
          >
            Choose metadata.db…
          </button>
        </div>
        {imported && (
          <p role="status" className="small">
            {imported}
          </p>
        )}
        {v1.error && (
          <p role="alert" className="small">
            {v1.error}
          </p>
        )}
      </div>
      <div className="hstack">
        <span className="spacer" />
        <button
          type="button"
          className="btn primary"
          onClick={() => {
            void finish.run("/home");
          }}
        >
          Open SecureFolio
        </button>
      </div>
    </SetupShell>
  );
}
