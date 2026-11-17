"""While the evaluation runs, the idle timer is off: no memory from its threads, no
waiting question asked again, no backup, and the reranker kept loaded."""

import shutil

import pytest

from backend import agent_service, sources, states
from evals import run
from evals.tests.questions import DATA, QUESTION, SOL
from testing.queue import drain


@pytest.fixture
def lease(stubs, tmp_path):
    home = tmp_path / "Home"
    shutil.copytree(DATA / "Home", home)
    sources.add_source(str(home), states.STANDARD)
    drain()
    return stubs


def test_no_idle_work_while_the_evaluation_runs(lease, monkeypatch):
    anthropic, openai = lease
    anthropic.requests.clear()
    openai.requests.clear()
    touched: list[str] = []

    def noted(name):
        def note(*_given, **_named):
            touched.append(name)

        return note

    async def extract(_thread_id):
        touched.append("memory")

    async def ask_those_waiting(_self):
        touched.append("waiting")

    # A tick every 10 ms, and every thread past its five idle minutes at once.
    monkeypatch.setattr(agent_service, "TICK_SECONDS", 0.01)
    monkeypatch.setattr(agent_service, "IDLE_FOR_MEMORY_SECONDS", -3600)
    monkeypatch.setattr(agent_service.memory, "extract", extract)
    monkeypatch.setattr(agent_service.Service, "_ask_those_waiting", ask_those_waiting)
    monkeypatch.setattr(agent_service.backup, "run_if_due", noted("backup"))
    monkeypatch.setattr(agent_service.reranker, "unload_if_idle", noted("unload"))
    second = {**QUESTION, "id": "q2"}
    # Unset, so the run itself must turn the timer off; the fixture puts it back.
    monkeypatch.delenv(agent_service.NO_TICKS_ENV)
    report, _claims = run.evaluate([QUESTION, second], SOL, limit_usd=50.0)
    assert touched == []
    sent = [
        call
        for row in report["rows"]
        for call in row["calls"]
        if call["outcome"] == "sent"
    ]
    assert sent, "the questions' own calls were made"
    assert len(anthropic.requests) + len(openai.requests) == len(sent), (
        "no call outside the questions' own"
    )
