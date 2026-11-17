"""A local run's report shows its prompts against the context they're fitted to, and a
kept row holds the passages as fitted."""

import logging
import shutil

import pytest

from agent import models
from backend import sources, states
from backend.build_mode import STUB_OLLAMA_ENV
from evals import run
from evals.tests.local_runs import LOCAL, planning_after
from evals.tests.questions import DATA, QUESTION
from testing.queue import drain
from testing.stub_models import stub_ollama


@pytest.fixture
def local(stubs, tmp_path, monkeypatch):
    home = tmp_path / "Home"
    shutil.copytree(DATA / "Home", home)
    sources.add_source(str(home), states.STANDARD)
    drain()
    with stub_ollama() as ollama:
        ollama.reply = planning_after(0.0)
        monkeypatch.setenv(STUB_OLLAMA_ENV, ollama.url)
        monkeypatch.delenv("LOCAL_MODEL", raising=False)
        yield ollama


def test_a_full_context_is_fitted_and_counted(local, tmp_path, monkeypatch, caplog):
    # Room for the rules and the reserve only: the best passage alone is handed over.
    monkeypatch.setattr(models, "LOCAL_CONTEXT", models.LOCAL_ANSWER_TOKENS)
    with caplog.at_level(logging.INFO, logger="agent.harness"):
        report, _claims = run.evaluate([QUESTION], LOCAL, tmp_path / "runs")
    row = report["rows"][0]
    assert row["state"] == "answered"
    assert len(row["written"]["handed"]) == 1, "the passages as fitted"
    prompts = report["local_prompts"]
    assert prompts["context"] == models.LOCAL_ANSWER_TOKENS
    assert prompts["answers"] == 1
    assert prompts["fitted"] == 1
    assert prompts["passages_dropped"] >= 1
    assert prompts["largest_counted"] > 0
    assert "a local prompt was fitted to its context" in caplog.text


def test_a_run_whose_prompts_fit_counts_none_fitted(stubs):
    rows = [
        {
            "calls": [
                {"purpose": "rewrite", "input_tokens": 90},
                {
                    "purpose": "answer",
                    "input_tokens": 2400,
                    "prompt_counted": 2600,
                    "passages_dropped": 0,
                },
            ]
        },
        {"calls": []},
    ]
    assert run.local_prompts(rows) == {
        "context": models.LOCAL_CONTEXT,
        "answers": 1,
        "fitted": 0,
        "passages_dropped": 0,
        "largest": 2400,
        "largest_counted": 2600,
    }
