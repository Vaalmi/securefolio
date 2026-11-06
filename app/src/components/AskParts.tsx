import { useEffect, useRef, useState } from "react";
import type { ReactElement } from "react";
import {
  AlertTriangle,
  BookmarkCheck,
  BookmarkPlus,
  Check,
  CheckCircle2,
  Cloud,
  Copy,
  Cpu,
  Gauge,
  Loader2,
  Lock,
  RotateCcw,
  Sparkles,
} from "lucide-react";

import { saveAnswerAsNote } from "../api";
import type { Answer, AnswerCitation, RunCard, ThreadMessage } from "../api";
import { modelName } from "../format";
import { money } from "../money";
import { toast } from "../toast";
import { LocalBadge, PrivateBadge } from "../ui";
import { useAction } from "../useAction";

const STEP_LABELS: Record<string, string> = {
  searching: "Searching your library",
  reading: "Reading passages",
  writing: "Writing the answer",
};

function CiteChip({
  citation,
  active,
  onOpen,
}: Readonly<{
  citation: AnswerCitation;
  active: boolean;
  onOpen: () => void;
}>): ReactElement {
  return (
    <button
      type="button"
      className="cite"
      aria-pressed={active}
      aria-label={`Citation ${String(citation.n)}: ${citation.title}, ${citation.label}. Open preview`}
      title={`${citation.title}, ${citation.label}`}
      onClick={onOpen}
    >
      {citation.n}
    </button>
  );
}

export function AnswerBody({
  answer,
  messageId,
  activeCite,
  onCite,
}: Readonly<{
  answer: Answer;
  messageId: string;
  activeCite: string | null;
  onCite: (key: string) => void;
}>): ReactElement {
  const citations = answer.citations ?? [];
  return (
    <p>
      {(answer.parts ?? []).map((part, i) => (
        <span key={i}>
          <span className={part.uncited ? "uncited" : undefined}>
            {part.text}
          </span>
          {part.uncited && (
            <span
              className="uncited-tag"
              title="No passage given to the model supports this sentence."
            >
              <AlertTriangle size={10} aria-hidden="true" />
              Uncited
            </span>
          )}
          {part.cite?.map((n) => {
            const citation = citations.find((c) => c.n === n);
            const key = `${messageId}-${String(n)}`;
            return citation ? (
              <CiteChip
                key={n}
                citation={citation}
                active={activeCite === key}
                onOpen={() => {
                  onCite(key);
                }}
              />
            ) : null;
          })}
        </span>
      ))}
    </p>
  );
}

function AnsweredBy({ answer }: Readonly<{ answer: Answer }>): ReactElement {
  if (answer.counted) return <span>Counted from your library</span>;
  const name = answer.model_name ?? modelName(answer.model);
  if (answer.local) return <LocalBadge tag={name} />;
  return (
    <span className="hstack" style={{ gap: 4 }}>
      <Cloud size={12} aria-hidden="true" />
      Cloud AI · {name}
    </span>
  );
}

export function AnswerMeta({
  answer,
  privateThread,
  when,
}: Readonly<{
  answer: Answer;
  privateThread: boolean;
  when: number;
}>): ReactElement {
  const uncited = (answer.parts ?? []).filter((p) => p.uncited).length;
  const time = new Date(when * 1000).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
  return (
    <div className="answer-meta">
      <AnsweredBy answer={answer} />
      {answer.local && privateThread && <PrivateBadge />}
      <span>· {time}</span>
      {costLine(answer) !== null && <span>· {costLine(answer)}</span>}
      {answer.citations && answer.citations.length > 0 && (
        <span>· {answer.citations.length} cited</span>
      )}
      {answer.kind === "list" && <span>· from cards</span>}
      {uncited > 0 && (
        <span>
          · {uncited} sentence{uncited === 1 ? "" : "s"} uncited
        </span>
      )}
    </div>
  );
}

// What the answer cost in the cloud. A local answer's helper steps may have gone to
// a cloud model, whose calls Spending lists. An older answer doesn't say where its
// helpers ran, so nothing is claimed about it.
function costLine(answer: Answer): string | null {
  if (!answer.local) return `$${answer.cost_usd.toFixed(2)}`;
  if (answer.helper_local === undefined) return null;
  return answer.helper_local ? "no cloud cost" : "helper steps by Cloud AI";
}

function copyable(answer: Answer): string {
  const words = (answer.parts ?? [])
    .map((part) => {
      const marks = (part.cite ?? []).map((n) => `[${String(n)}]`).join("");
      return `${part.text}${marks}`;
    })
    .join("");
  const sources = (answer.citations ?? []).map(
    (c) => `[${String(c.n)}] ${c.title}, ${c.label}`,
  );
  const rows = (answer.table?.rows ?? []).map(
    (row) => `${row.title}, ${row.doc_date ?? "no date"}: ${row.amount_text}`,
  );
  const total = answer.table?.wants_total
    ? [`Total: ${money(answer.table.total)}`]
    : [];
  return [words, ...rows, ...total, ...sources].join("\n");
}

export function AnswerActions({
  message,
  answer,
  canDeep,
  onDeep,
  onRetry,
}: Readonly<{
  message: ThreadMessage;
  answer: Answer;
  canDeep: boolean;
  onDeep: () => void;
  onRetry: () => void;
}>): ReactElement {
  const [copied, setCopied] = useState(false);
  const [saved, setSaved] = useState(false);
  const save = useAction(
    () => saveAnswerAsNote(message.id),
    () => {
      setSaved(true);
      toast("Saved to Notes with its citations.");
    },
  );
  return (
    <div className="answer-actions" role="group" aria-label="Answer actions">
      <button
        type="button"
        className="btn sm ghost"
        onClick={() => {
          void navigator.clipboard.writeText(copyable(answer)).then(() => {
            setCopied(true);
            toast("Copied the answer with its citations.");
          });
        }}
      >
        {copied ? (
          <Check size={12} aria-hidden="true" />
        ) : (
          <Copy size={12} aria-hidden="true" />
        )}{" "}
        Copy
      </button>
      <button type="button" className="btn sm ghost" onClick={onRetry}>
        <RotateCcw size={12} aria-hidden="true" /> Retry
      </button>
      {canDeep && (
        <button type="button" className="btn sm ghost" onClick={onDeep}>
          <Sparkles size={12} aria-hidden="true" /> Re-run in Deep
        </button>
      )}
      <button
        type="button"
        className="btn sm ghost"
        aria-pressed={saved}
        disabled={saved}
        onClick={() => {
          void save.run();
        }}
      >
        {saved ? (
          <BookmarkCheck size={12} aria-hidden="true" />
        ) : (
          <BookmarkPlus size={12} aria-hidden="true" />
        )}
        {saved ? "Saved as note" : "Save as note"}
      </button>
      {save.error && (
        <span role="alert" className="small">
          {save.error}
        </span>
      )}
    </div>
  );
}

export function Steps({
  steps,
}: Readonly<{ steps: { name: string; detail: string }[] }>): ReactElement {
  const last = steps.length - 1;
  return (
    <div className="steps" role="status" aria-live="polite">
      {steps.map((step, i) => (
        <div key={`${step.name}${String(i)}`} className="step">
          <span className="dot">
            {i < last ? (
              <CheckCircle2
                size={14}
                aria-hidden="true"
                style={{ color: "var(--success)" }}
              />
            ) : (
              <Loader2 size={14} aria-hidden="true" className="spin" />
            )}
          </span>
          {STEP_LABELS[step.name] ?? step.name}
          {step.detail && <span className="muted small">· {step.detail}</span>}
          <span className="sr-only">
            {i < last ? ", done" : ", in progress"}
          </span>
        </div>
      ))}
    </div>
  );
}

export function ConsentPrivate({
  titles,
  onLocal,
  onLeave,
}: Readonly<{
  titles: string[];
  onLocal: () => void;
  onLeave: () => void;
}>): ReactElement {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus();
  }, []);
  return (
    <div
      className="consent"
      role="group"
      aria-labelledby="consent-p-title"
      tabIndex={-1}
      ref={ref}
    >
      <h2 id="consent-p-title" className="h-sm hstack">
        <Lock size={16} aria-hidden="true" /> Add Private documents to this
        chat?
      </h2>
      {titles.length > 0 && (
        <p>
          <strong>{titles.join(", ")}</strong>: Private documents never go to
          any cloud model.
        </p>
      )}
      <ul
        className="small"
        style={{ margin: 0, paddingLeft: 18, color: "var(--ink-2)" }}
      >
        <li>
          Answer on this Mac: Local AI answers from now on, and this chat stays
          Private for good.
        </li>
        <li>
          Leave it out: Cloud AI keeps answering from Standard documents only.
        </li>
      </ul>
      <div className="hstack wrap">
        <button type="button" className="btn primary" onClick={onLocal}>
          <Cpu size={14} aria-hidden="true" /> Answer on this Mac
        </button>
        <button type="button" className="btn" onClick={onLeave}>
          Leave it out
        </button>
      </div>
    </div>
  );
}

export function ConsentDeep({
  card,
  onRun,
  onStandard,
  onCancel,
}: Readonly<{
  card: Extract<RunCard, { kind: "deep_cost" }>;
  onRun: () => void;
  onStandard: () => void;
  onCancel: () => void;
}>): ReactElement {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus();
  }, []);
  const cost = money(card.estimate_usd);
  return (
    <div
      className="consent"
      role="group"
      aria-labelledby="consent-d-title"
      tabIndex={-1}
      ref={ref}
    >
      <h2 id="consent-d-title" className="h-sm hstack">
        <Gauge size={16} aria-hidden="true" /> Run this in Deep for about {cost}
        ?
      </h2>
      <p className="small" style={{ color: "var(--ink-2)" }}>
        Question: “{card.question}”
      </p>
      <p>
        Cloud AI ({card.model_name}) will read {card.documents} documents in
        this scope. That's over your ask-first limit.
      </p>
      <p className="small muted">
        This month: <span className="mono">{money(card.spent_usd)}</span> of
        your <span className="mono">{money(card.cap_usd)}</span> cap. Estimates
        are usually within 20% of the bill.
      </p>
      <div className="hstack wrap">
        <button type="button" className="btn primary" onClick={onRun}>
          <Sparkles size={14} aria-hidden="true" /> Run Deep · about {cost}
        </button>
        <button type="button" className="btn" onClick={onStandard}>
          Use Standard instead
        </button>
        <button type="button" className="btn ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
