"""The backend starts the worker again, up to a point, using a stand-in worker."""

import asyncio
import json
import sys

from backend import worker_supervisor
from backend.worker_supervisor import WorkerSupervisor

DIES_AT_ONCE = (sys.executable, "-c", "raise SystemExit(3)")
ECHOES = (
    sys.executable,
    "-c",
    "import sys, pathlib; pathlib.Path(sys.argv[1]).write_text(sys.stdin.readline())",
)


def test_a_worker_that_keeps_dying_is_stopped_after_five(monkeypatch):
    monkeypatch.setattr(worker_supervisor, "BACKOFF_SECONDS", 0.01)

    async def run() -> WorkerSupervisor:
        supervisor = WorkerSupervisor(DIES_AT_ONCE)
        supervisor.start()
        for _ in range(500):
            if supervisor.stopped_for_good:
                break
            await asyncio.sleep(0.02)
        return supervisor

    supervisor = asyncio.run(run())
    assert supervisor.state == "stopped"
    assert len(supervisor.deaths) == worker_supervisor.MAX_DEATHS


def test_a_killed_worker_is_restarted(monkeypatch):
    monkeypatch.setattr(worker_supervisor, "BACKOFF_SECONDS", 0.01)
    sleeper = (sys.executable, "-c", "import time; time.sleep(60)")

    async def run() -> bool:
        supervisor = WorkerSupervisor(sleeper)
        supervisor.start()
        await asyncio.sleep(0.2)
        first = supervisor.process
        assert first is not None
        assert supervisor.state == "running"
        first.kill()
        for _ in range(200):
            if supervisor.process is not first and supervisor.state == "running":
                break
            await asyncio.sleep(0.02)
        restarted = supervisor.process is not first
        await supervisor.stop()
        return restarted

    assert asyncio.run(run())


def test_an_unlock_line_reaches_the_worker_with_its_job_id(tmp_path):
    out = tmp_path / "line"
    command = (*ECHOES, str(out))

    async def run() -> bool:
        supervisor = WorkerSupervisor(command)
        supervisor.start()
        await asyncio.sleep(0.2)
        sent = await supervisor.forward_unlock("job-1", " pass word ")
        await asyncio.sleep(0.3)
        await supervisor.stop()
        return sent

    assert asyncio.run(run())
    assert json.loads(out.read_text()) == {"job_id": "job-1", "password": " pass word "}


def test_no_worker_means_the_unlock_isnt_sent():
    assert not asyncio.run(WorkerSupervisor(DIES_AT_ONCE).forward_unlock("j", "p"))
