"""Runs the questions through the real graph on the current vault: retrieval, answers,
citations judged by Opus, and the hard-page measures. Spends money, up to a limit."""

import json
import os
import random
import subprocess
import sys
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient

from agent import harness, models
from backend import (
    auth,
    database,
    keychain,
    model_catalog,
    providers,
    search,
    settings,
    spend,
    threads,
    vault,
)
from backend.main import NO_WORKER_ENV, create_app
from evals import score

PORT = 18798
HOST = f"http://127.0.0.1:{PORT}"
LIMIT_USD = 30.0
THESE = "these"
FETCH = 40
TOP = 10
QUESTIONS = Path(__file__).parents[1] / "data/evals/calibration/questions.json"
HARD_PAGES = Path(__file__).parents[1] / "scripts/measure/hard_pages.py"
CALIBRATION = Path(__file__).parents[1] / "data/evals/calibration/hard_pages"
WAIT_SECONDS = 300
FINISHED = (threads.COMPLETED, threads.FAILED, threads.STOPPED, threads.PAUSED)


class OverBudget(Exception):
    """The run reached its limit, so it stopped rather than spend more."""


class NotAModel(SystemExit):
    """--model names no model SecureFolio can answer with."""


def _checked(model: str) -> str:
    if not providers.is_model_id(model) or model_catalog.get(model) is None:
        raise NotAModel(f"--model {model} isn't a model SecureFolio knows.")
    if model_catalog.price(model) is None:
        raise NotAModel(f"--model {model} has no known price.")
    return model


@contextmanager
def _answering(model: str | None) -> Iterator[str]:
    """The Answer model for this run: the one given, checked, or the vault's own. The
    one given holds in this process only, never in the vault, so nothing is left
    changed if the run stops partway. The judge stays Claude Opus 5.5 either way."""
    if model is None:
        yield str(settings.get(settings.MODEL_ANSWER))
        return
    _checked(model)
    stored = settings.get

    def get(key: str) -> Any:
        return model if key == settings.MODEL_ANSWER else stored(key)

    settings.get = get
    try:
        yield model
    finally:
        settings.get = stored


class NotRecorded(Exception):
    """Some answers weren't the model's own: a fallback answered them."""


def record_measured(model: str, report: dict[str, Any], day: str) -> None:
    """--record: the scores, for the catalog to show the model as measured. Only when
    that model wrote every answer it could: one written by a fallback, the local model
    under Offline say, isn't its score. Private questions are answered on this Mac for
    every model, so they don't count against it."""
    if report.get("answered_by") != [model]:
        raise NotRecorded(
            f"Not recorded: {model} didn't write every answer "
            f"(written by {', '.join(report.get('answered_by') or ['nothing'])})."
        )
    found = json.loads(model_catalog.MEASURED.read_text())
    found["models"][model] = {
        "measured_on": day,
        "recall_at_10": report["recall_at_10"],
        "citation_precision": report["citation_precision"],
        "answers_correct": report["answers_correct"],
        "unanswerable": report["unanswerable"],
        "source": f"npm run eval -- --model {model} --record",
    }
    model_catalog.MEASURED.write_text(json.dumps(found, indent=2) + "\n")


def _on_page(item: dict[str, Any], page: int) -> str:
    """The passage's words on that page, even if an answer spans its elements."""
    return " ".join(
        element["text"]
        for element in item["elements"]
        if (element["locator"] or {}).get("page") == page
    )


def _holds_the_answer(question: dict[str, Any], item: dict[str, Any]) -> bool:
    if item["title"] != Path(question["expected_document"] or "").stem:
        return False
    answer, page = question["answer"], question["expected_page"]
    if answer is None:
        return True
    if page is None:
        return score.holds(item["text"], answer)
    return score.holds(_on_page(item, page), answer)


def _hit(question: dict[str, Any], ranked: list[dict[str, Any]]) -> int | None:
    """The rank of the first passage holding the answer, within the top 10. Across
    documents, the rank at which every document's passage holding its fact has come."""
    top = ranked[:TOP]
    facts = question.get("passage_facts") or {}
    if facts:
        ranks = []
        for path, words in facts.items():
            title = Path(path).stem
            found = [
                at
                for at, item in enumerate(top, start=1)
                if item["title"] == title and score.holds(item["text"], words)
            ]
            if not found:
                return None
            ranks.append(found[0])
        return max(ranks)
    for at, item in enumerate(top, start=1):
        if _holds_the_answer(question, item):
            return at
    return None


def _selected(question: dict[str, Any]) -> list[str]:
    """Documents a question speaks of as chosen: "which of these two"."""
    if THESE not in question["question"].casefold() or not question["documents"]:
        return []
    titles = [Path(path).stem for path in question["documents"]]
    with database.read() as connection:
        return list(
            connection.execute(
                sa.select(database.documents.c.id).where(
                    database.documents.c.title.in_(titles)
                )
            ).scalars()
        )


def _rank(question: dict[str, Any]) -> int | None:
    chosen = _selected(question)
    scope = search.Scope(
        include_private=bool(question["private"]),
        document_ids=tuple(chosen) if chosen else None,
    )
    found = search.retrieve(question["question"], scope, FETCH)
    return _hit(question, harness._ranked(question["question"], found))  # noqa: SLF001


def _ask(client: TestClient, question: dict[str, Any]) -> dict[str, Any]:
    scope = None
    chosen = _selected(question)
    if question["private"]:
        scope = {"kind": "library", "include_private": True, "label": "All"}
    elif chosen:
        scope = {"kind": "documents", "ids": chosen, "label": "Selected"}
    thread = client.post("/api/threads", json={"scope": scope}).json()["id"]
    run = client.post(
        f"/api/threads/{thread}/messages", json={"text": question["question"]}
    ).json()
    begun = time.monotonic()
    while threads.get_run(run["id"])["state"] not in FINISHED:
        if time.monotonic() - begun > WAIT_SECONDS:
            client.post(f"/api/threads/{thread}/stop")
            return {}
        time.sleep(0.05)
    turns = threads.transcript(thread)
    answer = turns[-1].get("answer") or {} if turns else {}
    return {**answer, "thread_id": thread}


def _has_the_facts(question: dict[str, Any], answer: dict[str, Any]) -> bool:
    """What was asked for, in the answer's words, and from the right document."""
    if question["category"] in score.NO_ANSWER:
        return answer.get("kind") == "no_evidence"
    text = _plain(answer)
    rows = (answer.get("table") or {}).get("rows", [])
    titles = {c["title"] for c in answer.get("citations", [])} | {
        r["title"] for r in rows
    }
    if question["documents"] and not (
        {Path(p).stem for p in question["documents"]} <= titles
    ):
        return False
    if question.get("answer_facts"):
        return all(score.holds(text, fact) for fact in question["answer_facts"])
    if question["documents"]:
        return True
    expected = Path(question["expected_document"]).stem
    if expected not in titles:
        return False
    return question["answer"] is None or score.holds(text, question["answer"])


def _plain(answer: dict[str, Any]) -> str:
    """The words, with a list's rows and total, which the app shows beside them."""
    table = answer.get("table") or {}
    return " ".join(
        [
            *[p.get("text", "") for p in answer.get("parts", [])],
            *[str(r.get("amount_text", "")) for r in table.get("rows", [])],
            f"${table['total']:,.2f}" if table.get("wants_total") else "",
        ]
    )


def _passage(citation: dict[str, Any]) -> str:
    """The passage a citation opens, not only the words Claude quoted from it."""
    with database.read() as connection:
        rows = connection.execute(
            sa.select(database.chunks.c.text, database.chunks.c.element_ids).where(
                database.chunks.c.revision_id == citation["revision_id"]
            )
        ).all()
    for row in rows:
        if citation["element_id"] in (row.element_ids or []):
            return row.text
    return citation.get("quote", "")


def _cited(citation: dict[str, Any]) -> models.Cited:
    return models.Cited(
        citation.get("title", ""),
        citation.get("quote", ""),
        _passage(citation),
        citation["document_id"],
        citation.get("sensitivity", "private"),
    )


def _card_sources(answer: dict[str, Any]) -> list[models.Cited]:
    """A list's sentences rest on its rows' cards, cited or not."""
    rows = (answer.get("table") or {}).get("rows", [])
    return [
        models.Cited(
            row["title"],
            str(row.get("amount_text", "")),
            f"{row.get('amount_text', '')}. {row.get('summary', '')}",
            row["document_id"],
            row.get("sensitivity", "private"),
        )
        for row in rows
    ]


def _judged(answer: dict[str, Any]) -> tuple[list[dict[str, Any]], list[bool], bool]:
    """Each citation on its own, and each uncited sentence against the answer's sources.
    Private evidence never goes to the judge: policy refuses it, and those are left for
    the owner's hand check. Returns the citations, their verdicts, and whether every
    sentence the judge could read held up."""
    by_n = {c["n"]: _cited(c) for c in answer.get("citations", [])}
    thread = answer.get("thread_id")
    sources = [*by_n.values(), *_card_sources(answer)]
    claims, verdicts, sound = [], [], True
    for part in answer.get("parts", []):
        sentence = part.get("text", "").strip()
        cited = [by_n[n] for n in part.get("cite") or [] if n in by_n]
        try:
            for target in cited:
                verdict = models.judge_citation(
                    sentence, target, [c for c in cited if c is not target], thread
                )
                verdicts.append(verdict)
                sound = sound and verdict
                claims.append(
                    {
                        "claim": sentence,
                        "title": target.title,
                        "words": target.words,
                        "passage": target.passage,
                        "supported": verdict,
                    }
                )
            if not cited and sentence and sources:
                sound = sound and models.judge_sentence(sentence, sources, thread)
        except models.CloudRefused:
            claims.extend(
                {"claim": sentence, "title": c.title, "by_hand": True} for c in cited
            )
    return claims, verdicts, sound


def _writers(rows: Iterable[dict[str, Any]]) -> list[str]:
    return sorted({row["model"] for row in rows if row["model"]})


def _row(
    question: dict[str, Any], answer: dict[str, Any], sound: bool
) -> dict[str, Any]:
    parts = answer.get("parts", [])
    return {
        "id": question["id"],
        "category": question["category"],
        "private": bool(question["private"]),
        "rank": _rank(question),
        "correct": _has_the_facts(question, answer) and sound,
        "kind": answer.get("kind"),
        "parts": len(parts),
        "uncited": sum(1 for p in parts if p.get("uncited")),
        "cost_usd": answer.get("cost_usd", 0.0),
        "model": answer.get("model") or "",  # who wrote it, a fallback included
    }


def _hard_measure(action: str, part: str, root: Path) -> dict[str, Any]:
    done = subprocess.run(  # noqa: S603
        [sys.executable, str(HARD_PAGES), action, part, str(root)],
        stdout=subprocess.PIPE,
        text=True,
        check=True,
        # A script's own folder is on its path, not the repo that `-m` put on ours.
        env={**os.environ, "PYTHONPATH": str(HARD_PAGES.parents[2])},
    )
    lines = done.stdout.strip().splitlines()
    return json.loads(next(ln for ln in reversed(lines) if ln.startswith("{")))


def hard_pages(part: str = "checking", root: Path = CALIBRATION) -> dict[str, float]:
    vault.use_model_cache()  # the script's process finds its models through these
    routed = _hard_measure("routing", part, root)
    tables = _hard_measure("tables", part, root)
    charts = _hard_measure("charts", part, root)
    return {
        "bad_pages_read": routed["caught"] / routed["bad"],
        "good_pages_read_again": routed["read_again"] / routed["good"],
        "table_cells": tables["mean_exact"],
        "chart_values": charts["share"],
    }


def evaluate(
    questions: list[dict[str, Any]], model: str | None = None
) -> tuple[dict[str, Any], list[dict]]:
    """A report of aggregates, and the citations for the owner's own check. `model`
    answers instead of the vault's Answer model, for this run only."""
    vault.use_model_cache()
    os.environ[NO_WORKER_ENV] = "1"
    started = spend.spent_this_month()
    rows, claims, judged = [], [], []
    app = create_app(PORT, auth.new_tokens(), write_runtime=False)
    keychain.prefer_environment()  # in development, .env's keys; else the Keychain's
    headers = {"Authorization": f"Bearer {app.state.tokens.app}"}
    with (
        TestClient(app, base_url=HOST, headers=headers) as client,
        _answering(model) as answering,
    ):
        rows, claims, judged = _ask_all(client, questions, started)
    report: dict[str, Any] = score.summarize(rows, judged)
    report["answer_model"] = answering
    # Who wrote the answers, as each recorded it: a fallback shows here. A Private
    # question is answered on this Mac whatever the Answer model, by policy, so it's
    # listed apart: it's in every model's scores alike, and isn't a fallback.
    report["answered_by"] = _writers(r for r in rows if not r["private"])
    report["private_answered_by"] = _writers(r for r in rows if r["private"])
    report["citations_by_hand"] = sum(1 for claim in claims if claim.get("by_hand"))
    report["question_set"] = score.set_hash(questions)
    report["spent_usd"] = round(spend.spent_this_month() - started, 4)
    return {**report, "rows": rows}, claims


def _ask_all(
    client: TestClient, questions: list[dict[str, Any]], started: float
) -> tuple[list[dict[str, Any]], list[dict], list[bool]]:
    rows, claims, judged = [], [], []
    for question in questions:
        if spend.spent_this_month() - started > LIMIT_USD:
            raise OverBudget
        answer = _ask(client, question)
        found, verdicts, sound = _judged(answer)
        rows.append(_row(question, answer, sound))
        claims.extend(found)
        judged.extend(verdicts)
    return rows, claims, judged


def sample(claims: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    return random.Random(0).sample(claims, min(count, len(claims)))  # noqa: S311
