"""`npm run evals:heldout`: scores the sealed set on a vault of its own and prints
aggregates only. Nothing from data/evals/answers/ is printed, by this or anything."""

import json
import os
import sys
import tempfile
from pathlib import Path

from evals import run, score

ANSWERS = Path(__file__).parents[1] / "data" / "evals" / "answers"


def report(answers: Path) -> dict:
    """On a temporary vault holding only the held-out library."""
    from data.build import seed  # noqa: PLC0415  it reads the vault's location on import

    with tempfile.TemporaryDirectory() as vault:
        os.environ["SECUREFOLIO_VAULT"] = vault
        seed.seed_from(answers / "library")
        questions = json.loads((answers / "questions.json").read_text())
        found, _claims = run.evaluate(questions)
        # The sealed hard pages sit beside the library, as `hard_pages` and its key.
        hard = run.hard_pages("hard_pages", answers)
    return {**{k: v for k, v in found.items() if k != "rows"}, **hard}


def main() -> int:
    if not (ANSWERS / "seed.json").exists():
        print("Nothing is sealed yet. `npm run evals:seal` is the owner's step.")
        return 2
    found = report(ANSWERS)
    print(json.dumps(found, indent=1))
    problems = score.misses(found)
    for problem in problems:
        print(f"MISSED: {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
