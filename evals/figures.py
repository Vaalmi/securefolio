"""`npm run eval -- --figures-from <run>`: the figure check over a finished kept run's
rows, as each answer was written and with the passages as they were sent. It asks no
model and opens no vault. Each marked figure goes to a sheet, to be sorted by hand:
shown in its passage after all (a wrong mark), worked out by the model, or not in the
sources. `--figures-sorted <sheet>` reads a sorted sheet back and gives the sorts. The
sheets hold document text, so they stay in the repo's cache, outside git, and nothing
here logs."""

import csv
import json
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from agent import figures
from agent.figures import Passage
from evals import kept as keeping

SHEETS = "figures"
COLUMNS: tuple[str, ...] = (
    "question",
    "part",
    "kind",
    "figure",
    "sentence",
    "passages",
    "sort",
    "cause",
)
SORTS = {
    "shown": "shown in its passage after all",
    "model": "worked out by the model",
    "absent": "not in the sources",
}
WRONG_MARKS_TARGET = Decimal(1) / Decimal(
    50
)  # a proposed value, set after a count by hand


class CantCount(SystemExit):
    """This kept run can't be counted."""


def passages_of(row: dict[str, Any]) -> tuple[dict[int, Passage], int]:
    """Each citation's passage as the model was given it, joined by its document's
    title and its place, and by its words when one place gave more than one element.
    Returns the passages and how many citations couldn't be joined."""
    handed = (row.get("written") or {}).get("handed") or []
    given = [
        (evidence.get("title", ""), element)
        for evidence in handed
        for element in evidence.get("elements", [])
    ]
    found: dict[int, Passage] = {}
    missed = 0
    for citation in row.get("citations", []):
        same = [
            (title, element)
            for title, element in given
            if title == citation.get("title")
            and element.get("locator") == citation.get("locator")
        ]
        if len(same) > 1:
            quote = citation.get("quote") or ""
            same = [(t, e) for t, e in same if quote and quote in e.get("text", "")]
        if len(same) == 1:
            title, element = same[0]
            found[int(citation["n"])] = Passage(element.get("text", ""), title)
        else:
            missed += 1
    return found, missed


def worked_of(row: dict[str, Any]) -> list[Decimal]:
    return figures.worked_of((row.get("written") or {}).get("table"))


def marked(part: dict[str, Any], found: list[dict[str, Any]]) -> bool:
    """A sentence that would carry a mark: an unshown figure, or no citation."""
    return bool(part.get("uncited")) or any(
        f["state"] == figures.UNSHOWN for f in found
    )


def count(kept: keeping.Kept) -> dict[str, Any]:
    """The check over a finished kept run's rows."""
    if kept.state != keeping.FINISHED:
        raise CantCount(f"{kept.folder.name} isn't a finished run.")
    return counted(kept.rows(), kept.folder.name, kept.manifest.get("answer_model"))


def counted(rows: list[dict[str, Any]], name: str, model: Any) -> dict[str, Any]:
    """The check over every row: what it found, the follow-ups whose earlier answer
    carries a mark, and its slowest time on any one answer."""
    by_id = {row["id"]: row for row in rows}
    checked = Counter()
    marked_by_kind = Counter()
    lines: list[dict[str, Any]] = []
    missed = 0
    slowest = 0.0
    carries_mark: set[str] = set()
    for row in rows:
        passages, not_joined = passages_of(row)
        missed += not_joined
        parts = row.get("sentences") or []
        started = time.perf_counter()
        found = figures.check(parts, passages, worked_of(row))
        slowest = max(slowest, time.perf_counter() - started)
        for at, (part, figures_found) in enumerate(zip(parts, found, strict=True)):
            if marked(part, figures_found):
                carries_mark.add(row["id"])
            for figure in figures_found:
                checked[figure["kind"]] += 1
                if figure["state"] != figures.UNSHOWN:
                    continue
                marked_by_kind[figure["kind"]] += 1
                cited = [passages[n] for n in part.get("cite", []) if n in passages]
                lines.append(
                    {
                        "question": row["id"],
                        "part": at,
                        "kind": figure["kind"],
                        "figure": figure["text"],
                        "sentence": part.get("text", "").strip(),
                        "passages": "\n---\n".join(
                            f"{p.title}: {p.text}" for p in cited
                        ),
                        "sort": "",
                        "cause": "",
                    }
                )
    follow_ups = sorted(
        row["id"]
        for row in rows
        if row.get("follows") in carries_mark and row.get("follows") in by_id
    )
    return {
        "run": name,
        "model": model,
        "by_kind": {
            kind: {"checked": checked[kind], "marked": marked_by_kind[kind]}
            for kind in figures.KINDS
        },
        "not_joined": missed,
        "follow_ups_changed": follow_ups,
        "slowest_ms": round(slowest * 1000, 2),
        "lines": lines,
    }


def reports_of(kept: keeping.Kept) -> Path:
    """Where a kept run's evaluation records are: the repo's cache for the synthetic
    library, or the vault's evals folder for a set of the owner's own. A sheet goes
    there too, beside the run it came from, so the owner's text never leaves it."""
    return kept.folder.resolve().parent.parent


class SheetSorted(Exception):
    """A run's sheet already holds sorts by hand, which counting again would wipe."""


def _sorted_by_hand(sheet: Path) -> bool:
    if not sheet.exists():
        return False
    with sheet.open(newline="") as rows:
        return any(
            (line.get("sort") or "").strip() or (line.get("cause") or "").strip()
            for line in csv.DictReader(rows)
        )


def write_sheet(counted: dict[str, Any], root: Path) -> Path:
    """The marked figures, one row each, with an empty sort to fill by hand, and the
    counts beside it, in `root`'s figures folder. A sheet already sorted by hand is
    never written over: counting its run again is refused until it's moved."""
    folder = root / SHEETS
    folder.mkdir(parents=True, exist_ok=True)
    sheet = folder / f"{counted['run']}.csv"
    if _sorted_by_hand(sheet):
        raise SheetSorted(
            f"{sheet} holds figures sorted by hand; move it to count {counted['run']} "
            "again."
        )
    with sheet.open("w", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(counted["lines"])
    summary = {k: v for k, v in counted.items() if k != "lines"}
    sheet.with_suffix(".json").write_text(json.dumps(summary, indent=1))
    return sheet


def section(counted: dict[str, Any], sheet: Path) -> dict[str, Any]:
    """What a run's report says about its figures: the counts, and its sheet."""
    return {**{k: v for k, v in counted.items() if k != "lines"}, "sheet": str(sheet)}


def own_section(kept: keeping.Kept) -> dict[str, Any]:
    """A run's own figures, as it ends: its own rows counted, and its own sheet written
    beside it. Every way a kept run ends gives its report this, so none carries
    another run's figures or none; a kept run refuses to end without it."""
    found = counted(kept.rows(), kept.folder.name, kept.manifest.get("answer_model"))
    return section(found, write_sheet(found, reports_of(kept)))


def sorted_report(sheet: Path) -> dict[str, Any]:
    """The sorts of a sheet sorted by hand, per kind, and wrong marks against the
    figures checked. A row left unsorted is counted as such, never guessed."""
    summary = json.loads(sheet.with_suffix(".json").read_text())
    with sheet.open(newline="") as rows:
        lines = list(csv.DictReader(rows))
    by_kind: dict[str, Counter[str]] = {kind: Counter() for kind in figures.KINDS}
    for line in lines:
        sort = line["sort"].strip()
        by_kind[line["kind"]][sort if sort in SORTS else "unsorted"] += 1
    checked = sum(k["checked"] for k in summary["by_kind"].values())
    wrong = sum(kind["shown"] for kind in by_kind.values())
    return {
        **summary,
        "sorts": {kind: dict(sorts) for kind, sorts in by_kind.items()},
        "wrong_marks": wrong,
        "checked": checked,
        "wrong_mark_rate": float(Decimal(wrong) / checked) if checked else 0.0,
        "within_target": checked == 0 or Decimal(wrong) / checked <= WRONG_MARKS_TARGET,
        "unsorted": sum(kind["unsorted"] for kind in by_kind.values()),
    }


def text_of(counted: dict[str, Any], sheet: Path) -> str:
    kinds = ", ".join(
        f"{kind} {v['marked']} of {v['checked']}"
        for kind, v in counted["by_kind"].items()
    )
    follow_ups = ", ".join(counted["follow_ups_changed"]) or "none"
    return (
        f"Figures marked, of those checked: {kinds}.\n"
        f"Citations not joined to a passage: {counted['not_joined']}.\n"
        f"Follow-ups whose earlier answer carries a mark: {follow_ups}.\n"
        f"Slowest check of one answer: {counted['slowest_ms']} ms.\n"
        f"Sheet to sort by hand: {sheet}\n"
    )
