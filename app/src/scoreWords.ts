import type { InstalledModel, Scores } from "./api";

// A measured model's scores in words, as Settings' model selectors show them
// (components/Scores.tsx shows them in full).

/** A share as a percentage with one decimal. */
export function percent(share: number): string {
  if (share === 1) return "100%";
  if (share === 0) return "0%";
  return `${(share * 100).toFixed(1)}%`;
}

/** What a model selector shows beside a measured model. */
export function shortScore(scores: Scores | null | undefined): string {
  if (!scores) return "";
  if (scores.judged && scores.answers_correct !== undefined)
    return ` · ${percent(scores.answers_correct)} correct`;
  return ` · ${percent(scores.facts_found ?? 0)} facts found`;
}

/** What the local answers job's selector shows beside each model. */
export function localShortScore(model: InstalledModel): string {
  const scores = model.measured?.scores;
  return scores ? shortScore(scores) : " · not measured";
}
