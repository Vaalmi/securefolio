"""`npm run evals:heldout -- --limit-usd N`: scores the sealed set on a vault of its
own and prints aggregates only, spending at most N. Its expected cost is printed first;
--estimate-only prints that and stops. Nothing from data/evals/answers/ is printed, by
this or anything."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from evals import run, score

ANSWERS = Path(__file__).parents[1] / "data" / "evals" / "answers"
KEPT = "evals/runs"  # none is kept: the temporary vault goes with the run
HELD_BACK = (  # the sealed set's own, never printed
    "rows",
    "wrong_questions_by_step",
    "private_wrong_questions_by_step",
    "follow_up_wrong_questions_by_step",
)


def report(
    answers: Path, limit_usd: float | None, estimate_only: bool = False
) -> dict | None:
    """On a temporary vault holding only the held-out library. Its expected cost is
    printed before it starts: a figure, with nothing of the set in it. None when
    `estimate_only`."""
    from data.build import seed  # noqa: PLC0415  it reads the vault's location on import

    with tempfile.TemporaryDirectory() as vault:
        os.environ["SECUREFOLIO_VAULT"] = vault
        seed.seed_from(answers / "library")
        questions = json.loads((answers / "questions.json").read_text())
        judge = run.judging(None, None)
        expected = run.expected_cost(questions, None, judge, Path(vault) / KEPT)
        print(expected.line())
        if estimate_only:
            return None
        refused = run.unlimited(expected, limit_usd)
        if refused:
            raise run.NoLimit(refused)
        found, _claims = run.evaluate(questions, judge=judge, limit_usd=limit_usd)
        if found.get("stopped_at_limit"):
            return {k: v for k, v in found.items() if k not in HELD_BACK}
        # The sealed hard pages sit beside the library, as `hard_pages` and its key.
        hard = run.hard_pages("hard_pages", answers)
    return {**{k: v for k, v in found.items() if k not in HELD_BACK}, **hard}


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit-usd", type=float)
    parser.add_argument("--estimate-only", action="store_true")
    given = parser.parse_args(arguments)
    if not (ANSWERS / "seed.json").exists():
        print("Nothing is sealed yet. `npm run evals:seal` is the owner's step.")
        return 2
    found = report(ANSWERS, given.limit_usd, given.estimate_only)
    if found is None:
        return 0
    print(json.dumps(found, indent=1))
    if found.get("stopped_at_limit"):
        print(f"Stopped at the limit of ${found['limit_usd']:.2f}.")
        return 1
    problems = score.misses(found)
    for problem in problems:
        print(f"MISSED: {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
