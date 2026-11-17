"""Your own set, as `npm run eval -- --questions <vault>/evals/my_questions.json` asks
it: the answers "This was wrong" saved (`backend/reports.py`). Each run reads, as they
are then, the sensitivity of every document and memory a report names: a report that
has become Private since is asked as Private, answered and scored on this Mac and never
judged by a cloud model, and one whose document is gone is listed and not asked. A
report passes only if the new answer cites the picked document at the picked place,
and holds the fact typed; its scores are reported apart, as reported failures that now
pass, since they're answers picked for being wrong."""

from typing import Any

import sqlalchemy as sa

from backend import database, reports
from backend.migrations import run as migrations
from evals import score

REPORTED = "reported"  # a report's category, beside the evaluation's own
BECAME = "became_private"  # a report made Private during the run, and why
NOT_A_MEASURE = (
    "These are answers you reported wrong, so how many now pass isn't a measure of "
    "how good SecureFolio is."
)


def is_report(question: dict[str, Any]) -> bool:
    return question.get("reported") is not None


def _present(document_ids: list[str]) -> set[str]:
    with database.read() as connection:
        return set(
            connection.execute(
                sa.select(database.documents.c.id).where(
                    database.documents.c.id.in_(document_ids)
                )
            ).scalars()
        )


def _named(question: dict[str, Any]) -> list[str]:
    """Every document a report names: the one picked, and those its answer rested on."""
    rested = question.get("rested_on") or {"documents": []}
    picked = question.get("expected_document_id")
    return [d for d in [picked, *rested["documents"]] if d]


def rests_on(question: dict[str, Any]) -> tuple[list[str], list[str]]:
    """What a report rests on: the documents it names, and the memories its answer
    used. Its question and earlier ones may quote any of them."""
    rested = question.get("rested_on") or {"memories": []}
    return _named(question), list(rested.get("memories") or [])


def _why_now(question: dict[str, Any]) -> str | None:
    """Why a report is Private, from what it names as it is now, or None."""
    document = question.get("expected_document_id")
    rested = question.get("rested_on") or {"documents": [], "memories": []}
    return reports.why_private(bool(question.get("private_chat")), document, rested)


def as_now(question: dict[str, Any]) -> dict[str, Any]:
    """A report as Private as what it names is now, read just before it's asked: one
    made Private since the run began is asked as Private, never as it was, and
    carries why, so the run lists it as having become Private."""
    if not is_report(question) or question["private"]:
        return question
    why = _why_now(question)
    return question if why is None else {**question, "private": True, BECAME: why}


def _gone_why(question: dict[str, Any], present: set[str]) -> str | None:
    """Why a report can't be asked, when a document it names is gone. A document its
    answer rested on can't have its sensitivity read once gone, so the report isn't
    asked either, rather than asked as Standard."""
    picked = question.get("expected_document_id")
    if picked and picked not in present:
        return f"{question['expected_document']} is no longer in the library"
    if any(d not in present for d in _named(question)):
        return "a document its answer rested on is no longer in the library"
    return None


def checked(
    questions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, str]]]]:
    """The questions to ask, each report Private if anything it names is Private now,
    and those naming a document that's gone left out; with the reports listed as
    having become Private since, and as gone."""
    listed: dict[str, list[dict[str, str]]] = {BECAME: [], "gone": []}
    if not any(is_report(q) for q in questions):
        return questions, listed
    migrations.upgrade()  # the vault as the app's start brings it up to date
    present = _present([d for q in questions if is_report(q) for d in _named(q)])
    asked = []
    for question in questions:
        if not is_report(question):
            asked.append(question)
            continue
        gone = _gone_why(question, present)
        if gone:
            listed["gone"].append({"id": question["id"], "why": gone})
            continue
        why = _why_now(question)
        if why and not question["private"]:
            listed[BECAME].append({"id": question["id"], "why": why})
        asked.append(
            {
                **question,
                "private": why is not None,
                # With no fact typed, what's owed is the document, at its place: as
                # a "find" question's is, so search and the steps read it so.
                "assessed_by": None if question["answer_facts"] else score.BY_DOCUMENT,
            }
        )
    return asked, listed


def _overlap(one: str, other: str) -> bool:
    a, b = reports.cells_box(one), reports.cells_box(other)
    if a is None or b is None:
        return False
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def at_the_place(place: dict[str, Any], locator: dict[str, Any]) -> bool:
    """Whether a citation's place is the report's: the page, the slide, the message,
    lines that overlap, or the sheet with a cell range that overlaps."""
    kind = place["kind"]
    if kind == "lines":
        if locator.get("kind") != "lines":
            return False
        return locator["start"] <= place["end"] and place["start"] <= locator["end"]
    if kind == "cells":
        return (
            locator.get("kind") == "sheet"
            and locator.get("sheet") == place["sheet"]
            and _overlap(str(locator.get("range") or ""), place["cells"])
        )
    return locator.get(kind) == place[kind]  # page, slide or message, by its number


def passes(question: dict[str, Any], answer: dict[str, Any], text: str) -> bool:
    """A report's pass check: a citation of the picked document at the picked place
    (any citation of it, for a kind with no numbered place), and every fact typed in
    the answer's words."""
    place = question.get("expected_place")
    cited = any(
        c.get("document_id") == question["expected_document_id"]
        and (place is None or at_the_place(place, c.get("locator") or {}))
        for c in answer.get("citations", [])
    )
    return cited and all(score.holds(text, fact) for fact in question["answer_facts"])


def section(
    rows: list[dict[str, Any]], listed: dict[str, list[dict[str, str]]]
) -> dict[str, Any]:
    """Your set's scores, apart: how many reported failures now pass, with those that
    became Private as the run began or during it."""
    asked = [r for r in rows if r.get("reported")]
    told = [r for r in asked if r["state"] == score.ANSWERED]
    meanwhile = [{"id": r["id"], "why": r[BECAME]} for r in asked if r.get(BECAME)]
    return {
        "asked": len(asked),
        "now_pass": sum(1 for r in told if r["facts_found"]),
        "failed": sum(1 for r in asked if r["state"] == score.FAILED),
        "stopped": sum(1 for r in asked if r["state"] == score.STOPPED),
        **listed,
        BECAME: [*listed[BECAME], *meanwhile],
        "note": NOT_A_MEASURE,
    }


def text_of(found: dict[str, Any]) -> str:
    """What the run prints of it."""
    lines = [
        f"{found['now_pass']} of {found['asked']} reported failures now pass. "
        + NOT_A_MEASURE
    ]
    lines += [
        f"Became Private since it was reported, so asked and scored only on this "
        f"Mac: {each['id']}, because {each['why']}."
        for each in found[BECAME]
    ]
    lines += [f"Not asked: {each['id']}, as {each['why']}." for each in found["gone"]]
    return "\n".join(lines) + "\n"
