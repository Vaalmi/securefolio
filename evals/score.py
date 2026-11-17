"""Scores an evaluation's rows, and compares a report with the baseline. No models here,
so the gate itself can be tested on made-up reports."""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

PUNCTUATION = re.compile(r"[^\w$.]+")
TARGETS = {
    "recall_at_10": 0.90,
    "citation_precision": 0.95,
    "unanswerable": 0.90,
    "table_cells": 0.95,
    "chart_values": 0.85,
    "bad_pages_read": 0.95,
}
FALSE_ALARMS = "good_pages_read_again"
MOST_FALSE_ALARMS = 0.05
SCORING_VERSION = "2026-11-13.1"  # changed whenever what a row scores changes
GUARDED = ("recall_at_10", "citation_precision")
JUDGED = ("citation_precision", "answers_correct")  # what only a judged run measures
NO_ANSWER = ("near_miss", "unanswerable")
BY_DOCUMENT = "document"  # a question's assessed_by: the fact is the document itself
ANSWERED = "answered"
FAILED = "failed"
NOT_ASSESSED = "not_assessed"  # answerable, but nothing says what's owed
STOPPED = "stopped"  # the run's spending limit stopped it
APART = (FAILED, NOT_ASSESSED, STOPPED)  # reported apart, never scored


# What a report gives a model's scores, as Settings shows them: an unjudged run has
# no answers correct and no citations supported, and gives the facts found instead.
MEASURES = (
    "answers_correct",
    "citation_precision",
    "facts_found",
    "unanswerable",
    "answerable_refused",
    "recall_at_10",
)


def measured_entry(
    report: dict[str, Any], day: str, judge: str | None
) -> dict[str, Any]:
    """A model's scores from a run's report: the day, how many questions, whether it
    was judged and by whom, and each measure the report gives."""
    judged = report.get("judged", True) is not False
    return {
        "measured_on": day,
        "questions": report.get("questions"),
        "judged": judged,
        "judge_model": judge if judged else None,
        **{name: report[name] for name in MEASURES if name in report},
    }


def normalized(text: str) -> str:
    return PUNCTUATION.sub(" ", text.casefold()).strip()


def holds(text: str, wanted: str) -> bool:
    """The wanted words in order, not as part of a longer number or word."""
    pattern = rf"(?<![\w.]){re.escape(normalized(wanted))}(?![\w])"
    return re.search(pattern, normalized(text)) is not None


def assessable(question: dict[str, Any]) -> bool:
    """Whether code can tell the facts were found: a refusal is owed, the facts are
    written down, or the fact asked for is the document itself."""
    return bool(
        question["category"] in NO_ANSWER
        or question.get("answer")
        or question.get("answer_facts")
        or question.get("assessed_by") == BY_DOCUMENT
    )


def on_page(item: dict[str, Any], page: int) -> str:
    """The passage's words on that page, even if an answer spans its elements."""
    return " ".join(
        element["text"]
        for element in item["elements"]
        if (element.get("locator") or {}).get("page") == page
    )


def in_passage(question: dict[str, Any], item: dict[str, Any]) -> bool:
    """Whether a passage of the expected document holds what was asked: its single
    fact, on its expected page when it has one, or else every fact written down. Only
    a "find" question, assessed by the document, is held by any of its passages."""
    if item["title"] != Path(question["expected_document"] or "").stem:
        return False
    answer, page = question["answer"], question["expected_page"]
    if answer is not None:
        return holds(item["text"] if page is None else on_page(item, page), answer)
    facts = question.get("answer_facts") or []
    if facts:
        return all(holds(item["text"], fact) for fact in facts)
    return question.get("assessed_by") == BY_DOCUMENT


def hit(question: dict[str, Any], ranked: list[dict[str, Any]], top: int) -> int | None:
    """The rank of the first passage holding the answer, within the top `top`. Across
    documents, the rank at which every document's passage holding its fact has come."""
    shown = ranked[:top]
    facts = question.get("passage_facts") or {}
    if facts:
        ranks = []
        for path, words in facts.items():
            title = Path(path).stem
            found = [
                at
                for at, item in enumerate(shown, start=1)
                if item["title"] == title and holds(item["text"], words)
            ]
            if not found:
                return None
            ranks.append(found[0])
        return max(ranks)
    for at, item in enumerate(shown, start=1):
        if in_passage(question, item):
            return at
    return None


def first(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The first questions, without their follow-ups."""
    return [q for q in questions if not q.get("follows")]


def set_hash(questions: list[dict[str, Any]]) -> str:
    """The question set, by its first questions alone, so a set that gains its
    follow-ups still compares with its baseline."""
    plain = json.dumps(
        [q["id"] + q["question"] for q in first(questions)], sort_keys=True
    ).encode()
    return hashlib.sha256(plain).hexdigest()


def asked_hash(questions: list[dict[str, Any]]) -> str:
    """Every question asked, follow-ups included: what a run's cost is learned on."""
    plain = json.dumps(
        [
            # A report's earlier questions, when it has any, are asked too.
            q["id"]
            + q["question"]
            + str(q.get("follows"))
            + "".join(q.get("earlier") or [])
            for q in questions
        ],
        sort_keys=True,
    ).encode()
    return hashlib.sha256(plain).hexdigest()


def verdicts(rows: list[dict[str, Any]]) -> list[bool]:
    """The judge's verdict on each citation it read in these rows: citation
    precision's own, as an uncited sentence's verdict isn't a citation's."""
    return [
        claim["supported"]
        for row in rows
        for claim in row.get("claims", [])
        if not claim.get("uncited") and "supported" in claim
    ]


def _share(found: list[bool]) -> float:
    return sum(found) / len(found) if found else 0.0


def _told(rows: list[dict[str, Any]]) -> list[bool]:
    """Whether each row was right, leaving out those that can't be told (None)."""
    return [r["correct"] for r in rows if r["correct"] is not None]


def _cited(row: dict[str, Any]) -> int:
    """The citations a row's sentences give, counted as the judge reads them: each
    sentence's own, one by one."""
    shown = {c.get("n") for c in row.get("citations") or []}
    return sum(
        1
        for part in row.get("sentences") or []
        for n in part.get("cite") or []
        if n in shown
    )


def summarize(
    rows: list[dict[str, Any]], judged: list[bool], judge: bool = True
) -> dict[str, Any]:
    """Aggregates only: this is all a held-out run may print. An unjudged run has no
    answers correct and no citation precision, rather than a zero for each. Failed
    and not-assessed rows are counted apart and enter no share."""
    every = rows
    rows = [r for r in every if r.get("state", ANSWERED) not in APART]
    answerable = [r for r in rows if r["category"] not in NO_ANSWER]
    no_answer = [r for r in rows if r["category"] in NO_ANSWER]
    with_rank = [r for r in answerable if r["rank"] is not None]
    parts = sum(r["parts"] for r in rows)
    uncited = sum(r["uncited"] for r in rows)
    found: dict[str, Any] = {
        "judged": judge,
        "questions": len(every),
        "recall_at_10": _share([r["rank"] is not None for r in answerable]),
        "mrr": sum(1 / r["rank"] for r in with_rank) / max(len(answerable), 1),
        "citations_judged": len(judged),
        "coverage": (parts - uncited) / parts if parts else 0.0,
        "facts_found": _share(
            [r["facts_found"] for r in answerable if r.get("facts_found") is not None]
        ),
        # A refusal is told by code, judged or not: whether one was owed and came.
        "unanswerable": _share(
            [r["facts_found"] for r in no_answer if r.get("facts_found") is not None]
        ),
        "answerable_refused": _share(
            [r.get("kind") == "no_evidence" for r in answerable]
        ),
        "thrown_away": sum(1 for r in rows if r.get("thrown_away")),
        # An unjudged run judged none of its citations.
        "citations_not_judged": sum(
            1 for r in rows for c in r.get("claims", []) if c.get("by_hand")
        )
        if judge
        else sum(_cited(r) for r in rows),
        **{state: sum(1 for r in every if r.get("state") == state) for state in APART},
        "not_scored": sum(1 for r in every if r["correct"] is None),
        "wrong_by_step": _by_step(every),
        "cost_usd": round(sum(r["cost_usd"] for r in every), 4),
    }
    if judge:
        found["citation_precision"] = _share(judged)
        found["answers_correct"] = _share(_told(answerable))
    return found


def private_section(rows: list[dict[str, Any]], answered_by: list[str]) -> dict:
    """Private questions, apart from every main score, under the model that answered
    them. The judge never reads one, so none is scored right or wrong."""
    scored = [r for r in rows if r.get("state", ANSWERED) not in APART]
    answerable = [r for r in scored if r["category"] not in NO_ANSWER]
    return {
        "questions": len(rows),
        "answered_by": answered_by,
        # Reported apart and never scored, as the main scores keep them.
        **{state: sum(1 for r in rows if r.get("state") == state) for state in APART},
        "recall_at_10": _share([r["rank"] is not None for r in answerable]),
        "refused": sum(1 for r in scored if r.get("kind") == "no_evidence"),
        "thrown_away": sum(1 for r in scored if r.get("thrown_away")),
        "wrong_by_step": _by_step(scored),
    }


def _by_step(rows: list[dict[str, Any]]) -> dict[str, int]:
    """How many wrong answers each step lost: counts only, as a held-out run prints."""
    counted: dict[str, int] = {}
    for row in rows:
        if row.get("step"):
            counted[row["step"]] = counted.get(row["step"], 0) + 1
    return counted


def misses(report: dict[str, float], whole: bool = True) -> list[str]:
    """Each target this report misses. On a whole run, a measure it doesn't hold is a
    miss as well: a gate that didn't run hasn't passed."""
    unjudged = report.get("judged") is False
    absent = [
        f"{name} wasn't measured"
        for name in TARGETS
        if whole and name not in report and not (unjudged and name in JUDGED)
    ]
    named = absent + [
        f"{name} is {report[name]:.3f}, under {target:.2f}"
        for name, target in TARGETS.items()
        if name in report and report[name] < target
    ]
    false_alarms = report.get(FALSE_ALARMS)
    if whole and false_alarms is None:
        named.append(f"{FALSE_ALARMS} wasn't measured")
    if false_alarms is not None and false_alarms > MOST_FALSE_ALARMS:
        named.append(
            f"good_pages_read_again is {false_alarms:.3f}, over {MOST_FALSE_ALARMS:.2f}"
        )
    return named


def regressions(report: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """A run lower than the baseline fails, even above target. Only on the same set."""
    if report.get("question_set") != baseline.get("question_set"):
        return ["This run's question set isn't the baseline's, so they can't compare."]
    return [
        f"{name} fell from {baseline[name]:.3f} to {report[name]:.3f}"
        for name in GUARDED
        if report[name] < baseline[name]
    ]
