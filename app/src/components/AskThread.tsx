import type { ReactElement } from "react";
import { BrainCircuit, Cpu, Search } from "lucide-react";

import { getDocument, updateThread } from "../api";
import type {
  Answer,
  AskRoute,
  Thread,
  ThreadMessage,
  ThreadScope,
} from "../api";
import { toast } from "../toast";
import { PrivateBadge } from "../ui";
import { useAction } from "../useAction";
import { useLoad } from "../useLoad";
import { useThreadsChanged } from "../useThreadsChanged";
import { ListAnswer, NoEvidence } from "./AskAnswers";
import { ChatMenu } from "./ChatMenu";
import { AnswerActions, AnswerBody, AnswerMeta, Steps } from "./AskParts";

function emptyTitle(count: number): string {
  if (count === 0) return "Ask your library";
  return count === 1
    ? "Ask about this document"
    : `Ask about these ${String(count)} documents`;
}

export function EmptyChat({
  scope,
}: Readonly<{ scope: ThreadScope }>): ReactElement {
  const ids = scope.kind === "documents" ? scope.ids : [];
  const documents = useLoad(
    () => Promise.all(ids.slice(0, 20).map((id) => getDocument(id))),
    ids.join(","),
  );
  const includesPrivate = (documents.data ?? []).some(
    (d) => d.document.sensitivity === "private",
  );
  return (
    <div className="empty">
      <span className="empty-icon">
        <Search size={20} aria-hidden="true" />
      </span>
      <h2>{emptyTitle(ids.length)}</h2>
      <p className="muted" style={{ maxWidth: 440 }}>
        {ids.length > 0
          ? "Answers use only these documents, and every claim links to its page. The scope stays as it is unless you change it."
          : "Every claim in an answer links to the page it came from. Private folders stay out unless you include them."}
      </p>
      {ids.length > 0 && (
        <ul className="small" style={{ textAlign: "left", margin: 0 }}>
          {documents.data?.map((d) => (
            <li key={d.document.id}>{d.document.title}</li>
          ))}
        </ul>
      )}
      {includesPrivate && (
        <p className="small">
          Includes Private documents, so the local model on this Mac answers and
          this chat stays Private.
        </p>
      )}
    </div>
  );
}

export function AnswerTurn({
  message,
  answer,
  thread,
  deepCloud,
  activeCite,
  onCite,
  onRetry,
  onDeep,
  onAskLater,
  onIncludePrivate,
}: Readonly<{
  message: ThreadMessage;
  answer: Answer;
  thread: Thread;
  deepCloud: boolean; // Re-run in Deep shows only when Deep would go to the cloud
  activeCite: string | null;
  onCite: (key: string) => void;
  onRetry: () => void;
  onDeep: () => void;
  onAskLater: () => void;
  onIncludePrivate: () => void;
}>): ReactElement {
  const noEvidence = answer.kind === "no_evidence";
  return (
    <article className="answer" aria-label="Answer">
      {answer.kind === "answer" && (
        <AnswerBody
          answer={answer}
          messageId={message.id}
          activeCite={activeCite}
          onCite={onCite}
        />
      )}
      {answer.kind === "list" && (
        <ListAnswer
          answer={answer}
          messageId={message.id}
          activeCite={activeCite}
          onCite={onCite}
        />
      )}
      {noEvidence && (
        <NoEvidence
          answer={answer}
          scopeLabel={thread.scope.label}
          onPreview={onCite}
          onAskLater={onAskLater}
          onIncludePrivate={thread.private_tainted ? null : onIncludePrivate}
        />
      )}
      {(answer.left_out?.length ?? 0) > 0 && (
        <p className="small" role="note">
          Couldn't use: {answer.left_out?.join(", ")}.
        </p>
      )}
      <AnswerMeta
        answer={answer}
        privateThread={thread.private_tainted}
        when={message.created_at}
      />
      {!noEvidence && (
        <AnswerActions
          message={message}
          answer={answer}
          canDeep={
            deepCloud && !thread.private_tainted && answer.mode !== "deep"
          }
          onDeep={onDeep}
          onRetry={onRetry}
        />
      )}
    </article>
  );
}

export function Header({
  thread,
  onChanged,
}: Readonly<{ thread: Thread; onChanged: () => void }>): ReactElement {
  const change = useAction(
    (changes: Parameters<typeof updateThread>[1]) =>
      updateThread(thread.id, changes),
    onChanged,
  );
  useThreadsChanged(onChanged);
  return (
    <div className="hstack" style={{ gap: 6 }}>
      {thread.private_tainted && <PrivateBadge label="Private chat" />}
      <button
        type="button"
        className="btn sm"
        aria-pressed={!thread.memory_off}
        title="Whether SecureFolio may remember preferences and facts from this chat"
        onClick={() => {
          void change.run({ memory_off: !thread.memory_off });
          toast(
            thread.memory_off
              ? "Memory is on for this chat."
              : "Memory is off for this chat. Nothing from it will be remembered.",
          );
        }}
      >
        <BrainCircuit size={12} aria-hidden="true" />
        {thread.memory_off ? "Memory off" : "Memory on"}
      </button>
      <ChatMenu thread={thread} label="Chat actions" />
      {change.error && (
        <span role="alert" className="small">
          {change.error}
        </span>
      )}
    </div>
  );
}

export function Pending({
  question,
  steps,
  text,
}: Readonly<{
  question: string | undefined;
  steps: { name: string; detail: string }[];
  text: string;
}>): ReactElement {
  return (
    <div className="stack" style={{ gap: 12 }}>
      {question && <div className="q-bubble">{question}</div>}
      <article
        className="answer"
        aria-label="Answer in progress"
        aria-busy="true"
      >
        <Steps steps={steps} />
        {text && <p>{text}</p>}
      </article>
    </div>
  );
}

// What leaves the Mac for the next answer, as the route says who gives it.
export function Foot({
  route,
}: Readonly<{ route: AskRoute | undefined }>): ReactElement | null {
  if (!route) return null;
  return (
    <div className="xs muted" style={{ textAlign: "center", marginTop: 6 }}>
      {route.local && route.helper_local && (
        <span className="hstack" style={{ justifyContent: "center", gap: 4 }}>
          <Cpu size={11} aria-hidden="true" /> Local AI answers on this Mac.
          Nothing in this chat leaves your Mac.
        </span>
      )}
      {route.local && !route.helper_local && (
        // The answer stays here, but its helper steps go to the cloud.
        <span className="hstack" style={{ justifyContent: "center", gap: 4 }}>
          <Cpu size={11} aria-hidden="true" /> Local AI answers on this Mac.
          Cloud AI ({route.helper_name}) runs its helper steps, so it gets this
          question and recent turns. Nothing from Private documents.
        </span>
      )}
      {!route.local && (
        <details className="disclosure">
          <summary>
            Cloud AI ({route.name}) gets this question, recent turns and the
            passages behind the answer. Nothing from Private documents.
          </summary>
          <span style={{ display: "block", textAlign: "left", marginTop: 4 }}>
            What leaves your Mac for an answer: the question; this chat's recent
            turns and its summary; memories that apply; the passages and
            document cards the answer uses; and, on a retry, the titles of
            near-miss documents. Every call is listed in Settings, Spending and
            cloud log.
          </span>
        </details>
      )}
    </div>
  );
}
