import type { ReactElement } from "react";

import type { Scores } from "../api";
import { fmtDate } from "../format";
import { percent } from "../scoreWords";

// A measured model's scores, under it where it's chosen in Settings.

/** The spec's targets, where it sets one. */
const TARGETS = { citations: 0.95, refused: 0.9 };

function Under({
  share,
  target,
}: Readonly<{ share: number; target: number }>): ReactElement | null {
  if (share >= target) return null;
  return (
    <span className="under">
      under the {percent(target).replace(".0", "")} target
    </span>
  );
}

function judgedBy(scores: Scores): string {
  if (!scores.judged) return "citations not judged";
  const judge = scores.judge_name ?? scores.judge_model ?? "a judge";
  return `citations judged by ${judge}`;
}

function Correct({ scores }: Readonly<{ scores: Scores }>): ReactElement {
  if (scores.judged && scores.answers_correct !== undefined)
    return (
      <div title="Every fact the question asks for, and every sentence sound, judged">
        <dt>Answers correct</dt>
        <dd>{percent(scores.answers_correct)}</dd>
      </div>
    );
  return (
    <div title="The facts the question asks for are in the answer, checked by code; with no judge, nothing says the rest is sound">
      <dt>Facts found</dt>
      <dd>{percent(scores.facts_found ?? 0)}</dd>
    </div>
  );
}

function Supported({ scores }: Readonly<{ scores: Scores }>): ReactElement {
  const share = scores.citation_precision;
  return (
    <div title="Citations whose passage supports the sentence, judged">
      <dt>Citations supported</dt>
      <dd>
        {share === undefined ? (
          <span className="under" style={{ marginLeft: 0 }}>
            not judged
          </span>
        ) : (
          <>
            {percent(share)}
            <Under share={share} target={TARGETS.citations} />
          </>
        )}
      </dd>
    </div>
  );
}

export function ScoresBlock({
  scores,
  where,
  id,
}: Readonly<{ scores: Scores; where: string; id: string }>): ReactElement {
  const refused = scores.unanswerable ?? 0;
  const questions =
    scores.questions === null ? "" : `, ${String(scores.questions)} questions`;
  return (
    <div className="scores" role="group" aria-labelledby={`${id}-h`}>
      <span id={`${id}-h`} className="xs" style={{ color: "var(--ink-2)" }}>
        Scores {where} on {fmtDate(scores.measured_on)}
        {questions} · {judgedBy(scores)}
      </span>
      <dl className="score-grid">
        <Correct scores={scores} />
        <Supported scores={scores} />
        <div title="Questions the library has no answer to, answered “I couldn't find enough evidence”">
          <dt>No-answer questions refused</dt>
          <dd>
            {percent(refused)}
            <Under share={refused} target={TARGETS.refused} />
          </dd>
        </div>
        <div title="Questions the library does answer, refused anyway">
          <dt>Answerable questions refused</dt>
          <dd>{percent(scores.answerable_refused ?? 0)}</dd>
        </div>
      </dl>
    </div>
  );
}
