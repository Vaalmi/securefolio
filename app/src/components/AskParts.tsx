import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { ReactElement } from "react";
import {
  AlertTriangle,
  BookmarkCheck,
  BookmarkPlus,
  Calculator,
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
import type {
  Answer,
  AnswerCitation,
  AnswerFigure,
  AnswerPart,
  RunCard,
  ThreadMessage,
} from "../api";
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

/**
 * A mark's tag. Its explanation shows on hover, on keyboard focus or on a click, and
 * Escape hides it; a screen reader reads it as the tag's description. Near the
 * pane's right edge it opens leftward, and near its top it opens below the tag, so
 * the pane never cuts it off.
 */
export function TipTag({
  className,
  icon,
  label,
  tip,
}: Readonly<{
  className: string;
  icon: ReactElement;
  label: string;
  tip: string;
}>): ReactElement {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [toLeft, setToLeft] = useState(false);
  const [below, setBelow] = useState(false);
  const wrap = useRef<HTMLSpanElement>(null);
  const bubble = useRef<HTMLSpanElement>(null);
  // Escape closes it wherever focus is, a hover-opened one included.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  useLayoutEffect(() => {
    if (!open || !wrap.current || !bubble.current) return;
    const pane = (
      wrap.current.closest(".pane") ?? document.body
    ).getBoundingClientRect();
    const tag = wrap.current.getBoundingClientRect();
    setToLeft(tag.left + bubble.current.offsetWidth > pane.right - 8);
    // Above the tag, unless the pane's top edge, or the window's, would cut it.
    const top = Math.max(pane.top, 0);
    setBelow(tag.top - bubble.current.offsetHeight - 6 < top + 8);
  }, [open]);
  return (
    <span
      className="tip-wrap"
      ref={wrap}
      onMouseEnter={() => {
        setOpen(true);
      }}
      onMouseLeave={() => {
        setOpen(false);
      }}
    >
      <button
        type="button"
        className={`${className} tip-tag`}
        aria-describedby={id}
        onFocus={() => {
          setOpen(true);
        }}
        onBlur={() => {
          setOpen(false);
        }}
        onClick={() => {
          setOpen(true);
        }}
      >
        {icon}
        {label}
      </button>
      <span
        role="tooltip"
        id={id}
        ref={bubble}
        className={`tip${toLeft ? " to-left" : ""}${below ? " below" : ""}`}
        hidden={!open}
      >
        {tip}
      </span>
    </span>
  );
}

function unshownOf(part: AnswerPart): AnswerFigure[] {
  return (part.figures ?? []).filter((f) => f.state === "unshown");
}

function andList(items: string[]): string {
  if (items.length < 2) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/** What a "Worked out" tag says: a figure SecureFolio added up itself. */
export const WORKED_TIP =
  "Added up on this Mac from the cards in the table. A figure SecureFolio works out is never marked.";

/** What a "Not in its sources" tag says, naming its sentence's unshown figures. */
function unshownTip(part: AnswerPart): string {
  const figures = unshownOf(part).map((f) => f.text);
  const many = figures.length > 1;
  const where =
    (part.cite?.length ?? 0) > 1
      ? "the passages this sentence cites"
      : "the passage this sentence cites";
  return `${andList(figures)} ${many ? "aren't" : "isn't"} in ${where}. ${many ? "They" : "It"} may have been worked out by the model. Open the citation to check.`;
}

/**
 * A sentence's text, each unshown figure underlined as an uncited sentence is. The
 * backend counts offsets in characters, as Python does, so the text is cut by
 * characters too, never by UTF-16 units: an emoji before a figure doesn't move it.
 */
function MarkedText({ part }: Readonly<{ part: AnswerPart }>): ReactElement {
  const chars = Array.from(part.text);
  const cut = (from: number, to: number = chars.length) =>
    chars.slice(from, to).join("");
  const unshown = unshownOf(part).sort((a, b) => a.start - b.start);
  const pieces: ReactElement[] = [];
  let at = 0;
  for (const figure of unshown) {
    if (figure.start < at) continue;
    pieces.push(<span key={`t${String(at)}`}>{cut(at, figure.start)}</span>);
    pieces.push(
      <span key={`f${String(figure.start)}`} className="unshown">
        {cut(figure.start, figure.end)}
      </span>,
    );
    at = figure.end;
  }
  pieces.push(<span key={`t${String(at)}`}>{cut(at)}</span>);
  return <>{pieces}</>;
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
            <MarkedText part={part} />
          </span>
          {part.uncited && (
            <TipTag
              className="uncited-tag"
              icon={<AlertTriangle size={10} aria-hidden="true" />}
              label="Uncited"
              tip="No passage given to the model supports this sentence."
            />
          )}
          {part.cite?.map((n) => {
            const citation = citations.find((c) => c.n === n);
            // The sentence goes with it, so the citation opens at its figures.
            const key = `${messageId}-${String(n)}-s${String(i)}`;
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
          {unshownOf(part).length > 0 && (
            <TipTag
              className="uncited-tag"
              icon={<AlertTriangle size={10} aria-hidden="true" />}
              label="Not in its sources"
              tip={unshownTip(part)}
            />
          )}
          {(part.figures ?? []).some((f) => f.state === "worked") && (
            <TipTag
              className="badge outline worked-tag"
              icon={<Calculator size={12} aria-hidden="true" />}
              label="Worked out"
              tip={WORKED_TIP}
            />
          )}
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

export function AnswerActions({
  message,
  canDeep,
  onDeep,
  onRetry,
}: Readonly<{
  message: ThreadMessage;
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
          // Made by the backend, with its marks in words, as a note and a
          // follow-up read it.
          void navigator.clipboard
            .writeText(message.copy_text ?? "")
            .then(() => {
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
