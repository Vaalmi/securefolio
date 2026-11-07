"""An answer's event stream ends only once it has sent the run's closing event. A run
moves to its last state first and writes `done` just after, so a stream that saw the
state and stopped would leave the answer showing "Writing the answer" for good."""

import asyncio
from typing import Any

import pytest

from backend import threads
from backend.migrations import run as migrations
from backend.routes import threads as thread_routes


@pytest.fixture(autouse=True)
def tables() -> None:
    migrations.upgrade()


class _Writer:
    """Stands in for the agent service: while the stream waits, the run writes what it
    has left, as `_finish` does after `complete_run`."""

    def __init__(self, run_id: str, left: list[tuple[str, dict[str, Any]]]) -> None:
        self.run_id = run_id
        self.left = left
        self.waits = 0

    async def wait_for_event(self, run_id: str, _seconds: float) -> None:
        self.waits += 1
        if self.left:
            event, data = self.left.pop(0)
            threads.add_event(run_id, event, data)


def _events(service: _Writer, after: int = 0) -> list[str]:
    async def follow() -> list[str]:
        sent = []
        async for chunk in thread_routes.run_stream(service, service.run_id, after):
            sent.append(chunk.split("\n")[1].removeprefix("event: "))
        return sent

    return asyncio.run(follow())


def _a_run() -> str:
    thread = threads.create(None, "standard")
    return threads.start_run(thread["id"], "What does the roof report recommend?", None)


def test_done_written_just_after_the_run_completes_is_still_sent():
    run_id = _a_run()
    threads.add_event(run_id, "token", {"text": "The roof"})
    threads.complete_run(run_id)
    service = _Writer(run_id, [("done", {"state": threads.COMPLETED})])
    assert _events(service) == ["token", "done"]


def test_a_pauses_card_written_just_after_it_pauses_is_still_sent():
    run_id = _a_run()
    threads.pause_run(run_id, {"id": "c1", "kind": "deep_cost"})
    service = _Writer(run_id, [("card", {"id": "c1", "kind": "deep_cost"})])
    assert _events(service) == ["card"]


def test_reattaching_after_the_closing_event_ends_at_once():
    run_id = _a_run()
    threads.complete_run(run_id)
    done = threads.add_event(run_id, "done", {"state": threads.COMPLETED})
    service = _Writer(run_id, [])
    assert _events(service, after=done) == []
    assert service.waits == 0


def test_a_run_that_stopped_without_a_closing_event_ends_after_its_grace(monkeypatch):
    monkeypatch.setattr(thread_routes, "CLOSING_GRACE_SECONDS", 0)
    run_id = _a_run()
    threads.stop_run(run_id, threads.FAILED, "It stopped.")
    assert _events(_Writer(run_id, [])) == []


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


class _Steps:
    """Stands in for the agent service: each wait moves the clock on and does the
    next thing the run would."""

    def __init__(self, run_id: str, clock: _Clock, steps: list) -> None:
        self.run_id = run_id
        self.clock = clock
        self.steps = steps

    async def wait_for_event(self, _run_id: str, _seconds: float) -> None:
        if self.steps:
            seconds, step = self.steps.pop(0)
            self.clock.now += seconds
            step()


def test_a_resumed_answer_still_sends_done_however_long_it_takes(monkeypatch):
    """The grace a stopped run gets starts again once it runs: a pause answered, then
    an answer longer than the grace, still ends on `done`."""
    clock = _Clock()
    monkeypatch.setattr(thread_routes, "time", clock)
    run_id = _a_run()
    card = {"id": "c1", "kind": "deep_cost"}
    threads.pause_run(run_id, card)

    def card_then_resumed() -> None:
        threads.add_event(run_id, "card", card)
        threads.resume_run(run_id, "c1", "standard")

    def completed() -> None:
        threads.complete_run(run_id)

    def done() -> None:
        threads.add_event(run_id, "done", {"state": threads.COMPLETED})

    steps = [(1, card_then_resumed), (30, completed), (1, done)]
    sent: list[str] = []

    async def follow() -> None:
        service = _Steps(run_id, clock, steps)
        async for chunk in thread_routes.run_stream(service, run_id, 0):
            sent.append(chunk.split("\n")[1].removeprefix("event: "))

    asyncio.run(follow())
    assert sent == ["card", "done"]
