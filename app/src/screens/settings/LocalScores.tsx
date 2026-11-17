import type { ReactElement } from "react";

import type { InstalledModel } from "../../api";
import { ScoresBlock } from "../../components/Scores";

// A local model's scores, measured on this Mac and kept in the library for the build
// that was measured. Only the answers job is
// measured: cards and figures show none.

function short(build: string | null | undefined): string {
  return (build ?? "unknown").slice(0, 8);
}

/** Under the answers job's words: "Not measured", and why, when there are no scores. */
export function LocalScoresNote({
  model,
}: Readonly<{ model: InstalledModel }>): ReactElement | null {
  const measured = model.measured;
  if (measured?.scores) return null;
  const changed = measured?.changed;
  return (
    <span className="hstack wrap" style={{ gap: 6 }}>
      <span
        className="badge outline"
        title="npm run eval -- --model local:<tag> measures it on this Mac"
      >
        Not measured
      </span>
      {changed && (
        <span>
          {model.name} has changed since it was measured: build{" "}
          <span className="mono">{short(changed.measured)}</span> was measured,{" "}
          <span className="mono">{short(changed.installed)}</span> is installed.
          Its old scores aren't shown.
        </span>
      )}
    </span>
  );
}

/** Under the answers job: the chosen model's scores, for the build installed. */
export function LocalScores({
  model,
  id,
}: Readonly<{ model: InstalledModel; id: string }>): ReactElement | null {
  const scores = model.measured?.scores;
  if (!scores) return null;
  return (
    <ScoresBlock
      scores={scores}
      where={`measured on this Mac, build ${short(model.measured?.build)},`}
      id={id}
    />
  );
}
