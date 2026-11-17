"""Runs the questions through the real graph on the current vault: retrieval, answers,
citations judged by Opus, and the hard-page measures. A run that may spend has a limit,
and stops before the request that would pass it."""

import hashlib
import json
import math
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

from agent import citations, models
from backend import (
    agent_service,
    auth,
    database,
    keychain,
    model_catalog,
    model_store,
    providers,
    search,
    settings,
    spend,
    threads,
    vault,
)
from backend.main import NO_WORKER_ENV, create_app
from backend.migrations import run as migrations
from evals import estimate, figures, own_set, scopes, score, search_parts, steps
from evals import kept as keeping
from evals.kept import Kept

PORT = 18798
HOST = f"http://127.0.0.1:{PORT}"
QUESTIONS = Path(__file__).parents[1] / "data/evals/calibration/questions.json"
HARD_PAGES = Path(__file__).parents[1] / "scripts/measure/hard_pages.py"
CALIBRATION = Path(__file__).parents[1] / "data/evals/calibration/hard_pages"
WAIT_SECONDS = 300
POLL_SECONDS = 0.01
FINISHED = (threads.COMPLETED, threads.FAILED, threads.STOPPED, threads.PAUSED)


class NoLimit(SystemExit):
    """A run that may spend has no limit, or one of nothing."""


class NotAModel(SystemExit):
    """--model names no model SecureFolio can answer with."""


class NothingToJudge(SystemExit):
    """--judge on a set whose every question is Private: the judge may read none."""


class CantAsk(SystemExit):
    """A question set the evaluation can't ask or score as it says: refused before
    anything is asked."""


# The three shapes scoring checks in full, each with the fields that belong to the
# others. One document: `expected_document`, its fact as `answer` (or the document
# itself). Several documents: `documents`, each fact in `passage_facts` under one of
# them, any sum in `answer_facts`. No answer in the library: a refusal is owed. A
# question mixing them would be scored on one shape's fields, the other's unchecked.
NOT_IN_SHAPE = {
    "about one document": ("answer_facts", "passage_facts"),
    "about several documents": ("expected_document", "answer", "expected_page"),
    "with no answer in the library": (
        "answer",
        "answer_facts",
        "documents",
        "passage_facts",
    ),
    # A report: its document by id, at a place, with any fact typed as answer_facts.
    "reported by you": ("answer", "documents", "passage_facts", "expected_page"),
}


def _shape_of(question: dict[str, Any]) -> str | None:
    if question["category"] in score.NO_ANSWER:
        return "with no answer in the library"
    if question["category"] == own_set.REPORTED:
        return "reported by you" if question.get("expected_document_id") else None
    if question.get("documents"):
        return "about several documents"
    if question.get("expected_document"):
        return "about one document"
    return None


def _given(value: Any) -> bool:
    """A field is given unless it's missing or empty: 0 is an amount, and a page."""
    return value is not None and value not in ("", [], {})


def _shape_broken(question: dict[str, Any]) -> str | None:
    """How a question breaks the shape it's scored as, if it does."""
    shape = _shape_of(question)
    if shape is None:
        return "names neither an expected_document nor its documents"
    mixed = [name for name in NOT_IN_SHAPE[shape] if _given(question.get(name))]
    passages = question.get("passage_facts") or {}
    if not mixed and passages and set(passages) != set(question["documents"]):
        mixed = ["passage_facts for other documents"]
    return f"is {shape}, but gives {', '.join(mixed)}" if mixed else None


def _refused_questions(questions: list[dict[str, Any]]) -> str | None:
    """Why a set can't be asked, before anything is. A follow-up is asked in its first
    question's thread, in that thread's scope, so a Private one under a Standard one
    would reach the Standard thread's cloud model, and the other way round would be
    asked as Private, and one that follows no first question in the set would never be
    asked at all. A follow-up is its thread's second turn, so a first question has one
    at most. Each question must be one of the shapes scoring checks in full, and each
    id names one question, since every check here and every row finds it by its id."""
    seen: set[str] = set()
    for question in questions:
        if question["id"] in seen:
            return (
                f"{question['id']} names more than one question: each question in a "
                "set has its own id."
            )
        seen.add(question["id"])
    by_id = {q["id"]: q for q in score.first(questions)}
    followed: set[str] = set()
    for question in questions:
        follows = question.get("follows")
        if follows and (follows not in by_id or follows in followed):
            return (
                f"{question['id']} follows {follows}, which isn't a first question in "
                "the set without a follow-up: a follow-up is the second turn of its "
                "first question's thread."
            )
        if follows:
            followed.add(follows)
        first = by_id.get(follows or "")
        if first is not None and bool(first["private"]) != bool(question["private"]):
            return (
                f"{question['id']} follows {first['id']}, but one is Private and the "
                "other isn't: a follow-up is asked in its first question's thread."
            )
        earlier = question.get("earlier") or []
        listed = isinstance(earlier, list) and all(isinstance(e, str) for e in earlier)
        if earlier and (follows or not listed):
            return (
                f"{question['id']} gives earlier questions, which are a list of "
                "questions asked first in its own thread, and can't follow another."
            )
        broken = _shape_broken(question)
        if broken:
            return (
                f"{question['id']} {broken}: a question is about one document, "
                "several, or none in the library, and is scored as that."
            )
    return None


LOCAL_PREFIX = "local:"  # --model local:<tag>, a model Ollama runs on this Mac


def local_of(model: str | None) -> str | None:
    """The Ollama tag a `local:<tag>` model names, or None for a cloud model."""
    if model is None or not model.startswith(LOCAL_PREFIX):
        return None
    return model.removeprefix(LOCAL_PREFIX)


def _checked(model: str) -> str:
    tag = local_of(model)
    if tag is not None:
        if not tag or not agent_service.local_listed(tag):
            raise NotAModel(
                f"--model {model}: Ollama on this Mac doesn't list {tag!r}."
            )
        return model
    if not providers.is_model_id(model) or model_catalog.get(model) is None:
        raise NotAModel(f"--model {model} isn't a model SecureFolio knows.")
    if model_catalog.price(model) is None:
        raise NotAModel(f"--model {model} has no known price.")
    return model


def _overrides(model: str, judge: bool, helper: str | None = None) -> dict[str, Any]:
    """The settings this run holds in its process. A local model answers, helps and
    reads Private questions; unjudged, it runs Offline, so nothing leaves the Mac. A
    cloud model's helper is the vault's, unless the run names one."""
    tag = local_of(model)
    if tag is None:
        return {settings.MODEL_ANSWER: model} | (
            {settings.MODEL_HELPER: helper} if helper else {}
        )
    local = {
        key: settings.LOCAL
        for key in (settings.MODEL_ANSWER, settings.MODEL_DEEP, settings.MODEL_HELPER)
    }
    local |= {key: tag for key in settings.LOCAL_JOBS}
    return local if judge else {**local, settings.OFFLINE: True}


# The helper a run names for a cloud model, in place of the vault's: for the process,
# while it runs, as the judge is.
_helper: str | None = None


@contextmanager
def helped_by(helper: str | None) -> Iterator[None]:
    """For one run: the helper a cloud model's run uses, or the vault's when None."""
    global _helper  # noqa: PLW0603  one helper for the process, as the evaluation runs
    if helper is not None:
        _checked(helper)
    before, _helper = _helper, helper
    try:
        yield
    finally:
        _helper = before


@contextmanager
def _answering(model: str | None, judge: bool = True) -> Iterator[str]:
    """The Answer model for this run: the one given, checked, or the vault's own. The
    one given holds in this process only, never in the vault, so nothing is left
    changed if the run stops partway. The judge is the run's own, `judged_by`. A
    local model's tag is also `LOCAL_MODEL`, which `model_store.local_tag` reads
    before the settings, and which `npm run eval` may have loaded from `.env`."""
    if model is None and _helper is None:
        yield str(settings.get(settings.MODEL_ANSWER))
        return
    model = model or str(settings.get(settings.MODEL_ANSWER))
    _checked(model)
    overrides = _overrides(model, judge, _helper)
    stored, environment = settings.get, os.environ.get("LOCAL_MODEL")

    def get(key: str) -> Any:
        return overrides[key] if key in overrides else stored(key)

    settings.get = get
    tag = local_of(model)
    if tag is not None:
        os.environ["LOCAL_MODEL"] = tag
    try:
        yield model
    finally:
        settings.get = stored
        if tag is not None:
            if environment is None:
                os.environ.pop("LOCAL_MODEL", None)
            else:
                os.environ["LOCAL_MODEL"] = environment


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
        **score.measured_entry(report, day, agent_service.judge_model()),
        "source": f"npm run eval -- --model {model} --record",
    }
    model_catalog.MEASURED.write_text(json.dumps(found, indent=2) + "\n")


def _rank(question: dict[str, Any]) -> int | None:
    """Recall's rank: search as Ask runs it, hybrid and reranked."""
    return search_parts.rank(question, search_parts.WHOLE)


def _expected_titles(question: dict[str, Any]) -> list[str]:
    paths = list(question.get("passage_facts") or {}) or [question["expected_document"]]
    return [Path(path).stem for path in paths if path]


def _indexed(question: dict[str, Any]) -> list[dict[str, Any]]:
    """Every passage of the expected documents, as indexed in their active revision,
    with its elements: what step 1 reads."""
    titles = _expected_titles(question)
    if not titles:
        return []
    with database.read() as connection:
        ids = list(
            connection.execute(
                sa.select(database.chunks.c.id)
                .join(
                    database.documents,
                    database.documents.c.id == database.chunks.c.document_id,
                )
                .where(
                    database.documents.c.title.in_(titles),
                    database.documents.c.active_revision_id
                    == database.chunks.c.revision_id,
                )
            ).scalars()
        )
    return search.evidence(ids, include_private=True)


def _new_thread(client: TestClient, question: dict[str, Any]) -> str:
    """A thread in the scope the question speaks of."""
    scope = None
    chosen = scopes.selected(question)
    if question["private"]:
        scope = {"kind": "library", "include_private": True, "label": "All"}
    elif chosen:
        scope = {"kind": "documents", "ids": chosen, "label": "Selected"}
    return client.post("/api/threads", json={"scope": scope}).json()["id"]


def _ask(
    client: TestClient, question: dict[str, Any], thread: str | None = None
) -> dict[str, Any]:
    """A question as a new thread's first turn, or a follow-up as the next turn of
    the thread given, in that thread's scope."""
    thread = thread or _new_thread(client, question)
    begun = time.monotonic()
    run = client.post(
        f"/api/threads/{thread}/messages", json={"text": question["question"]}
    ).json()
    times = _timed(run["id"], begun)
    if times["complete_ms"] is None:
        client.post(f"/api/threads/{thread}/stop")
        return {"thread_id": thread, "times": times, "timed_out": True}
    answer = _answer_of(thread, run["id"])
    written = client.portal.call(  # type: ignore[union-attr]  # set inside `with`
        client.app.state.agent.turn_record,  # type: ignore[attr-defined]
        thread,
        run["id"],
    )
    return {**answer, "thread_id": thread, "times": times, "written": written}


def _answer_of(thread: str, run_id: str) -> dict[str, Any]:
    """This run's own answer, and only when the run completed: a run that failed,
    stopped or paused has none, even where an earlier turn of its thread has one."""
    if threads.get_run(run_id)["state"] != threads.COMPLETED:
        return {}
    mine = [
        dict(turn["answer"])
        for turn in threads.transcript(thread)
        if turn.get("run_id") == run_id and turn.get("answer")
    ]
    return mine[-1] if mine else {}


def _ms(since: float) -> int:
    return round((time.monotonic() - since) * 1000)


def _timed(run_id: str, begun: float) -> dict[str, int | None]:
    """When the first step, the first words and the finish reached the run's events,
    from posting the question: the times the owner sees. None for what never came."""
    times: dict[str, int | None] = {
        "first_step_ms": None,
        "first_words_ms": None,
        "complete_ms": None,
    }
    seen = 0
    while time.monotonic() - begun <= WAIT_SECONDS:
        for event in threads.events_after(run_id, seen):
            seen = event["id"]
            if event["event"] == "step" and times["first_step_ms"] is None:
                times["first_step_ms"] = _ms(begun)
            if event["event"] == "token" and times["first_words_ms"] is None:
                times["first_words_ms"] = _ms(begun)
        if threads.get_run(run_id)["state"] in FINISHED:
            times["complete_ms"] = _ms(begun)
            # The run may have written its last events after they were read above
            # and before it finished: they're read now, at the finish.
            for event in threads.events_after(run_id, seen):
                if event["event"] == "step" and times["first_step_ms"] is None:
                    times["first_step_ms"] = times["complete_ms"]
                if event["event"] == "token" and times["first_words_ms"] is None:
                    times["first_words_ms"] = times["complete_ms"]
            return times
        time.sleep(POLL_SECONDS)
    return times


def _opens_the_page(question: dict[str, Any], answer: dict[str, Any]) -> bool:
    """A single fact with an expected page is found only where a citation of the
    expected document opens that page."""
    page = question.get("expected_page")
    if question["category"] in score.NO_ANSWER or page is None:
        return True  # a refusal cites nothing
    if question.get("answer_facts") or question["documents"]:
        return True
    expected = Path(question["expected_document"]).stem
    return any(
        c.get("title") == expected and (c.get("locator") or {}).get("page") == page
        for c in answer.get("citations", [])
    )


def _facts_found(question: dict[str, Any], answer: dict[str, Any]) -> bool | None:
    """By code: whether the answer holds what was asked for, from the right
    documents. None when no answer came, or when nothing says what's owed."""
    if not answer.get("kind") or not score.assessable(question):
        return None
    if question["category"] == own_set.REPORTED:  # at its place, not its page alone
        return own_set.passes(question, answer, _plain(answer))
    return _has_the_facts(question, answer) and _opens_the_page(question, answer)


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


def card_sources(answer: dict[str, Any]) -> list[models.Cited]:
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
    sources = [*by_n.values(), *card_sources(answer)]
    claims, verdicts, sound = [], [], True
    for part in answer.get("parts", []):
        sentence = part.get("text", "").strip()
        cited = [by_n[n] for n in part.get("cite") or [] if n in by_n]
        unread = list(cited)  # those the judge hasn't read yet
        try:
            for target in cited:
                _stop_at_limit()
                verdict = agent_service.judge_citation(
                    sentence, target, [c for c in cited if c is not target], thread
                )
                verdicts.append(verdict)
                sound = sound and verdict
                # Its document by id, so judging it again never has to tell it.
                claims.append(
                    {
                        "claim": sentence,
                        "title": target.title,
                        "words": target.words,
                        "passage": target.passage,
                        "document_id": target.document_id,
                        "supported": verdict,
                    }
                )
                unread.remove(target)
            if not cited and sentence and sources:
                _stop_at_limit()
                verdict = agent_service.judge_sentence(sentence, sources, thread)
                sound = sound and verdict
                claims.append(
                    {"claim": sentence, "uncited": True, "supported": verdict}
                )
        except models.OverLimit:
            raise  # the run stops: nothing is left for the hand check
        except models.CloudRefused:
            claims.extend(
                {"claim": sentence, "title": c.title, "by_hand": True} for c in unread
            )
            if not cited:
                claims.append({"claim": sentence, "uncited": True, "not_judged": True})
    return claims, verdicts, sound


def _stop_at_limit() -> None:
    """Between judge calls: once the limit has refused a request, no more go."""
    if agent_service.limit_reached():
        raise models.OverLimit("The run reached its spending limit.")


def _writers(rows: Iterable[dict[str, Any]]) -> list[str]:
    return sorted({row["model"] for row in rows if row["model"]})


def thrown_away(answer: dict[str, Any]) -> bool:
    """The model wrote an answer from passages, and Verify replaced it with "no
    evidence" because none of its citations could be read."""
    written = answer.get("written") or {}
    said = str(written.get("text") or "")
    return (
        answer.get("kind") == "no_evidence"
        and bool(written.get("handed"))
        and bool(said.strip())
        and not citations.is_refusal(said)  # the model's own refusal isn't thrown away
    )


def correct(
    question: dict[str, Any],
    facts: bool | None,
    sound: bool | None,
    claims: list[dict[str, Any]],
) -> bool | None:
    """Facts found and every sentence judged sound. None when it can't be told: a
    Private row, which the judge never reads; a row whose facts can't be told; any
    row of an unjudged run (`sound` None); or one where any sentence went unjudged.
    Whether a refusal was owed and came is told by code, in `facts_found`."""
    if question["private"] or facts is None or sound is None:
        return None
    if any(c.get("by_hand") or c.get("not_judged") for c in claims):
        return None
    return facts and sound


def _row(
    question: dict[str, Any],
    answer: dict[str, Any],
    sound: bool | None,
    claims: list[dict[str, Any]],
) -> dict[str, Any]:
    row = _measured(question, answer, sound, claims)
    return {**row, "step": steps.step(question, row)}


def _measured(
    question: dict[str, Any],
    answer: dict[str, Any],
    sound: bool | None,
    claims: list[dict[str, Any]],
) -> dict[str, Any]:
    parts = answer.get("parts", [])
    facts = _facts_found(question, answer)
    return {
        "id": question["id"],
        "category": question["category"],
        "private": bool(question["private"]),
        "follows": question.get("follows"),
        "earlier": len(question.get("earlier") or []),
        "reported": own_set.is_report(question),
        "state": _state(question, answer),
        "rank": _rank(question),
        "facts_found": facts,
        "correct": correct(question, facts, sound, claims),
        "kind": answer.get("kind"),
        "parts": len(parts),
        "uncited": sum(1 for p in parts if p.get("uncited")),
        "cost_usd": answer.get("cost_usd", 0.0),
        "model": answer.get("model") or "",  # who wrote it, a fallback included
        "thrown_away": thrown_away(answer),
        "indexed_holds": steps.indexed_holds(question, _indexed(question)),
        "handed_holds": steps.handed_holds(question, answer.get("written")),
        "cards_hold": steps.cards_hold(question, answer.get("written")),
        **(answer.get("times") or _NO_TIMES),
        "sentences": parts,
        "citations": [
            {
                k: c.get(k)
                for k in ("n", "document_id", "title", "label", "locator", "quote")
            }
            for c in answer.get("citations", [])
        ],
        "claims": claims,
        "written": answer.get("written"),
    }


def _state(question: dict[str, Any], answer: dict[str, Any]) -> str:
    """failed and not_assessed rows are reported apart, and never scored."""
    if not answer.get("kind"):
        return score.FAILED
    return score.ANSWERED if score.assessable(question) else score.NOT_ASSESSED


_NO_TIMES = {"first_step_ms": None, "first_words_ms": None, "complete_ms": None}


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


INSTRUCTIONS = Path(__file__).parents[1] / "agent" / "instructions"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _version(model: str) -> str:
    """A cloud model's id names its version; a local one's is the build Ollama
    reports for its tag."""
    if providers.is_model_id(model):
        return model
    return model_store.digest(local_of(model) or model) or "not listed"


def _index() -> dict[str, Any]:
    """Which revisions were searched: their count, chunks, and a hash of their ids and
    contents, so two runs on different indexes don't compare as one."""
    with database.read() as connection:
        active = connection.execute(
            sa.select(
                database.revisions.c.id, database.revisions.c.content_sha256
            ).where(
                database.revisions.c.id.in_(
                    sa.select(database.documents.c.active_revision_id)
                )
            )
        ).all()
        chunks = connection.execute(
            sa.select(sa.func.count()).select_from(database.chunks)
        ).scalar()
    pairs = sorted(f"{row.id}:{row.content_sha256}" for row in active)
    return {
        "revisions": len(pairs),
        "chunks": chunks,
        "sha256": _sha256("\n".join(pairs).encode()),
    }


def measured(
    questions: list[dict[str, Any]], answering: str, judge: bool = True
) -> dict[str, Any]:
    """Everything needed to run this again: what answered and helped, with versions
    and settings, the instructions, the index, the questions and the scoring."""
    helper = str(settings.get(settings.MODEL_HELPER))
    local = model_store.local_tag()
    return {
        "judged": judge,
        "answer_model": answering,
        "answer_version": _version(answering),
        "helper_model": helper,
        "helper_version": _version(helper) if helper != settings.LOCAL else None,
        "local_answers": local,
        "local_version": _version(local),
        "settings": {
            key: settings.get(key)
            for key in (
                settings.MODEL_ANSWER,
                settings.MODEL_DEEP,
                settings.MODEL_HELPER,
                settings.LOCAL_ANSWERS,
                settings.OFFLINE,
                settings.MEMORY_ON,
            )
        },
        "local_answer_tokens": models.LOCAL_ANSWER_TOKENS,
        "local_temperature": models.LOCAL_ANSWER_TEMPERATURE,
        "local_context": models.LOCAL_CONTEXT,
        "local_setup": agent_service.local_setup(local),
        "judge_model": agent_service.judge_model() if judge else None,
        "instructions": {
            path.name: _sha256(path.read_bytes())
            for path in sorted(INSTRUCTIONS.glob("*.md"))
        },
        "index": _index(),
        "question_set": score.set_hash(questions),
        "asked": score.asked_hash(questions),
        "questions": len(questions),
        "scoring": score.SCORING_VERSION,
    }


class NotAllowed(SystemExit):
    """A model this run would call belongs to a provider the run doesn't name."""


def _allowed(answering: str, judge: bool) -> None:
    """Before anything is sent: each model the run calls, cloud or local, is one it
    may send to."""
    named = [
        ("Answer model", answering),
        ("helper", str(settings.get(settings.MODEL_HELPER))),
        *((("judge", agent_service.judge_model()),) if judge else ()),
    ]
    outside = [
        f"its {what}, {model}"
        for what, model in named
        if not agent_service.provider_allowed(model)
    ]
    if outside:
        raise NotAllowed(
            f"This run may send only to the providers it names, and would call "
            f"{', '.join(outside)}."
        )


def expected_cost(
    questions: list[dict[str, Any]],
    model: str | None,
    judge: bool,
    kept_in: Path,
) -> estimate.Estimate:
    """What the run is expected to cost, with the models it would run with. The vault
    is brought up to date first, as the app's start does, to read its settings."""
    if model is not None:
        _checked(model)  # before the vault is opened
    migrations.upgrade()
    with _answering(model, judge) as answering:
        _allowed(answering, judge)
        helper = str(settings.get(settings.MODEL_HELPER))
        found = estimate.expected(questions, answering, helper, judge, kept_in)
        return estimate.asked_here(
            found, questions, answering, agent_service.local_tag()
        )


def judging(model: str | None, judge: bool | None) -> bool:
    """Judged by default for a cloud model, not for a local one: its run then makes
    no cloud request at all. --judge and --no-judge say otherwise."""
    return local_of(model) is None if judge is None else judge


def judged_run(
    questions: list[dict[str, Any]], model: str | None, judge: bool | None
) -> bool:
    """Whether the run is judged, from its questions as they'll be asked: the one
    place this is decided, for the estimate, the providers' check and the run alike.
    With every question Private the judge would read none, which --judge is refused
    for; a set of your reports, every one Private now, goes unjudged instead."""
    judged = judging(model, judge)
    if judged and questions and all(q["private"] for q in questions):
        if judge is True or not any(own_set.is_report(q) for q in questions):
            raise NothingToJudge(
                "--judge: every question here is Private, and the judge reads none."
            )
        return False  # your reports, every one Private now: asked on this Mac
    return judged


def evaluate(
    questions: list[dict[str, Any]],
    model: str | None = None,
    kept_in: Path | None = None,
    judge: bool | None = None,
    limit_usd: float | None = None,
) -> tuple[dict[str, Any], list[dict]]:
    """A report of aggregates, and the citations for the owner's own check. `model`
    answers instead of the vault's Answer model, for this run only. A run that may
    spend needs `limit_usd`; one that can't runs under a limit of nothing, so no cloud
    request can go from it at all. A run that meets its limit is kept as stopped at
    it, with the rows it finished, and its report says so."""
    if model is not None:
        _checked(model)  # before the app starts and opens the vault
    refused = _refused_questions(questions)
    if refused:
        raise CantAsk(refused)
    # Your own set's reports, as Private as what they name is now; any whose
    # document is gone, left out and listed.
    questions, listed = own_set.checked(questions)
    judge = judged_run(questions, model, judge)
    vault.use_model_cache()
    os.environ[NO_WORKER_ENV] = "1"
    os.environ[agent_service.NO_TICKS_ENV] = "1"
    started = spend.spent_this_month()
    rows, claims = [], []
    # The run's settings hold from before the app starts until after it has shut
    # down, so an unjudged local run is Offline throughout.
    with _answering(model, judge) as answering:
        _allowed(answering, judge)
        limit = _limit(answering, judge, limit_usd)
        app = create_app(PORT, auth.new_tokens(), write_runtime=False)
        keychain.prefer_environment()  # in development, .env's; else the Keychain's
        headers = {"Authorization": f"Bearer {app.state.tokens.app}"}
        with TestClient(app, base_url=HOST, headers=headers) as client:
            kept = kept_in and keeping.start(
                kept_in, answering, measured(questions, answering, judge)
            )
            try:
                with (
                    agent_service.recording_calls(),
                    agent_service.spending_limit(limit),
                ):
                    rows, claims, _verdicts, at_limit = _ask_all(
                        client, questions, kept, judge
                    )
            except BaseException as failed:
                if kept:
                    kept.fail(type(failed).__name__)
                raise
        # The run's own local model, while its tag still holds.
        loaded = agent_service.local_setup(model_store.local_tag())
    report = scored(rows, judge)
    report["answer_model"] = answering
    report["citations_by_hand"] = sum(1 for claim in claims if claim.get("by_hand"))
    report["question_set"] = score.set_hash(questions)
    report["spent_usd"] = round(spend.spent_this_month() - started, 4)
    report["local_loaded"] = loaded
    report["limit_usd"] = limit
    report["stopped_at_limit"] = at_limit
    if any(own_set.is_report(q) for q in questions) or any(listed.values()):
        report["reported_failures"] = own_set.section(rows, listed)
    if kept:
        report["kept"] = str(kept.folder)
        if at_limit:
            report["figures"] = figures.own_section(kept)
            kept.stop_at_limit(report)
    return {**report, "rows": rows}, claims


def scored(rows: list[dict[str, Any]], judge: bool) -> dict[str, Any]:
    """Every score a report gives from its rows: the first questions', the follow-ups'
    apart, and the Private questions' in a section of their own, with who wrote them
    and the wrong answers by step. A kept run's rows give the same report again."""
    # Your reports are scored apart, as reported failures that now pass.
    rows = [r for r in rows if not r.get("reported")]
    main = [r for r in rows if not r["private"] and not r.get("follows")]
    report: dict[str, Any] = score.summarize(main, score.verdicts(main), judge)
    follow_ups = [r for r in rows if not r["private"] and r.get("follows")]
    if follow_ups:  # scored apart: the main scores are the first questions'
        report["follow_ups"] = score.summarize(
            follow_ups, score.verdicts(follow_ups), judge
        )
    # Who wrote the answers, as each recorded it: a fallback shows here. A Private
    # question is answered on this Mac whatever the Answer model, by policy, so it's
    # listed apart: it's in every model's scores alike, and isn't a fallback.
    report["answered_by"] = _writers(r for r in rows if not r["private"])
    report["private_answered_by"] = _writers(r for r in rows if r["private"])
    report["private"] = score.private_section(
        [r for r in rows if r["private"]], report["private_answered_by"]
    )
    report["helpers_cut"] = helpers_cut(rows)
    report["local_prompts"] = local_prompts(rows)
    return report | _listed_by_step(rows)


def helpers_cut(rows: list[dict[str, Any]]) -> dict[str, int]:
    """How many local helper calls ended at their length or time limit, by purpose,
    over every row: a count that rises says a limit is set too tight."""
    cut: dict[str, int] = {}
    for row in rows:
        for call in row.get("calls") or []:
            if call.get("outcome") == models.CUT:
                cut[call["purpose"]] = cut.get(call["purpose"], 0) + 1
    return cut


def local_prompts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Local answers' prompts against the context they're fitted to: how many were
    fitted and how many passages that left out, and the largest prompt as Ollama
    counted it (`prompt_eval_count`) and as the fit counted it."""
    sent = [
        call
        for row in rows
        for call in row.get("calls") or []
        if "prompt_counted" in call
    ]
    return {
        "context": models.LOCAL_CONTEXT,
        "answers": len(sent),
        "fitted": sum(1 for call in sent if call["passages_dropped"]),
        "passages_dropped": sum(call["passages_dropped"] for call in sent),
        "largest": max((call["input_tokens"] for call in sent), default=0),
        "largest_counted": max((call["prompt_counted"] for call in sent), default=0),
    }


def _listed_by_step(rows: list[dict[str, Any]]) -> dict[str, dict[str, list[str]]]:
    """The wrong answers' questions under each step: Standard first questions, and
    Private rows and Standard follow-ups apart, as every report keeps them."""
    standard = [r for r in rows if not r["private"]]
    return {
        "wrong_questions_by_step": steps.by_step(
            [r for r in standard if not r.get("follows")]
        ),
        "private_wrong_questions_by_step": steps.by_step(
            [r for r in rows if r["private"]]
        ),
        "follow_up_wrong_questions_by_step": steps.by_step(
            [r for r in standard if r.get("follows")]
        ),
    }


def _limit(answering: str, judge: bool, limit_usd: float | None) -> float:
    """The run's limit: the one given, or nothing for a run that can't spend."""
    helper = str(settings.get(settings.MODEL_HELPER))
    if estimate.free(answering, helper, judge):
        return limit_usd or 0.0
    if limit_usd is None or not usable(limit_usd):
        raise NoLimit("A run that may spend needs --limit-usd, above $0.")
    return limit_usd


def unlimited(expected: estimate.Estimate, limit_usd: float | None) -> str | None:
    """Why a run that may spend can't start with this limit, shown before it starts.
    A limit under the estimate isn't refused: the run goes until the limit stops it."""
    if expected.free:
        return None
    if limit_usd is None or not usable(limit_usd):
        return "This run may spend: give it --limit-usd, a finite amount above $0."
    if limit_usd < expected.usd:
        sys.stdout.write(
            f"The limit, ${limit_usd:.2f}, is under the expected cost: the run "
            "stops when it reaches it.\n"
        )
    return None


def usable(limit_usd: float | None) -> bool:
    """A limit is a finite amount above $0: NaN or infinity would bound nothing."""
    return limit_usd is not None and math.isfinite(limit_usd) and limit_usd > 0


def _ask_all(
    client: TestClient,
    questions: list[dict[str, Any]],
    kept: Kept | None = None,
    judge: bool = True,
) -> tuple[list[dict[str, Any]], list[dict], list[bool], bool]:
    """Each first question in turn, each follow-up as the next turn of its first
    question's thread, until the run's limit refuses a request: that question is kept
    as stopped, never as wrong, and no later one is asked. Returns the rows, the
    claims, the verdicts, and whether the limit stopped the run."""
    rows, claims, judged = [], [], []
    for question in score.first(questions):
        thread = None
        for each in [
            question,
            *(q for q in questions if q.get("follows") == question["id"]),
        ]:
            if each is question or thread is not None:
                row, found, verdicts, at_limit = _asked(client, each, judge, thread)
            else:  # its first question failed, so there's no turn to follow
                row, found, verdicts, at_limit = _unasked(each), [], [], False
            if at_limit:
                row = _stopped(row)
            if kept is not None:
                kept.row(row)
            rows.append(row)
            if at_limit:
                return rows, claims, judged, True
            claims.extend(found)
            judged.extend(verdicts)
            if each is question and row["state"] != score.FAILED:
                thread = row["thread_id"]
    return rows, claims, judged, False


def _unasked(question: dict[str, Any]) -> dict[str, Any]:
    """A follow-up whose first question failed: reported apart, as failed."""
    return {**_row(question, {}, None, []), "thread_id": None, "calls": []}


def _asked(
    client: TestClient, question: dict[str, Any], judge: bool, thread: str | None
) -> tuple[dict[str, Any], list[dict], list[bool], bool]:
    """One question asked and, when the run is judged and it's scored, judged: its
    row, its claims, its verdicts, and whether the limit stopped it. A report is
    read as Private as it is just before it's asked; and while it's asked and judged,
    every request rests on what it does, so one made Private meanwhile refuses the
    next request, and none of its words leave."""
    if not own_set.is_report(question):
        return _asking(client, question, judge, thread)
    question = own_set.as_now(question)
    with agent_service.resting_on(*own_set.rests_on(question)):
        row, found, verdicts, at_limit = _asking(client, question, judge, thread)
    # Saved as Private, with why, if it became so before or while it was asked.
    why = own_set.as_now(question).get(own_set.BECAME)
    if why:
        row = {**row, "private": True, own_set.BECAME: why}
    return row, found, verdicts, at_limit


def _asking(
    client: TestClient, question: dict[str, Any], judge: bool, thread: str | None
) -> tuple[dict[str, Any], list[dict], list[bool], bool]:
    """`_asked`'s question asked: a report's earlier questions first, in its own
    thread."""
    earlier_usd = 0.0
    if question.get("earlier"):
        thread, answered, earlier_usd = _earlier_asked(client, question)
        if not answered:  # an earlier turn got no answer, so this isn't asked
            calls = agent_service.take_calls(thread)
            row = {**_unasked(question), "thread_id": thread, "calls": calls}
            row["cost_usd"] = estimate.cost_of(calls)  # what its earlier turns sent
            return row, [], [], agent_service.limit_reached()
    answer = _ask(client, question, thread)
    if earlier_usd:  # the earlier turns are the report's own cost
        answer["cost_usd"] = answer.get("cost_usd", 0.0) + earlier_usd
    found, verdicts, sound = [], [], None
    at_limit = agent_service.limit_reached()
    # Only Standard rows that are scored go to the judge: a Private one is reported
    # as not judged, and a failed or not-assessed one is never scored.
    scored = answer.get("kind") and score.assessable(question)
    if judge and scored and not question["private"] and not at_limit:
        try:
            found, verdicts, sound = _judged(answer)
        except models.OverLimit:
            at_limit = True
    row = _row(question, answer, sound, found)
    row["thread_id"] = answer["thread_id"]
    row["calls"] = agent_service.take_calls(answer["thread_id"])
    if not answer.get("kind"):  # no answer to say what it cost: its calls say
        row["cost_usd"] = estimate.cost_of(row["calls"])
    return row, found, verdicts, at_limit


def _earlier_asked(
    client: TestClient, question: dict[str, Any]
) -> tuple[str, bool, float]:
    """A new thread, in the question's scope, with its earlier questions asked in
    order: whether each got an answer, and what they cost. They aren't scored or
    judged; their calls and their cost are the question's own."""
    thread = _new_thread(client, question)
    spent = 0.0
    for words in question["earlier"]:
        answer = _ask(client, {**question, "question": words}, thread)
        spent += float(answer.get("cost_usd") or 0.0)
        if not answer.get("kind"):
            return thread, False, spent
    return thread, True, spent


def _stopped(row: dict[str, Any]) -> dict[str, Any]:
    """The question the limit stopped: reported apart, and never scored."""
    return {**row, "state": score.STOPPED, "correct": None, "claims": [], "step": None}


def sample(claims: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """Citations for the owner's own check: an uncited sentence has none to check."""
    cited = [claim for claim in claims if not claim.get("uncited")]
    return random.Random(0).sample(cited, min(count, len(cited)))  # noqa: S311
