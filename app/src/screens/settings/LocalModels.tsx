import { useRef, useState } from "react";
import type { ReactElement } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Cloud,
  Cpu,
  Download,
  XCircle,
} from "lucide-react";

import { pullLocalModel, updateSettings } from "../../api";
import type { InstalledModel, LocalJob, ModelsStatus } from "../../api";
import { useAction } from "../../useAction";
import { Row } from "./Row";

// Settings, Local models: a model for each job, what's installed in Ollama, and a
// download by name.

const GIB = 1024 ** 3;

const JOBS: { id: LocalJob; title: string; desc: string; used: string }[] = [
  {
    id: "local_answers",
    title: "Private and Offline answers",
    desc: "With their helper steps",
    used: "Answers",
  },
  {
    id: "local_cards",
    title: "Cards",
    desc: "New documents, and cards still pending",
    used: "Cards",
  },
  {
    id: "local_figures",
    title: "Figures and charts",
    desc: "Reads pictures, so only models that take images",
    used: "Figures and charts",
  },
];

function gigabytes(bytes: number): string {
  return `${(bytes / GIB).toFixed(1)} GB`;
}

function JobWarning({
  job,
  models,
}: Readonly<{ job: LocalJob; models: ModelsStatus }>): ReactElement | null {
  const { tag, problem } = models.jobs[job];
  const here = models.installed.local.some((m) => m.name === tag);
  const waits =
    job === "local_cards" ? "cards wait as pending" : "this job waits";
  const words = here
    ? problem
    : `${tag} is no longer in Ollama. Choose another model; ${waits} until you do.`;
  if (!words) return null;
  return (
    <span className="hstack needs-key" style={{ gap: 4 }}>
      <AlertTriangle size={12} aria-hidden="true" />
      {words}
    </span>
  );
}

function JobRow({
  job,
  models,
  onChoose,
  saving,
}: Readonly<{
  job: (typeof JOBS)[number];
  models: ModelsStatus;
  onChoose: (tag: string) => void;
  saving: boolean; // a choice still being saved: no other is sent past it
}>): ReactElement {
  const { tag } = models.jobs[job.id];
  const options = models.installed.local.filter((m) => m.jobs.includes(job.id));
  const present = options.some((m) => m.name === tag);
  const id = `lm-${job.id}`;
  return (
    <Row
      title={job.title}
      desc={
        <>
          <span>{job.desc}</span>
          <JobWarning job={job.id} models={models} />
        </>
      }
    >
      <label className="sr-only" htmlFor={id}>
        Model for {job.title}
      </label>
      <select
        id={id}
        className="select"
        style={{ width: 240 }}
        value={present ? tag : ""}
        aria-invalid={!present}
        disabled={saving}
        onChange={(event) => {
          onChoose(event.target.value);
        }}
      >
        {!present && (
          <option value="" disabled>
            Choose a model
          </option>
        )}
        {options.map((m) => (
          <option key={m.name} value={m.name}>
            {m.name}
            {m.name === models.local.recommended ? " · suggested" : ""}
          </option>
        ))}
      </select>
    </Row>
  );
}

export function JobsCard({
  models,
  onChanged,
}: Readonly<{ models: ModelsStatus; onChanged: () => void }>): ReactElement {
  const change = useAction(updateSettings, onChanged);
  const { memory } = models;
  return (
    <div className="card">
      <h2 style={{ marginBottom: 4 }}>Model for each job</h2>
      <p className="small" style={{ color: "var(--ink-2)", marginBottom: 8 }}>
        Changes take effect from the next question or job, without a restart. A
        new cards or figures model is used for new documents and pending work
        only; what's already made stays as it is.
      </p>
      {JOBS.map((job) => (
        <JobRow
          key={job.id}
          job={job}
          models={models}
          saving={change.busy}
          onChoose={(tag) => {
            void change.run({ [job.id]: tag });
          }}
        />
      ))}
      <p className="small" role="status" style={{ marginTop: 8 }}>
        Together they need{" "}
        <strong className="mono">
          {memory.about ? "about " : ""}
          {memory.gb.toFixed(1)} GB
        </strong>{" "}
        of memory while loaded. This Mac has{" "}
        <span className="mono">{models.mac_memory_gb} GB</span>. Each different
        model takes its own memory, and unloads after 5 idle minutes.
      </p>
      {change.error && (
        <p role="alert" className="small">
          {change.error}
        </p>
      )}
    </div>
  );
}

function takesImages(model: InstalledModel): string {
  if (model.images === null) return "Not known yet";
  return model.images ? "Yes" : "No";
}

function LocalRow({
  model,
  models,
}: Readonly<{ model: InstalledModel; models: ModelsStatus }>): ReactElement {
  const used = JOBS.filter((job) => models.jobs[job.id].tag === model.name)
    .map((job) => job.used)
    .join(", ");
  return (
    <tr>
      <td className="mono small">{model.name}</td>
      <td className="num">{gigabytes(model.size)}</td>
      <td className="num">
        {model.memory.about ? "about " : ""}
        {model.memory.gb.toFixed(1)} GB
      </td>
      <td className="small">{takesImages(model)}</td>
      <td className="small">
        {used || <span className="muted">Not used</span>}
      </td>
    </tr>
  );
}

export function InstalledCard({
  models,
}: Readonly<{ models: ModelsStatus }>): ReactElement {
  return (
    <div className="card">
      <h2 style={{ marginBottom: 4 }}>Installed in Ollama</h2>
      <p className="small" style={{ color: "var(--ink-2)", marginBottom: 8 }}>
        Suggested for this Mac's {models.mac_memory_gb} GB:{" "}
        <span className="mono">{models.local.recommended}</span>. Remove models
        in Ollama itself.
      </p>
      <div className="table-scroll">
        <table className="table">
          <caption className="sr-only">Models installed in Ollama</caption>
          <thead>
            <tr>
              <th scope="col">Model</th>
              <th scope="col" className="num">
                Size
              </th>
              <th scope="col" className="num">
                Memory
              </th>
              <th scope="col">Takes images</th>
              <th scope="col">Used for</th>
            </tr>
          </thead>
          <tbody>
            {models.installed.local.length + models.installed.remote.length ===
              0 && (
              <tr>
                <td colSpan={5} className="small muted">
                  Nothing is installed in Ollama.
                </td>
              </tr>
            )}
            {models.installed.local.map((model) => (
              <LocalRow key={model.name} model={model} models={models} />
            ))}
            {models.installed.remote.map((model) => (
              <tr key={model.name}>
                <td className="mono small">{model.name}</td>
                <td className="num">–</td>
                <td className="num">–</td>
                <td className="small">–</td>
                <td className="small">
                  <span className="hstack" style={{ gap: 4 }}>
                    <Cloud size={12} aria-hidden="true" /> Runs on Ollama's
                    servers, so never used as a local model
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PullStatus({
  models,
  refused,
}: Readonly<{ models: ModelsStatus; refused: string }>): ReactElement {
  // The backend's download always shows as it is; a refusal is about the name just
  // sent, so it shows beside it and never hides it.
  const { pull } = models.local;
  const failures = [refused, pull.state === "failed" ? pull.reason : ""].filter(
    (words): words is string => Boolean(words),
  );
  return (
    <span id="pull-status" role="status" className="stack" style={{ gap: 4 }}>
      {pull.tag && pull.state === "downloading" && (
        <>
          <span className="small hstack" style={{ gap: 6 }}>
            <Cpu size={12} aria-hidden="true" />
            <span className="mono">{pull.tag}</span> · {gigabytes(pull.done)} of{" "}
            {gigabytes(pull.total)}
          </span>
          <span
            className="progress"
            role="progressbar"
            aria-label={`Downloading ${pull.tag}`}
            aria-valuenow={
              pull.total > 0 ? Math.round((pull.done / pull.total) * 100) : 0
            }
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <span
              style={{
                width: `${String(pull.total > 0 ? (pull.done / pull.total) * 100 : 0)}%`,
              }}
            />
          </span>
        </>
      )}
      {pull.tag && pull.state === "installed" && (
        <span className="small hstack" style={{ gap: 6 }}>
          <CheckCircle2
            size={14}
            aria-hidden="true"
            style={{ color: "var(--success)" }}
          />{" "}
          <span className="mono">{pull.tag}</span> is installed. Choose it for a
          job above.
        </span>
      )}
      {failures.map((words) => (
        <span key={words} className="small hstack" style={{ gap: 6 }}>
          <XCircle
            size={14}
            aria-hidden="true"
            style={{ color: "var(--danger)" }}
          />{" "}
          {words}
        </span>
      ))}
    </span>
  );
}

export function PullCard({
  models,
  offline,
  onStarted,
}: Readonly<{
  models: ModelsStatus;
  offline: boolean;
  onStarted: () => void;
}>): ReactElement {
  const [tag, setTag] = useState("");
  const [refused, setRefused] = useState("");
  // One download at a time: the form stays blocked from the click until a status
  // newer than the one shown then comes back, which says the download is running.
  // The ref claims it before React renders, so a double click sends one request.
  const sending = useRef(false);
  const [sentWith, setSentWith] = useState<ModelsStatus | null>(null);
  const pulling =
    sentWith === models || models.local.pull.state === "downloading";
  const start = async (name: string): Promise<void> => {
    if (sending.current) return;
    sending.current = true;
    setSentWith(models);
    setRefused("");
    try {
      await pullLocalModel(name);
      onStarted();
    } catch (failure) {
      // A cloud name, Offline, another download, or Ollama stopped: the reason why.
      setRefused(failure instanceof Error ? failure.message : String(failure));
      setSentWith(null);
    } finally {
      sending.current = false;
    }
  };
  return (
    <div className="card">
      <h2 style={{ marginBottom: 4 }}>Download another model</h2>
      <form
        className="stack"
        style={{ gap: 6 }}
        onSubmit={(event) => {
          event.preventDefault();
          const name = tag.trim();
          if (!name || offline || pulling) return;
          void start(name);
        }}
      >
        <label htmlFor="pull-tag" className="small">
          Ollama model name
        </label>
        <span className="hstack">
          <input
            id="pull-tag"
            className="input mono"
            placeholder="qwen3.5:27b"
            value={tag}
            // One download at a time: the name stays as submitted until it ends.
            readOnly={pulling}
            disabled={offline}
            aria-describedby="pull-help pull-status"
            style={{ width: 240 }}
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => {
              setTag(event.target.value);
              setRefused("");
            }}
          />
          <button
            type="submit"
            className="btn sm"
            disabled={!tag.trim() || offline || pulling}
          >
            <Download size={12} aria-hidden="true" /> Download
          </button>
        </span>
        <span id="pull-help" className="help">
          {offline
            ? "Offline is on: SecureFolio asks Ollama for no downloads."
            : "Ollama downloads it from its library. Names ending in -cloud run on Ollama's servers and aren't offered."}
        </span>
        <PullStatus models={models} refused={refused} />
      </form>
    </div>
  );
}
