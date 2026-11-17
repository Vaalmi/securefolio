import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { ReactElement } from "react";
import { Flag } from "lucide-react";

import { getDocument, getReportPrivacy, reportAnswer } from "../api";
import type { Answer, AnswerCitation, ReportBody } from "../api";
import { missingOf, PLACE_OF, placeOf, placeText } from "../reportPlace";
import type { PlaceKind, Where } from "../reportPlace";
import { toast } from "../toast";
import { useAction } from "../useAction";
import { useLoad } from "../useLoad";
import {
  DocumentPicker,
  FactField,
  Footer,
  NoteField,
  PlaceField,
  WhereChoice,
} from "./ReportFields";

/** The citation the form starts at: the answer's first, when it cites any. */
function firstCited(answer: Answer): AnswerCitation | undefined {
  return answer.kind === "no_evidence" ? undefined : answer.citations?.[0];
}

/** The report as the backend takes it: no place for a document without one. */
function said(
  chosen: string | null,
  kind: PlaceKind | undefined,
  loc: string,
  fact: string,
  note: string,
): ReportBody {
  const place = chosen && kind ? placeOf(kind, loc) : null;
  return { document_id: chosen, place, fact, note };
}

/**
 * "This was wrong": the short form that saves an answer as a question in the
 * owner's own set, in the library's evaluation folder. It stays on this Mac.
 * Escape cancels it.
 */
export function WrongReport({
  threadId,
  messageId,
  answer,
  before,
  onSaved,
  onCancel,
}: Readonly<{
  threadId: string;
  messageId: string;
  answer: Answer;
  before: number; // the questions before this one in the chat
  onSaved: () => void;
  onCancel: () => void;
}>): ReactElement {
  const id = useId();
  const form = useRef<HTMLFormElement>(null);
  useEffect(() => {
    const element = form.current;
    if (!element) return;
    element.focus({ preventScroll: true });
    element.scrollIntoView({ block: "end" });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
      }
    };
    element.addEventListener("keydown", onKey);
    return () => {
      element.removeEventListener("keydown", onKey);
    };
  }, [onCancel]);
  const first = firstCited(answer);
  const [where, setWhere] = useState<Where>("doc");
  const [docId, setDocId] = useState(first?.document_id ?? "");
  const [loc, setLoc] = useState(placeText(first?.locator));
  const [fact, setFact] = useState("");
  const [note, setNote] = useState("");
  const [gap, setGap] = useState<string>();
  const picked = useLoad(
    () =>
      docId
        ? getDocument(docId).then((d) => d.document)
        : Promise.resolve(null),
    docId,
  ).data;
  const chosen = where === "doc" ? docId || null : null;
  const why = useLoad(
    () =>
      getReportPrivacy(threadId, messageId, chosen).then((said) => said.why),
    chosen ?? "",
  ).data;
  const kind = picked ? PLACE_OF[picked.file_kind] : undefined;

  const saving = useAction(
    () =>
      reportAnswer(threadId, messageId, said(chosen, kind, loc, fact, note)),
    () => {
      toast("Saved as a question in your own set, on this Mac.");
      onSaved();
    },
  );
  const problem = gap ?? saving.error;

  function save(): void {
    const missing = missingOf(where, Boolean(picked), kind, loc, fact);
    setGap(missing);
    if (!missing) void saving.run();
  }

  return (
    <form
      ref={form}
      className="consent report"
      tabIndex={-1}
      aria-labelledby={`${id}-title`}
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <h2 id={`${id}-title`} className="h-sm hstack">
        <Flag size={16} aria-hidden="true" /> This was wrong
      </h2>
      <WhereChoice id={id} where={where} onChange={setWhere} />
      {where === "doc" && (
        <div className="report-fields">
          <DocumentPicker
            id={id}
            answer={answer}
            docId={docId}
            onPick={(next) => {
              setDocId(next);
              const citing = answer.citations?.find(
                (c) => c.document_id === next,
              );
              setLoc(placeText(citing?.locator));
            }}
          />
          {kind && (
            <PlaceField
              id={id}
              kind={kind}
              pages={picked?.pages ?? null}
              loc={loc}
              onChange={setLoc}
            />
          )}
        </div>
      )}
      <FactField
        id={id}
        where={where}
        kind={kind}
        fact={fact}
        onChange={setFact}
      />
      <NoteField id={id} note={note} onChange={setNote} />
      {problem && (
        <p className="small needs-key" role="alert">
          {problem}
        </p>
      )}
      <Footer
        why={why}
        before={before}
        busy={saving.busy}
        onCancel={onCancel}
      />
    </form>
  );
}

/** "This was wrong" under an answer, opening its form. Once saved it says
 * "Reported wrong", and an answer has one report. Focus comes back to it when the
 * form closes. */
export function ReportAction({
  threadId,
  messageId,
  answer,
  before,
  reportedAlready,
}: Readonly<{
  threadId: string;
  messageId: string;
  answer: Answer;
  before: number;
  reportedAlready: boolean;
}>): ReactElement {
  const [reporting, setReporting] = useState(false);
  const [reported, setReported] = useState(reportedAlready);
  const button = useRef<HTMLButtonElement>(null);
  // Stable, so the form's Escape listener and its first focus are set up once.
  const close = useCallback(() => {
    setReporting(false);
    window.setTimeout(() => button.current?.focus(), 0);
  }, []);
  const done = reported || reportedAlready;
  return (
    <>
      <div className="hstack wrap">
        <button
          ref={button}
          type="button"
          className="btn sm ghost"
          aria-pressed={done}
          aria-expanded={reporting}
          aria-disabled={done}
          onClick={() => {
            if (!done) setReporting((open) => !open);
          }}
        >
          <Flag size={12} aria-hidden="true" />{" "}
          {done ? "Reported wrong" : "This was wrong"}
        </button>
      </div>
      {reporting && (
        <WrongReport
          threadId={threadId}
          messageId={messageId}
          answer={answer}
          before={before}
          onSaved={() => {
            setReported(true);
            close();
          }}
          onCancel={close}
        />
      )}
    </>
  );
}
