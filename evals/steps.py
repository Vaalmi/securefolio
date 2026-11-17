"""The steps an answer goes through, told from a kept row: whether what the model was
handed held the fact, or, for a list or a total, whether the cards held the figure.
Pure functions over the question and the turn's record, so each can be tested on a
made-up row."""

import re
from pathlib import Path
from typing import Any

from evals import score


def _as_sent(item: dict[str, Any]) -> dict[str, Any]:
    """A handed passage's words as they went, after any cut."""
    return {**item, "text": " ".join(e["text"] for e in item["elements"])}


def _owed(question: dict[str, Any]) -> bool:
    return question["category"] not in score.NO_ANSWER and score.assessable(question)


def _of(path: str, items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A document's passages taken together, or None when none of them came."""
    title = Path(path).stem
    mine = [item for item in items if item["title"] == title]
    if not mine:
        return None
    return {
        "title": title,
        "text": " ".join(item["text"] for item in mine),
        "elements": [element for item in mine for element in item["elements"]],
    }


def _held(question: dict[str, Any], items: list[dict[str, Any]]) -> bool:
    """Whether these passages hold what was asked: on a question across documents,
    each document's own fact in its own passages; else the expected document's
    passages taken together, as `score.in_passage` reads them."""
    facts = question.get("passage_facts") or {}
    if facts:
        return all(
            score.holds(found["text"], fact) if (found := _of(path, items)) else False
            for path, fact in facts.items()
        )
    expected = question.get("expected_document")
    if expected:
        found = _of(expected, items)
        return found is not None and score.in_passage(question, found)
    text = " ".join(item["text"] for item in items)
    return all(score.holds(text, fact) for fact in question.get("answer_facts") or [])


def indexed_holds(question: dict[str, Any], items: list[dict[str, Any]]) -> bool | None:
    """Step 1: whether one passage of the expected documents, as indexed, holds what
    was asked, each passage read on its own, as search ranks them: on a question
    across documents, each document's own fact in one of its passages. `items` are
    every passage of those documents. None when nothing is owed."""
    if not _owed(question):
        return None
    facts = question.get("passage_facts") or {}
    if facts:
        return all(
            any(
                item["title"] == Path(path).stem and score.holds(item["text"], fact)
                for item in items
            )
            for path, fact in facts.items()
        )
    if question.get("expected_document"):
        return any(score.in_passage(question, item) for item in items)
    wanted = question.get("answer_facts") or []
    return any(all(score.holds(item["text"], f) for f in wanted) for item in items)


def handed_holds(
    question: dict[str, Any], written: dict[str, Any] | None
) -> bool | None:
    """Step 3: whether the passages the model was handed, in the text as it was sent,
    held what was asked. None when nothing is owed, when no answer was written, and
    for a list or a total, which `cards_hold` tells instead."""
    if not written or written.get("table") is not None or not _owed(question):
        return None
    return _held(question, [_as_sent(item) for item in written.get("handed") or []])


AMOUNT = re.compile(r"\$?(\d[\d,]*(?:\.\d+)?)")
SUMMED = 2  # figures it takes for a total


def _amount(text: str) -> float | None:
    found = AMOUNT.fullmatch(text.strip())
    return float(found.group(1).replace(",", "")) if found else None


def _figures(rows: list[dict[str, Any]], title: str) -> str:
    """A document's figures in the table: as the card wrote them, and as summed."""
    return " ".join(
        f"{row.get('amount_text') or ''} ${row['amount']:,.2f}"
        if isinstance(row.get("amount"), (int, float))
        else str(row.get("amount_text") or "")
        for row in rows
        if row["title"] == title
    )


def _is_total(fact: str, figures: list[str]) -> bool:
    """Whether a fact is the sum of the documents' figures: a total code works out."""
    each = [_amount(figure) for figure in figures]
    whole = _amount(fact)
    if len(each) < SUMMED or whole is None or None in each:
        return False
    return round(sum(a or 0.0 for a in each), 2) == round(whole, 2)


def _wanted(question: dict[str, Any]) -> dict[str, list[str]]:
    """Each document's own figures, as its card should hold them."""
    if question.get("passage_facts"):
        return {path: [fact] for path, fact in question["passage_facts"].items()}
    expected = question.get("expected_document")
    if not expected:
        return {}
    facts = [question["answer"]] if question.get("answer") else []
    return {expected: facts + list(question.get("answer_facts") or [])}


def cards_hold(question: dict[str, Any], written: dict[str, Any] | None) -> bool | None:
    """For a list or a total, worked out by code from the cards: whether the table
    held each document, with its figure, and a total asked for in the table's own
    total, never in a card's. A fact is a total when it is the sum of the documents'
    figures, whatever else it equals, so the table's total is checked whenever it
    gives one. This stands in for reading, search and handing over.
    A figure the answer has to work out, such as a difference, isn't the cards' to
    hold. None when the answer wasn't a list, nothing is owed, or nothing says what
    each document's card should hold."""
    table = (written or {}).get("table")
    wanted = _wanted(question)
    if table is None or not _owed(question) or not wanted:
        return None
    rows = table.get("rows") or []
    for path, facts in wanted.items():
        title = Path(path).stem
        if not any(row["title"] == title for row in rows):
            return False
        if not all(score.holds(_figures(rows, title), fact) for fact in facts):
            return False
    own = [fact for facts in wanted.values() for fact in facts]
    totals = [f for f in question.get("answer_facts") or [] if _is_total(f, own)]
    if table.get("wants_total"):
        given = f"${table.get('total', 0):,.2f}"
        return all(score.holds(given, fact) for fact in totals)
    # No total given: a sum that is also a document's own figure is held by its card.
    return all(fact in own for fact in totals)


READING = "reading"
SEARCH = "search"
HANDING_OVER = "handing_over"
CARDS = "cards"  # a list or a total: in place of reading, search and handing over
ANSWER_OR_REFUSE = "answer_or_refuse"
FACTS = "facts"
CITATIONS = "citations"
COULDNT_TELL = "couldnt_tell"
STEPS = (
    READING,
    SEARCH,
    HANDING_OVER,
    CARDS,
    ANSWER_OR_REFUSE,
    FACTS,
    CITATIONS,
    COULDNT_TELL,
)


def wrong(row: dict[str, Any]) -> bool:
    """A scored row that's known to be wrong: judged so, or, where nothing was
    judged, its facts weren't found. Failed, stopped and not-assessed rows are apart."""
    if row.get("state", score.ANSWERED) != score.ANSWERED:
        return False
    if row.get("correct") is not None:
        return row["correct"] is False
    return row.get("facts_found") is False


def _said(row: dict[str, Any]) -> str:
    """Everything the answer said: the reply as written, which Verify may have thrown
    away, its sentences, and a list's figures and total."""
    written = row.get("written") or {}
    table = written.get("table") or {}
    figures = [_figures([r], r["title"]) for r in table.get("rows") or []]
    total = f"${table['total']:,.2f}" if table.get("wants_total") else ""
    return " ".join(
        [
            str(written.get("text") or ""),
            *[str(part.get("text", "")) for part in row.get("sentences") or []],
            *figures,
            total,
        ]
    )


def _said_the_facts(question: dict[str, Any], row: dict[str, Any]) -> bool:
    """Step 5: the expected facts are in what it said. A "find" question's fact is
    the document, which only its citations can give: that's step 6."""
    said = _said(row)
    if question.get("answer_facts"):
        return all(score.holds(said, fact) for fact in question["answer_facts"])
    if question.get("answer"):
        return score.holds(said, question["answer"])
    return True


def _before_answering(row: dict[str, Any]) -> str | None:
    """Steps 1 to 3, or the cards in their place: the first that failed, None when
    each held, and COULDNT_TELL when one can't be told."""
    if (row.get("written") or {}).get("table") is not None or (
        row.get("cards_hold") is not None
    ):
        # A list or a total: its cards stand in for the first three, or it can't
        # be told when nothing says what each document's card should hold.
        held = row.get("cards_hold")
        return COULDNT_TELL if held is None else None if held else CARDS
    if row.get("indexed_holds") is None:
        return COULDNT_TELL
    if row["indexed_holds"] is False:
        return READING
    if row.get("rank") is None:
        return SEARCH
    if row.get("handed_holds") is None:
        return COULDNT_TELL
    return None if row["handed_holds"] else HANDING_OVER


def step(question: dict[str, Any], row: dict[str, Any]) -> str | None:
    """The step a wrong answer most likely went wrong at, the first in order that
    fails, or COULDNT_TELL. None for a row that isn't known to be wrong. A question
    with no answer in the library has only step 4. An answer Verify threw away was
    still written, so it counts as answered and lands at step 5 or 6."""
    if not wrong(row):
        return None
    if question["category"] in score.NO_ANSWER:
        return ANSWER_OR_REFUSE  # it answered a question that has none
    earlier = _before_answering(row)
    if earlier is not None:
        return earlier
    if row.get("kind") == "no_evidence" and not row.get("thrown_away"):
        return ANSWER_OR_REFUSE  # it refused with the fact in hand
    if not _said_the_facts(question, row):
        return FACTS
    # The facts were said: thrown away for its citations, the wrong document or page
    # cited, or, judged, a citation that didn't hold its sentence up.
    return CITATIONS


def by_step(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    """The wrong answers' question ids under each step, in the steps' order."""
    found: dict[str, list[str]] = {name: [] for name in STEPS}
    for row in rows:
        if row.get("step"):
            found[row["step"]].append(row["id"])
    return found
