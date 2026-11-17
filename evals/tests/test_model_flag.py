"""`npm run eval -- --model <id>` and `--record`: another model answers, the judge
stays Claude Opus 5.5, and the scores mark the model as measured in the catalog."""

import json
import shutil

import pytest
import sqlalchemy as sa

from agent import models
from backend import (
    database,
    model_catalog,
    settings,
    sources,
    states,
)
from backend.build_mode import STUB_OLLAMA_ENV
from evals import cli, run
from evals.tests.fake_runs import fake_evaluate
from evals.tests.questions import DATA, QUESTION, SOL
from testing.queue import drain
from testing.stub_models import (
    ollama_reply,
    stub_ollama,
)

LUNA = "openai:gpt-6-luna"  # not measured
SCORES = {
    "recall_at_10": 0.9,
    "citation_precision": 0.95,
    "answers_correct": 0.9,
    "unanswerable": 1.0,
}


def test_another_model_answers_and_opus_still_judges(stubs, tmp_path):
    anthropic, openai = stubs
    home = tmp_path / "Home"
    shutil.copytree(DATA / "Home", home)
    sources.add_source(str(home), states.STANDARD)
    drain()
    before = settings.get(settings.MODEL_ANSWER)
    report, _claims = run.evaluate([QUESTION], SOL, limit_usd=50.0)
    assert report["answer_model"] == SOL
    assert report["answered_by"] == [SOL]
    answers = [r for r in openai.requests if '<source id=\\"' in r.raw]
    assert answers, "the answer went to OpenAI"
    assert {r.body["model"] for r in answers} == {"gpt-6.1-sol"}
    assert models.JUDGE_MODEL == "anthropic:claude-opus-5-5"
    judges = [r for r in anthropic.requests if r.body.get("model") == "claude-opus-5-5"]
    assert judges, "Opus judged the citations"
    assert settings.get(settings.MODEL_ANSWER) == before, "for this run only"


def test_a_private_question_answered_on_this_mac_isnt_a_fallback(
    stubs, tmp_path, measured_file, monkeypatch
):
    # In a live run, the calibration set's two Private questions were answered by the
    # local model, by policy, for every Answer model, so --record never passed.
    home, medical = tmp_path / "Home", tmp_path / "Medical"
    shutil.copytree(DATA / "Home", home)
    shutil.copytree(DATA / "Medical", medical)
    sources.add_source(str(home), states.STANDARD)
    sources.add_source(str(medical), states.PRIVATE)
    drain()
    private = {
        **QUESTION,
        "id": "q2",
        "category": "private",
        "question": "What dose was I prescribed at the clinic?",
        "expected_document": "Medical/Clinic visit.pdf",
        "expected_page": None,
        "answer": None,
        "private": True,
    }
    with stub_ollama() as local:
        local.reply = ollama_reply
        monkeypatch.setenv(STUB_OLLAMA_ENV, local.url)
        report, _claims = run.evaluate([QUESTION, private], SOL, limit_usd=50.0)
    assert report["answered_by"] == [SOL]
    assert report["private_answered_by"] == [models.local_tag()]
    run.record_measured(SOL, report, "2026-11-05")
    assert SOL in json.loads(measured_file.read_text())["models"]


def test_the_vault_never_holds_the_runs_model(stubs, tmp_path, monkeypatch):
    # A run stopped partway leaves the vault as it was.
    home = tmp_path / "Home"
    shutil.copytree(DATA / "Home", home)
    sources.add_source(str(home), states.STANDARD)
    drain()
    before = settings.get(settings.MODEL_ANSWER)
    seen = []

    def stopped(_client, _questions, _kept=None, _judge=True):
        seen.append(settings.get(settings.MODEL_ANSWER))
        with database.read() as connection:
            stored = connection.execute(
                sa.select(database.settings.c.value).where(
                    database.settings.c.key == settings.MODEL_ANSWER
                )
            ).scalar()
        seen.append(stored)
        raise KeyboardInterrupt

    monkeypatch.setattr(run, "_ask_all", stopped)
    with pytest.raises(KeyboardInterrupt):
        run.evaluate([QUESTION], SOL, limit_usd=50.0)
    assert seen[0] == SOL, "this run answers with it"
    assert seen[1] != SOL, "but the vault never stored it"
    assert settings.get(settings.MODEL_ANSWER) == before


def test_answers_a_fallback_wrote_arent_recorded(library, measured_file, monkeypatch):
    # Offline, say, and the local model answered instead.
    report = {**SCORES, "answer_model": LUNA, "answered_by": ["gemma4:26b"]}
    with pytest.raises(run.NotRecorded):
        run.record_measured(LUNA, report, "2026-11-05")
    assert LUNA not in json.loads(measured_file.read_text())["models"]
    mixed = {**report, "answered_by": [LUNA, "gemma4:26b"]}
    with pytest.raises(run.NotRecorded):
        run.record_measured(LUNA, mixed, "2026-11-05")


def test_a_model_it_doesnt_know_is_refused_before_anything_runs(stubs):
    with pytest.raises(run.NotAModel):
        run.evaluate([QUESTION], "openai:no-such-model", limit_usd=50.0)


def is_measured(model_id: str) -> bool:
    found = model_catalog.get(model_id)
    assert found is not None
    return found.measured


def test_record_marks_the_model_measured(library, measured_file):
    assert is_measured("openai:gpt-6-luna") is False
    report = {**SCORES, "answered_by": ["openai:gpt-6-luna"]}
    run.record_measured("openai:gpt-6-luna", report, "2026-11-05")
    saved = json.loads(measured_file.read_text())["models"]["openai:gpt-6-luna"]
    assert saved["measured_on"] == "2026-11-05"
    assert saved["citation_precision"] == 0.95
    assert is_measured("openai:gpt-6-luna") is True
    assert is_measured("anthropic:claude-sonnet-5-5") is True
    assert is_measured("openai:gpt-6.1-sol") is True  # measured in a live run
    assert is_measured("anthropic:claude-haiku-5-5") is False


def test_the_command_line_records_the_model_that_answered(
    library, measured_file, monkeypatch, tmp_path
):
    seen = []
    found = {**SCORES, "answer_model": SOL, "answered_by": [SOL], "rows": []}
    monkeypatch.setattr(run, "evaluate", fake_evaluate(found, seen=seen))
    monkeypatch.setattr(cli, "REPORTS", tmp_path / "reports")
    cli.main(["--model", SOL, "--record", "--skip-hard-pages", "--limit-usd", "50"])
    assert seen == [SOL]
    assert SOL in json.loads(measured_file.read_text())["models"]


def test_record_is_only_for_the_calibration_set(tmp_path):
    own = tmp_path / "mine.json"
    own.write_text("[]")
    with pytest.raises(SystemExit):
        cli.main(["--questions", str(own), "--record"])
