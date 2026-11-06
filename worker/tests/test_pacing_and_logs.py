"""Power rules, the sleep assertion, the worker's loop, and logs without text."""

import json
import logging
import threading
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from backend import jobs, logs, macos, settings, sources, states, vault
from testing.queue import drain, next_job
from worker import main as worker_main
from worker import pacing, watcher


def battery(percent: int, plugged: bool):
    return lambda: SimpleNamespace(percent=percent, power_plugged=plugged)


@pytest.fixture(autouse=True)
def ready(library):
    pass


def test_jobs_wait_on_battery_at_or_below_twenty_percent(monkeypatch):
    pace = pacing.Pacing()
    monkeypatch.setattr(psutil, "sensors_battery", battery(20, False))
    assert not pace.may_work()
    monkeypatch.setattr(psutil, "sensors_battery", battery(21, False))
    assert pace.may_work()
    monkeypatch.setattr(psutil, "sensors_battery", battery(5, True))
    assert pace.may_work()
    monkeypatch.setattr(psutil, "sensors_battery", lambda: None)
    assert pace.may_work()


def test_the_menu_bars_pause_holds_every_job():
    pace = pacing.Pacing()
    settings.put(pacing.INDEXING_PAUSED, True)
    assert not pace.may_work()


def test_the_sleep_assertion_is_held_only_while_jobs_are_queued_on_power(monkeypatch):
    held = []

    class Assertion:
        def terminate(self):
            held.append("released")

    monkeypatch.setattr(
        macos,
        "hold_sleep_assertion",
        lambda _pid: (held.append("held"), Assertion())[1],
    )
    monkeypatch.setattr(psutil, "sensors_battery", battery(90, True))
    pace = pacing.Pacing()
    pace.update_sleep_assertion()
    assert held == []
    job_id = jobs.enqueue(jobs.SCAN, 1)
    pace.update_sleep_assertion()
    pace.update_sleep_assertion()
    assert held == ["held"]
    monkeypatch.setattr(psutil, "sensors_battery", battery(90, False))
    pace.update_sleep_assertion()
    assert held == ["held", "released"]
    monkeypatch.setattr(psutil, "sensors_battery", battery(90, True))
    pace.update_sleep_assertion()
    next_job()
    jobs.succeed(job_id)
    pace.update_sleep_assertion()
    assert held == ["held", "released", "held", "released"]
    pace.stop()


def test_one_bad_job_doesnt_stop_the_queue(monkeypatch):
    job_id = jobs.enqueue(jobs.OPTIMIZE, 1)

    def broken(_job):
        raise RuntimeError("boom")

    monkeypatch.setitem(worker_main.HANDLERS, jobs.OPTIMIZE, broken)
    worker_main.run_job(next_job())
    assert jobs.get(job_id)["state"] == states.FAILED


def test_the_loop_runs_jobs_then_stops_when_told(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "a.txt").write_text("words")
    sources.add_source(str(folder), states.STANDARD)
    stop = threading.Event()
    done = threading.Timer(1.5, stop.set)
    done.start()
    worker_main.work_until(stop)
    assert (
        sources.get(next(s["id"] for s in sources.live() if not s["builtin"]))["status"]
        == states.WATCHING
    )


def test_an_hourly_tick_and_a_wake_both_scan(tmp_path, monkeypatch):
    folder = tmp_path / "f"
    folder.mkdir()
    source = sources.add_source(str(folder), states.STANDARD)
    drain()
    timers = worker_main.Timers()
    timers.tick(timers.last_tick + 10)
    assert jobs.claim() is None
    timers.tick(timers.last_tick + worker_main.WAKE_GAP_SECONDS + 1)  # a wake
    woke = next_job()
    assert woke["kind"] == jobs.SCAN
    worker_main.run_job(woke)
    timers.last_tick = timers.last_scan + worker_main.SCAN_EVERY_SECONDS
    timers.tick(timers.last_scan + worker_main.SCAN_EVERY_SECONDS)  # an hour on
    assert next_job()["source_id"] == source["id"]


def test_watching_a_real_folder_hints_a_new_file(tmp_path, monkeypatch):
    folder = tmp_path / "f"
    folder.mkdir()
    sources.add_source(str(folder), states.STANDARD)
    drain()
    monkeypatch.setattr(watcher, "DEBOUNCE_MS", 50)
    monkeypatch.setattr(watcher, "TIMEOUT_MS", 200)
    stop = threading.Event()
    thread = threading.Thread(target=watcher.run, args=(stop,), daemon=True)
    thread.start()
    threading.Event().wait(0.5)
    (folder / "new.txt").write_text("a new file")
    found: list[str] = []
    for _ in range(50):
        job = jobs.claim()
        if job:
            found += job["payload"]["paths"]
            jobs.succeed(job["id"])
        if any(path.endswith("new.txt") for path in found):
            break
        threading.Event().wait(0.1)
    stop.set()
    thread.join(5)
    assert any(path.endswith("new.txt") for path in found)


def test_logs_hold_no_document_text(tmp_path):
    root = logging.getLogger()
    before = list(root.handlers)
    logs.configure("test")
    try:
        folder = tmp_path / "f"
        folder.mkdir()
        (folder / "a.txt").write_text("the planted-log-secret text")
        sources.add_source(str(folder), states.STANDARD)
        drain()
        logging.getLogger("x").info("job finished")
        try:
            _raise_planted()
        except ValueError:
            logging.getLogger("x").exception("failed")
        for handler in root.handlers:
            handler.flush()
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers = before
    text = (vault.logs_dir() / "test.log").read_text()
    assert "job finished" in text
    assert "planted-log-secret" not in text
    failed = json.loads(text.splitlines()[-1])
    assert failed["error"] == "ValueError"
    assert failed["where"][-1].startswith("worker/tests/test_pacing_and_logs.py:")
    assert failed["where"][-1].endswith(" _raise_planted")
    assert str(Path.home()) not in text


def _raise_planted() -> None:
    raise ValueError("planted-log-secret")


def test_cards_wait_while_the_owner_is_asking():
    jobs.enqueue(jobs.ORGANIZE, jobs.PRIORITY_HOUSEKEEPING, key="a card")
    assert jobs.claim(held=(jobs.ORGANIZE,)) is None
    job = jobs.claim()
    assert job is not None
    assert job["kind"] == jobs.ORGANIZE
