"""`npm run evals:seal`: a maintainer-only step. Draws a random seed once and writes
the whole held-out set under data/evals/answers/, and nowhere else. Sealing the
answer keys is never part of the build, so the build never runs this. --add-follow-ups
adds the follow-ups to a set sealed before it had them, from its stored seed."""

import argparse
import json
import secrets
from pathlib import Path

from data.build import hard_pages, library

ANSWERS = Path(__file__).parent.parent / "evals" / "answers"
HELD_OUT_BAD_PAGES = 20
HELD_OUT_GOOD_PAGES = 40
HELD_OUT_PREFIX = library.HELD_OUT_PREFIX


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


def add_follow_ups(target: Path) -> int:
    """Adds the follow-ups to a set sealed before it had them, from its stored seed,
    changing nothing else in it: the library and the keys stay as they were."""
    seed = json.loads((target / "seed.json").read_text(encoding="utf-8"))["seed"]
    return library.add_follow_ups(target / "questions.json", seed, HELD_OUT_PREFIX)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reseed", action="store_true")
    parser.add_argument(
        "--add-follow-ups",
        action="store_true",
        help="Add the follow-ups to a set sealed before it had them.",
    )
    arguments = parser.parse_args()
    if arguments.add_follow_ups:
        added = add_follow_ups(ANSWERS)
        print(f"Added {added} follow-ups. Nothing in data/evals/answers/ was printed.")
        return
    seal(ANSWERS, arguments.reseed)
    print("Sealed. Nothing in data/evals/answers/ was printed.")


if __name__ == "__main__":
    main()
