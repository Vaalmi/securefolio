"""A local run whose planning call never answers: the call ends at its limit, the
question carries on as with an unreadable plan, and the run's report counts the cut."""

import logging
import shutil

import pytest

from agent import api, models
from backend import sources, states
from backend.build_mode import STUB_OLLAMA_ENV
from evals import run
from evals.tests.local_runs import LOCAL, planning_after
from evals.tests.questions import DATA, PLANNED
from testing.queue import drain
from testing.stub_models import stub_ollama

STALL = 3.0


@pytest.fixture
def stalling(stubs, tmp_path, monkeypatch):
    home = tmp_path / "Home"
    shutil.copytree(DATA / "Home", home)
    sources.add_source(str(home), states.STANDARD)
    drain()
    with stub_ollama() as ollama:
        ollama.reply = planning_after(STALL)
        monkeypatch.setenv(STUB_OLLAMA_ENV, ollama.url)
        monkeypatch.delenv("LOCAL_MODEL", raising=False)
        monkeypatch.setitem(
            models.HELPER_LIMITS, api.Purpose.REWRITE, models.Limit(1024, 0.3)
        )
        yield ollama


def test_a_stalled_plan_is_cut_and_the_question_carries_on(stalling, tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="agent.models"):
        report, _claims = run.evaluate([PLANNED], LOCAL, tmp_path / "runs")
    row = report["rows"][0]
    assert row["state"] == "answered", "not failed: it carried on without a plan"
    outcomes = [(c["purpose"], c["outcome"]) for c in row["calls"]]
    assert ("rewrite", models.CUT) in outcomes
    assert ("answer", "sent") in outcomes
    assert report["helpers_cut"] == {"rewrite": 1}
    assert '"purpose": "rewrite", "by": "time", "cut": ' in caplog.text


def test_a_run_with_no_cut_counts_none(stubs):
    rows = [{"calls": [{"purpose": "rewrite", "outcome": "sent"}]}, {"calls": []}]
    assert run.helpers_cut(rows) == {}
