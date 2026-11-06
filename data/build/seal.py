"""`npm run evals:seal`: a maintainer-only step. Draws a random seed once and writes
the whole held-out set under data/evals/answers/, and nowhere else. Sealing the
answer keys is never part of the build, so the build never runs this."""

import argparse
import json
import secrets
from pathlib import Path

from data.build import hard_pages, library

ANSWERS = Path(__file__).parent.parent / "evals" / "answers"
HELD_OUT_BAD_PAGES = 20
HELD_OUT_GOOD_PAGES = 40


class AlreadySealed(Exception):
    """The set exists; re-sealing would change what was measured."""


class NotReady(Exception):
    """The library's generator isn't ready; sealing part of a set is refused."""


def seal(target: Path, reseed: bool = False) -> None:
    seed_file = target / "seed.json"
    if seed_file.exists() and not reseed:
        raise AlreadySealed(f"{target} is sealed already. Pass --reseed to replace it.")
    if not library.READY:
        raise NotReady(
            "The held-out library's generator isn't built yet; nothing was sealed."
        )
    target.mkdir(parents=True, exist_ok=True)
    seed = secrets.randbits(63)
    seed_file.write_text(json.dumps({"seed": seed}), encoding="utf-8")
    hard_pages.build(
        target / "hard_pages",
        seed,
        target / "hard_pages.json",
        HELD_OUT_BAD_PAGES,
        HELD_OUT_GOOD_PAGES,
    )
    library.build_held_out(target / "library", seed, target / "questions.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reseed", action="store_true")
    arguments = parser.parse_args()
    seal(ANSWERS, arguments.reseed)
    print("Sealed. Nothing in data/evals/answers/ was printed.")


if __name__ == "__main__":
    main()
