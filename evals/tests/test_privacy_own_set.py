"""A report whose document has become Private since it was saved is asked as Private
answered and scored on this Mac, never
judged by a cloud model, and listed by the run as having become Private."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from backend import database, sources, states
from backend.build_mode import STUB_OLLAMA_ENV
from evals import cli, run, score
from evals import kept as keeping
from evals.tests.questions import DATA, QUESTION, SOL
from evals.tests.reported import document, reported
from evals.tests.stub_providers import answered, judged
from testing.queue import drain
from testing.stub_models import ollama_reply, stub_ollama


def answers_on_this_mac(path: str, body: dict[str, Any]) -> dict[str, Any] | str:
    found = ollama_reply(path, body)
    if path == "/api/chat" and "<source id=" in json.dumps(body).replace('\\"', '"'):
        text = "The pet deposit is $299.11 [1]."
        return {**found, "message": {"role": "assistant", "content": text}}
    return found


@pytest.fixture
def home_and_local(stubs, tmp_path, monkeypatch):
    home = tmp_path / "Home"
    shutil.copytree(DATA / "Home", home)
    sources.add_source(str(home), states.STANDARD)
    drain()
    with stub_ollama() as ollama:
        ollama.reply = answers_on_this_mac
        monkeypatch.setenv(STUB_OLLAMA_ENV, ollama.url)
        monkeypatch.delenv("LOCAL_MODEL", raising=False)
        yield (*stubs, ollama)


def test_a_report_now_private_is_asked_on_this_mac_and_never_judged(
    home_and_local, tmp_path
):
    anthropic, openai, ollama = home_and_local
    private_lease()
    words = "Which pet deposit did my lease set, as I reported it?"
    report, _claims = run.evaluate(
        [QUESTION, reported(question=words)], SOL, tmp_path / "runs", limit_usd=50.0
    )
    rows = {r["id"]: r for r in keeping.Kept(Path(report["kept"])).rows()}
    mine = rows["r-1"]
    assert mine["private"] is True
    assert mine["model"] != SOL, "answered on this Mac"
    assert any(r.path == "/api/chat" for r in ollama.requests)
    assert mine["claims"] == [], "never judged"
    assert mine["correct"] is None
    cloud = [*anthropic.requests, *openai.requests]
    assert cloud, "the Standard question went to the cloud, and its judge"
    assert not any(words in r.raw for r in cloud), "nothing of the report did"
    assert report["reported_failures"]["became_private"] == [
        {"id": "r-1", "why": "Apartment lease is in a Private folder"}
    ]


def private_lease() -> None:
    with database.write() as connection:
        connection.execute(
            sa.update(database.documents)
            .where(database.documents.c.id == document("Apartment lease"))
            .values(sensitivity=states.PRIVATE)
        )


def test_a_set_of_reports_all_private_now_runs_on_this_mac_unjudged(
    home_and_local, tmp_path
):
    """Every report Private now: there's nothing for the judge to read, so the run
    goes unjudged rather than being refused, unless --judge was asked for."""
    anthropic, openai, _ollama = home_and_local
    private_lease()
    words = "Which pet deposit did my lease set, as I reported it?"
    with pytest.raises(run.NothingToJudge):
        run.evaluate([reported(question=words)], SOL, judge=True, limit_usd=50.0)
    report, _claims = run.evaluate(
        [reported(question=words)], SOL, tmp_path / "runs", limit_usd=50.0
    )
    (row,) = keeping.Kept(Path(report["kept"])).rows()
    assert report["judged"] is False
    assert row["private"] is True
    assert row["state"] == score.ANSWERED
    assert not any(words in r.raw for r in [*anthropic.requests, *openai.requests])


def test_a_set_of_reports_all_private_now_is_estimated_and_run_unjudged(
    home_and_local, tmp_path, capsys
):
    """The estimate and the providers' check take the run as it will go: no judge,
    so a run that may send only to OpenAI isn't refused for Anthropic's."""
    anthropic, _openai, _ollama = home_and_local
    private_lease()
    mine = tmp_path / "my_questions.json"
    mine.write_text(json.dumps([reported()]))
    helper = "openai:gpt-6-luna"
    ended = cli.main(
        [
            *("--questions", str(mine), "--model", SOL, "--helper-model", helper),
            *("--only-providers", "openai", "--limit-usd", "5"),
        ]
    )
    printed = capsys.readouterr().out
    assert ended == 0
    assert "to judge" not in printed.splitlines()[0], "the estimate names no judge"
    assert "of 1 reported failures now pass" in printed
    assert anthropic.requests == []


WORDS = "Which pet deposit did my lease set, as I reported it?"


def test_a_report_made_private_before_its_turn_is_asked_on_this_mac(
    home_and_local, tmp_path
):
    """Each report was once classed only as the run began. The lease goes Private as the
    question before it is judged: the report is read again just before it's asked, and
    asked on this Mac."""
    anthropic, openai, _ollama = home_and_local

    def private_as_the_first_is_judged(path: str, body: dict) -> dict:
        private_lease()
        return judged(path, body)

    anthropic.reply = private_as_the_first_is_judged
    report, _claims = run.evaluate(
        [QUESTION, reported(question=WORDS)], SOL, tmp_path / "runs", limit_usd=50.0
    )
    rows = {r["id"]: r for r in keeping.Kept(Path(report["kept"])).rows()}
    assert rows["r-1"]["private"] is True
    assert rows["r-1"]["model"] != SOL, "answered on this Mac"
    assert anthropic.requests, "the question before it was judged"
    assert not any(WORDS in r.raw for r in [*anthropic.requests, *openai.requests])
    # Listed as having become Private, with why.
    assert report["reported_failures"]["became_private"] == [
        {"id": "r-1", "why": "Apartment lease is in a Private folder"}
    ]


def test_a_report_made_private_while_it_is_asked_sends_nothing_more(
    home_and_local, tmp_path
):
    """A document the report's answer rested on, not one its questions find, goes
    Private as the report's earlier question is answered: every later request rests
    on it too, so policy refuses the report's own question as it leaves, and the row
    is saved as Private."""
    anthropic, openai, _ollama = home_and_local
    roof = document("Roof inspection report")
    earlier = "What does my lease say about pets?"

    def private_as_the_earlier_is_answered(path: str, body: dict) -> dict:
        sent = json.dumps(body)
        if earlier in sent and WORDS not in sent:
            with database.write() as connection:
                connection.execute(
                    sa.update(database.documents)
                    .where(database.documents.c.id == roof)
                    .values(sensitivity=states.PRIVATE)
                )
        return answered(path, body)

    openai.reply = private_as_the_earlier_is_answered
    mine = reported(
        question=WORDS,
        earlier=[earlier],
        rested_on={"documents": [roof], "memories": []},
    )
    report, _claims = run.evaluate([mine], SOL, tmp_path / "runs", limit_usd=50.0)
    (row,) = keeping.Kept(Path(report["kept"])).rows()
    cloud = [*anthropic.requests, *openai.requests]
    assert any(earlier in r.raw for r in cloud), "the earlier one went, as Standard"
    assert not any(WORDS in r.raw for r in cloud), "nothing after it changed"
    assert row["private"] is True
    assert report["reported_failures"]["became_private"] == [
        {
            "id": "r-1",
            "why": "the answer rested on Roof inspection report, which is Private",
        }
    ]
