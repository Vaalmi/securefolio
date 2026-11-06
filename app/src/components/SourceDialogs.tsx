import { useState } from "react";
import type { ReactElement } from "react";

import {
  addSource,
  getOrganizeEstimate,
  stopWatchingSource,
  updateSource,
} from "../api";
import type { OrganizeEstimate, Sensitivity, Source } from "../api";
import { toast } from "../toast";
import { Modal } from "../ui";
import { useAction } from "../useAction";
import { useLoad } from "../useLoad";
import { FolderSettings, SensitivityPicker } from "./FolderSettings";
import type { FolderDraft } from "./FolderSettings";

// Not loaded yet, or no known price for the helper model, so no cloud card is written.
function usd(value: number | null | undefined, digits: number): string {
  if (value === undefined) return "…";
  if (value === null) return "no known price";
  return `$${value.toFixed(digits)}`;
}

function rates(input: number | null, output: number | null): string {
  if (input === null || output === null) return "no known price";
  return `$${input.toFixed(2)} in · $${output.toFixed(2)} out per million tokens`;
}

// Who writes the cards.
function whoWrites(estimate: OrganizeEstimate): string {
  return `Cloud AI (${estimate.model_name}, from ${estimate.provider ?? "this Mac"}) writes the cards for this folder instead of Local AI.`;
}

// At what price: the backfill at the batch price where it applies, each new document
// at full price; or full price throughout, which is all OpenRouter has.
function priceLine(estimate: OrganizeEstimate): string {
  const full = rates(estimate.input_usd, estimate.output_usd);
  if (estimate.batched) {
    const batch = rates(
      estimate.backfill_input_usd,
      estimate.backfill_output_usd,
    );
    return `The documents here now go at the batch price, ${batch}, half the full price. Each new document after goes at full price, ${full}.`;
  }
  const why =
    estimate.provider === "OpenRouter"
      ? ", since OpenRouter has no batch price"
      : "";
  return `At full price${why}: ${full}.`;
}

// Why it can't be turned on yet (no ready key), or why the cloud can't write the
// cards now even though it's on.
function Availability({
  estimate,
}: Readonly<{ estimate: OrganizeEstimate }>): ReactElement | null {
  if (!estimate.key_ready) {
    return (
      <p role="status" className="small">
        Can't be turned on yet: {estimate.unavailable}
      </p>
    );
  }
  if (!estimate.unavailable) return null;
  return (
    <p role="status" className="small">
      Not available now: {estimate.unavailable}
    </p>
  );
}

export function OrganizeDialog({
  source,
  onClose,
}: Readonly<{ source: Source; onClose: () => void }>): ReactElement {
  const estimate = useLoad(() => getOrganizeEstimate(source.id), source.id);
  const found = estimate.data;
  const cost = usd(found?.backfill_usd, 2);
  const turnOn = useAction(
    (shown: OrganizeEstimate) =>
      updateSource(source.id, {
        auto_organize: true,
        model: shown.model,
        input_usd: shown.input_usd,
        output_usd: shown.output_usd,
        batched: shown.batched,
      }).catch((failed: unknown) => {
        estimate.reload(); // a changed helper or price: show the estimate as it is now
        throw failed;
      }),
    () => {
      toast(`Cloud organizing is on for ${source.name}.`);
      onClose();
    },
  );
  return (
    <Modal
      title={`Organize ${source.name} in the cloud?`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Keep it off
          </button>
          <button
            type="button"
            className="btn primary"
            disabled={!found?.key_ready}
            onClick={() => {
              if (found) void turnOn.run(found);
            }}
          >
            Turn on · about {cost}
          </button>
        </>
      }
    >
      <div className="modal-body">
        {found && <p>{whoWrites(found)}</p>}
        {found && <p className="small">{priceLine(found)}</p>}
        <div className="stack" style={{ gap: 4 }}>
          <strong className="small">What it sends for each document</strong>
          <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
            <li>The file name and path</li>
            <li>Its first pages and headings, up to about 6,800 tokens</li>
          </ul>
        </div>
        <p className="small">
          Estimate for {source.items.toLocaleString("en-US")} documents
          {found?.batched ? " at the batch price" : ""}:{" "}
          <strong className="mono">{cost}</strong> once, then about{" "}
          <span className="mono">{usd(found?.per_document_usd, 3)}</span> per
          new document. It counts toward your monthly cap and pauses when the
          cap is reached.
        </p>
        {found && <Availability estimate={found} />}
        {(turnOn.error ?? estimate.error) && (
          <p role="alert" className="small">
            {turnOn.error ?? estimate.error}
          </p>
        )}
      </div>
    </Modal>
  );
}

export function SensitivityDialog({
  source,
  onClose,
}: Readonly<{
  source: Source;
  onClose: () => void;
}>): ReactElement {
  const [level, setLevel] = useState<Sensitivity>(
    source.sensitivity === "private" ? "standard" : "private",
  );
  const save = useAction(
    () => updateSource(source.id, { sensitivity: level }),
    () => {
      toast(
        `${source.name} is now ${level === "private" ? "Private" : "Standard"}.`,
      );
      onClose();
    },
  );
  return (
    <Modal
      title={`Sensitivity for ${source.name}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn primary"
            onClick={() => {
              void save.run();
            }}
          >
            Save
          </button>
        </>
      }
    >
      <div className="modal-body">
        <SensitivityPicker
          value={level}
          onChange={setLevel}
          name={source.name}
        />
        <p className="small" style={{ color: "var(--ink-2)" }}>
          {level === "private"
            ? "Making it Private turns off cloud organizing and MCP for this folder. Chats that already used its documents become Private."
            : "Making it Standard lets answers send its passages to Cloud AI when they're used. Chats that are already Private stay Private."}
        </p>
        {save.error && (
          <p role="alert" className="small">
            {save.error}
          </p>
        )}
      </div>
    </Modal>
  );
}

export function StopDialog({
  source,
  onClose,
}: Readonly<{
  source: Source;
  onClose: () => void;
}>): ReactElement {
  const stop = useAction(
    () => stopWatchingSource(source.id),
    () => {
      toast(
        `Stopped watching ${source.name}. The files on your Mac weren't touched.`,
      );
      onClose();
    },
  );
  return (
    <Modal
      title={`Stop watching ${source.name}?`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn danger"
            onClick={() => {
              void stop.run();
            }}
          >
            Stop watching
          </button>
        </>
      }
    >
      <div className="modal-body">
        <p>
          Its {source.items.toLocaleString("en-US")} documents leave the
          library. The files stay where they are on your Mac, and notes about
          them are kept.
        </p>
        {stop.error && (
          <p role="alert" className="small">
            {stop.error}
          </p>
        )}
      </div>
    </Modal>
  );
}

export function AddDialog({
  path,
  level,
  onClose,
}: Readonly<{
  path: string;
  level: Sensitivity;
  onClose: () => void;
}>): ReactElement {
  const [draft, setDraft] = useState<FolderDraft>({
    path,
    sensitivity: level,
    organize: false,
    mcp: level === "standard",
  });
  const add = useAction(
    () => addSource(draft.path, draft.sensitivity, draft.organize, draft.mcp),
    () => {
      toast(`Watching ${draft.path}. Indexing in the background.`);
      onClose();
    },
  );
  return (
    <Modal
      title="Add a folder"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn primary"
            onClick={() => {
              void add.run();
            }}
          >
            Add and start indexing
          </button>
        </>
      }
    >
      <div className="modal-body">
        <p className="small" style={{ color: "var(--ink-2)" }}>
          Chosen in Finder. SecureFolio never changes, moves or deletes files in
          it.
        </p>
        <FolderSettings f={draft} onChange={setDraft} />
        {add.error && (
          <p role="alert" className="small">
            {add.error}
          </p>
        )}
      </div>
    </Modal>
  );
}
