"""`npm run eval -- --model local:<tag>`: that tag answers every question and runs
every helper. Unjudged, the run is Offline and nothing reaches a cloud model; judged,
the judge's calls are the only ones that do. Private rows are reported apart."""

import json
import os
import shutil
from pathlib import Path
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from backend import netguard, settings, sources, states
from backend.build_mode import STUB_OLLAMA_ENV
from evals import cli, run, score
from evals import kept as keeping
from evals.tests.questions import DATA, PLANNED, QUESTION
from testing import network
from testing.queue import drain
from testing.stub_models import ollama_reply, stub_ollama

TAG = "stub-local:7b"  # not the vault's own local tag, so what answers is this one
LOCAL = f"{run.LOCAL_PREFIX}{TAG}"
PRIVATE = {
    **QUESTION,
    "id": "q2",
    "category": "private",
    "question": "What dose was I prescribed at the clinic?",
    "expected_document": "Medical/Clinic visit.pdf",
    "expected_page": None,
    "answer": None,
    "private": True,
}


class Local:
    """A stub Ollama listing TAG beside the vault's own, which answers with a citation
    and notes, at each call, whether this process was Offline then."""

    def __init__(self) -> None:
        self.offline: list[bool] = []
        self.guarded: list[bool] = []

    def reply(self, path: str, body: dict[str, Any]) -> dict[str, Any] | str:
        if path == "/api/tags":
            return {"models": [{"name": "gemma4:26b"}, {"name": TAG}]}
        found = ollama_reply(path, body)
        if path != "/api/chat":
            return found
        self.offline.append(bool(settings.get(settings.OFFLINE)))
        self.guarded.append(netguard._guard["is_offline"]())  # noqa: SLF001
        if "<source id=" in json.dumps(body).replace('\\"', '"'):
            text = "The pet deposit is $299.11 [1]."
            return {**found, "message": {"role": "assistant", "content": text}}
        return found


@pytest.fixture
def local_run(stubs, tmp_path, monkeypatch):
    home, medical = tmp_path / "Home", tmp_path / "Medical"
    shutil.copytree(DATA / "Home", home)
    shutil.copytree(DATA / "Medical", medical)
    sources.add_source(str(home), states.STANDARD)
    sources.add_source(str(medical), states.PRIVATE)
    drain()
    anthropic, openai = stubs
    anthropic.requests.clear()
    openai.requests.clear()
    stand_in = Local()
    with stub_ollama() as ollama:
        ollama.reply = stand_in.reply
        monkeypatch.setenv(STUB_OLLAMA_ENV, ollama.url)
        monkeypatch.delenv("LOCAL_MODEL", raising=False)
        yield anthropic, openai, ollama, stand_in


def chats(ollama) -> list[str]:
    return [r.body["model"] for r in ollama.requests if r.path == "/api/chat"]


class Bracketed(TestClient):
    """The app's client, noting whether this process was Offline just before the app
    started and just after it shut down."""

    seen: ClassVar[list[bool]] = []

    def __enter__(self) -> "Bracketed":
        self.seen.append(bool(settings.get(settings.OFFLINE)))
        return super().__enter__()

    def __exit__(self, *raised: object) -> None:
        super().__exit__(*raised)
        self.seen.append(bool(settings.get(settings.OFFLINE)))


def test_an_unjudged_local_run_sends_nothing_to_the_cloud(
    local_run, tmp_path, monkeypatch
):
    anthropic, openai, ollama, stand_in = local_run
    network.attempts.clear()
    Bracketed.seen.clear()
    monkeypatch.setattr(run, "TestClient", Bracketed)
    report, claims = run.evaluate([PLANNED, PRIVATE], LOCAL, tmp_path / "runs")
    assert anthropic.requests == [], "no cloud model, the judge included"
    assert openai.requests == []
    assert network.attempts == [], "no connection off the Mac was tried"
    assert claims == []
    assert set(chats(ollama)) == {TAG}, "the tag answered and helped, nothing else"
    assert len(chats(ollama)) >= 3, "a plan and an answer, Private's included"
    assert stand_in.offline, "the stand-in was asked"
    assert all(stand_in.offline), "Offline, in this process"
    assert all(stand_in.guarded), "so netguard refuses any connection off the Mac"
    assert Bracketed.seen == [True, True], "Offline as the app starts and shuts down"
    assert settings.get(settings.OFFLINE) is False, "for this run only"
    assert "LOCAL_MODEL" not in os.environ
    assert report["answered_by"] == [TAG]
    assert report["private"]["answered_by"] == [TAG]
    manifest = keeping.Kept(Path(report["kept"])).manifest
    assert manifest["judged"] is False
    assert manifest["judge_model"] is None
    assert manifest["answer_model"] == LOCAL
    assert manifest["local_answers"] == TAG
    assert report["local_loaded"]["tag"] == TAG, "the run's model, not the vault's"


def test_a_judged_local_run_sends_only_the_judges_calls(local_run, tmp_path):
    anthropic, openai, ollama, stand_in = local_run
    report, _claims = run.evaluate(
        [QUESTION, PRIVATE], LOCAL, judge=True, limit_usd=50.0
    )
    assert openai.requests == []
    assert anthropic.requests, "the judge read the Standard answer's citation"
    assert {r.body["model"] for r in anthropic.requests} == {"claude-opus-5-5"}
    assert not any("Clinic" in r.raw for r in anthropic.requests), "nothing Private"
    assert set(chats(ollama)) == {TAG}
    assert not any(stand_in.offline), "a judged run can't be Offline"
    assert report["judged"] is True
    assert report["answered_by"] == [TAG]


def test_a_tag_ollama_doesnt_list_is_refused_before_anything_runs(
    local_run, tmp_path, monkeypatch
):
    started: list = []
    anthropic, openai, ollama, _stand_in = local_run
    monkeypatch.setattr(run, "create_app", started.append)
    with pytest.raises(run.NotAModel, match="no-such:1b"):
        run.evaluate([QUESTION], f"{run.LOCAL_PREFIX}no-such:1b", tmp_path / "runs")
    assert started == [], "the app never started"
    assert not (tmp_path / "runs").exists(), "no run was started"
    assert chats(ollama) == []
    assert anthropic.requests == openai.requests == []


def test_private_rows_are_apart_and_never_scored(local_run, tmp_path):
    report, _claims = run.evaluate(
        [QUESTION, PRIVATE], LOCAL, judge=True, limit_usd=50.0
    )
    rows = {row["id"]: row for row in report["rows"]}
    assert rows["q2"]["model"] == TAG, "the row names the model that answered it"
    assert rows["q2"]["correct"] is None
    assert rows["q2"]["claims"] == [], "never sent to the judge, so none to hand-check"
    assert report["citations_by_hand"] == 0
    assert report["questions"] == 1, "the main scores are the Standard question's"
    assert report["private"]["questions"] == 1
    assert report["private_answered_by"] == [TAG]


ANSWERS = {
    "cited": {"kind": "answer", "parts": [{"text": "It is 5 mg.", "cite": [1]}]},
    "refused": {"kind": "no_evidence", "parts": []},
    "thrown_away": {
        "kind": "no_evidence",
        "parts": [],
        "written": {"text": "It is 5 mg. [1, 2]", "handed": [{"chunk_id": "c"}]},
    },
}


@pytest.mark.parametrize("how", list(ANSWERS))
@pytest.mark.parametrize("category", ["private", "unanswerable"])
def test_a_private_rows_correct_is_null_however_it_answered(how, category):
    question = {**PRIVATE, "category": category}
    facts = run._facts_found(question, ANSWERS[how])  # noqa: SLF001
    assert run.correct(question, facts, True, []) is None


def test_an_unjudged_rows_correct_is_null_and_a_refusal_is_told_by_code():
    refusal = {**QUESTION, "category": "unanswerable"}
    assert run.correct(QUESTION, True, None, []) is None
    assert run.correct(refusal, True, None, []) is None, "unjudged"
    assert run._facts_found(refusal, ANSWERS["refused"]) is True  # noqa: SLF001
    assert run._facts_found(refusal, ANSWERS["cited"]) is False  # noqa: SLF001
    by_hand = [{"claim": "x", "by_hand": True}]
    assert run.correct(QUESTION, True, True, by_hand) is None


def test_an_unjudged_report_has_no_judged_measures_and_misses_none_of_them():
    rows = [
        {
            "category": "fact",
            "rank": 1,
            "facts_found": True,
            "correct": None,
            "parts": 1,
            "uncited": 0,
            "cost_usd": 0.0,
        },
        {
            "category": "unanswerable",
            "rank": None,
            "facts_found": True,
            "correct": None,
            "parts": 0,
            "uncited": 0,
            "cost_usd": 0.0,
        },
    ]
    found = score.summarize(rows, [], judge=False)
    assert "citation_precision" not in found
    assert "answers_correct" not in found
    assert found["judged"] is False
    assert found["not_scored"] == 2
    assert found["unanswerable"] == 1.0
    named = score.misses(found, whole=True)
    assert not any("citation_precision" in m or "answers_correct" in m for m in named)
    judged = score.summarize(rows, [True], judge=True)
    assert judged["answers_correct"] == 0.0, "the row that can't be told is left out"


def test_a_private_follow_up_of_a_standard_question_is_refused_before_asking(
    local_run,
):
    """It would be asked in the Standard thread."""
    follow_up = {**PRIVATE, "id": "f1", "follows": QUESTION["id"]}
    with pytest.raises(run.CantAsk):
        run.evaluate([QUESTION, follow_up], LOCAL, judge=False)
    with pytest.raises(run.CantAsk):
        run.evaluate(
            [PRIVATE, {**QUESTION, "id": "f2", "follows": PRIVATE["id"]}],
            LOCAL,
            judge=False,
        )


@pytest.mark.parametrize("follows", ["missing", "f0"])
def test_a_follow_up_of_no_first_question_in_the_set_is_refused_before_asking(
    local_run, follows
):
    """It would never be asked, and the run never finish."""
    chained = {**QUESTION, "id": "f0", "follows": QUESTION["id"]}
    orphan = {**QUESTION, "id": "f1", "follows": follows}
    with pytest.raises(run.CantAsk):
        run.evaluate([QUESTION, chained, orphan], LOCAL, judge=False)


def test_a_second_follow_up_of_one_first_question_is_refused_before_asking(local_run):
    """It would be asked as the thread's third turn, not its
    second, so a follow-up is one to a first question, as the generator writes them."""
    first = {**QUESTION, "id": "f1", "follows": QUESTION["id"]}
    second = {**QUESTION, "id": "f2", "follows": QUESTION["id"]}
    with pytest.raises(run.CantAsk):
        run.evaluate([QUESTION, first, second], LOCAL, judge=False)


def test_a_set_with_a_repeated_id_is_refused_before_asking(local_run):
    """The Private check found the Private first
    question by the shared id, while its follow-up was asked in the Standard one's
    thread. Every check and row looks a question up by its id, so ids are unique."""
    twin = {**PRIVATE, "id": QUESTION["id"]}
    follow_up = {**PRIVATE, "id": "f1", "follows": QUESTION["id"]}
    with pytest.raises(run.CantAsk):
        run.evaluate([QUESTION, twin, follow_up], LOCAL, judge=False)
    with pytest.raises(run.CantAsk):
        run.evaluate([QUESTION, {**QUESTION}], LOCAL, judge=False)


LEASE = "Home/Apartment lease.pdf"
NEAR = {**QUESTION, "category": "near_miss", "answer": None, "expected_page": None}
SEVERAL = {
    **QUESTION,
    "category": "multi",
    "expected_document": None,
    "expected_page": None,
    "answer": None,
    "documents": [LEASE],
    "passage_facts": {LEASE: ["$299.11"]},
}


@pytest.mark.parametrize(
    "broken",
    [
        # Facts with no documents, so neither the
        # expected document nor the page was checked.
        {**QUESTION, "answer": None, "answer_facts": ["$299.11"]},
        # Documents beside a single answer and its page.
        {**QUESTION, "documents": [LEASE]},
        {**SEVERAL, "expected_document": LEASE},
        {**SEVERAL, "expected_page": 3},
        # A zero is an answer, and a page, too.
        {**SEVERAL, "answer": 0},
        {**SEVERAL, "expected_page": 0},
        {**NEAR, "answer": 0},
        {**SEVERAL, "passage_facts": {"Home/Gym agreement.pdf": ["$45"]}},
        {**QUESTION, "passage_facts": {LEASE: ["$299.11"]}},
        {**NEAR, "answer": "$299.11"},
        {**NEAR, "documents": [LEASE]},
        {**QUESTION, "expected_document": None},
    ],
)
def test_a_question_mixing_shapes_is_refused_before_asking(local_run, broken):
    """Scoring checks one shape in full: one document, several, or none."""
    with pytest.raises(run.CantAsk):
        run.evaluate([broken], LOCAL, judge=False)


def test_each_shape_scoring_checks_is_asked(local_run):
    shapes = [QUESTION, {**SEVERAL, "id": "several"}, {**NEAR, "id": "near"}]
    assert run._refused_questions(shapes) is None  # noqa: SLF001


def test_a_failed_private_row_is_counted_apart_and_not_scored():
    """As the main scores keep failed rows apart."""
    answered = {"category": "fact", "rank": None, "state": "answered"}
    failed = {"category": "fact", "rank": 1, "state": "failed", "kind": "no_evidence"}
    found = score.private_section([answered, failed], [LOCAL])
    assert found["failed"] == 1
    assert found["recall_at_10"] == 0.0
    assert found["refused"] == 0


def test_every_question_private_cant_be_judged(local_run):
    with pytest.raises(run.NothingToJudge):
        run.evaluate([PRIVATE], LOCAL, judge=True, limit_usd=50.0)


@pytest.mark.parametrize(
    "flags",
    [
        ["--model", LOCAL, "--record"],
        ["--model", LOCAL, "--compare"],
        ["--model", LOCAL, "--write-baseline"],
        ["--no-judge", "--compare"],
    ],
)
def test_the_command_line_refuses_what_needs_a_judged_cloud_run(flags):
    with pytest.raises(SystemExit):
        cli.main([*flags, "--skip-hard-pages"])


def test_judging_is_on_for_a_cloud_model_and_off_for_a_local_one():
    assert run.judging(None, None) is True
    assert run.judging("openai:gpt-6.1-sol", None) is True
    assert run.judging(LOCAL, None) is False
    assert run.judging(LOCAL, True) is True
    assert run.judging("openai:gpt-6.1-sol", False) is False
