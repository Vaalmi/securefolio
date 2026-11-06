"""Scores an evaluation's rows, and compares a report with the baseline. No models here,
so the gate itself can be tested on made-up reports."""

import hashlib
import json
import re
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
GUARDED = ("recall_at_10", "citation_precision")
NO_ANSWER = ("near_miss", "unanswerable")


def normalized(text: str) -> str:
    return PUNCTUATION.sub(" ", text.casefold()).strip()


def holds(text: str, wanted: str) -> bool:
    """The wanted words in order, not as part of a longer number or word."""
    pattern = rf"(?<![\w.]){re.escape(normalized(wanted))}(?![\w])"
    return re.search(pattern, normalized(text)) is not None


def set_hash(questions: list[dict[str, Any]]) -> str:
    plain = json.dumps(
        [q["id"] + q["question"] for q in questions], sort_keys=True
    ).encode()
    return hashlib.sha256(plain).hexdigest()


def _share(found: list[bool]) -> float:
    return sum(found) / len(found) if found else 0.0


def summarize(rows: list[dict[str, Any]], judged: list[bool]) -> dict[str, float]:
    """Aggregates only: this is all a held-out run may print."""
    answerable = [r for r in rows if r["category"] not in NO_ANSWER]
    no_answer = [r for r in rows if r["category"] in NO_ANSWER]
    with_rank = [r for r in answerable if r["rank"] is not None]
    parts = sum(r["parts"] for r in rows)
    uncited = sum(r["uncited"] for r in rows)
    return {
        "questions": len(rows),
        "recall_at_10": _share([r["rank"] is not None for r in answerable]),
        "mrr": sum(1 / r["rank"] for r in with_rank) / max(len(answerable), 1),
        "citation_precision": _share(judged),
        "citations_judged": len(judged),
        "coverage": (parts - uncited) / parts if parts else 0.0,
        "answers_correct": _share([r["correct"] for r in answerable]),
        "unanswerable": _share([r["correct"] for r in no_answer]),
        "cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
    }


def misses(report: dict[str, float], whole: bool = True) -> list[str]:
    """Each target this report misses. On a whole run, a measure it doesn't hold is a
    miss as well: a gate that didn't run hasn't passed."""
    absent = [
        f"{name} wasn't measured" for name in TARGETS if whole and name not in report
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
