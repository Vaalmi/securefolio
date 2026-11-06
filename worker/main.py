"""The worker: claims jobs by priority and runs them, one at a time."""

import json
import logging
import os
import signal
import sys
import threading
import time
from typing import Any

from backend import (
    database,
    documents,
    index,
    jobs,
    logs,
    macos,
    netguard,
    revisions,
    settings,
    sources,
    states,
    threads,
    vault,
)
from backend.migrations import run as migrations
from worker import (
    batches,
    hard_pages,
    housekeeping,
    organize,
    pacing,
    pipeline,
    scan,
    unlock,
    watcher,
)

logger = logging.getLogger(__name__)
HEARTBEAT_SECONDS = 30
IDLE_SECONDS = 1.0
SCAN_EVERY_SECONDS = 3600
WAKE_GAP_SECONDS = 120


def _read_stdin(stop: threading.Event) -> None:
    """Unlock lines from the backend. Its end means the backend has died."""
    for line in sys.stdin:
        try:
            message = json.loads(line)
            unlock.receive(message["job_id"], message["password"])
        except (ValueError, KeyError):
            logger.warning("an unreadable line on stdin")
    stop.set()


def _heartbeats(job_id: str, finished: threading.Event) -> None:
    while not finished.wait(HEARTBEAT_SECONDS):
        jobs.heartbeat(job_id)


def _run_scan(job: dict[str, Any]) -> None:
    try:
        scan.run(job)
        jobs.succeed(job["id"])
    except scan.ScanStopped:
        jobs.finish_cancelled(job["id"])
        sources.settle_scans_with_no_job()
    except OSError as error:
        source_id = job["source_id"] or job["payload"]["source_id"]
        reason = f"Couldn't read this folder: {error.strerror or 'it is not there'}."
        sources.fail_scan(source_id, reason)
        jobs.fail(job["id"], reason, "scan")


def _simple(work: Any) -> Any:
    def run(job: dict[str, Any]) -> None:
        work(job)
        jobs.succeed(job["id"])

    return run


def _local_ready(_job: dict[str, Any]) -> None:
    """The waiting cards and pictures, back on the queue for the model now chosen."""
    organize.queue_pending_when_ready()
    hard_pages.queue_pending_when_ready()


HANDLERS = {
    jobs.LOCAL_READY: _simple(_local_ready),
    jobs.SCAN: _run_scan,
    jobs.INGEST: pipeline.run,
    jobs.HARD_PAGES: hard_pages.run,
    jobs.ORGANIZE: _simple(organize.run),
    jobs.OPTIMIZE: _simple(housekeeping.run_optimize),
}


def run_job(job: dict[str, Any]) -> None:
    finished = threading.Event()
    threading.Thread(
        target=_heartbeats, args=(job["id"], finished), daemon=True
    ).start()
    try:
        HANDLERS[job["kind"]](job)
    except Exception:
        # One bad job never takes the queue down. The type alone is logged.
        logger.exception("job %s failed", job["id"])
        jobs.fail(job["id"], "Something went wrong while working on this.", "internal")
    finally:
        finished.set()


def requeue_expired() -> None:
    for job in jobs.requeue_expired():
        with database.write() as connection:
            documents.requeue_after_lease_in(connection, job)
        if job["kind"] == jobs.SCAN and job["state"] == states.FAILED:
            source_id = job["source_id"] or job["payload"].get("source_id")
            if source_id:
                sources.fail_scan(source_id, job["reason"])


def startup() -> None:
    requeue_expired()
    sources.settle_scans_with_no_job()
    index.sweep()
    scan.enqueue_timer_scans()


def background_passes() -> None:
    """The slow timers' work. One failing doesn't stop the queue."""
    try:
        organize.queue_pending_when_ready()
        hard_pages.queue_pending_when_ready()
        revisions.sweep_retired()
        batches.tick()
    except Exception:
        logger.exception("a background pass failed")


class Timers:
    """The hourly scan, and one after the Mac wakes: a jump in the wall clock that
    the monotonic clock didn't see means it slept."""

    def __init__(self) -> None:
        self.last_scan = time.time()
        self.last_tick = time.time()
        self.was_busy = False
        self.last_pass = 0.0

    def tick(self, now: float) -> None:
        woke = now - self.last_tick > WAKE_GAP_SECONDS
        self.last_tick = now
        if woke or now - self.last_scan >= SCAN_EVERY_SECONDS:
            self.last_scan = now
            scan.enqueue_timer_scans()
        if now - self.last_pass >= batches.POLL_SECONDS:
            self.last_pass = now
            background_passes()


def work_until(stop: threading.Event) -> None:
    pace = pacing.Pacing()
    timers = Timers()
    while not stop.is_set():
        timers.tick(time.time())
        unlock.settle(jobs.is_live, jobs.document_has_live_work)
        sources.settle_scans_with_no_job()
        pace.update_sleep_assertion()
        pacing.unload_idle_models()
        requeue_expired()
        # A card and an answer would share the local model, and the owner is waiting.
        held = (jobs.ORGANIZE,) if threads.owner_is_asking() else ()
        job = jobs.claim(held=held) if pace.may_work() else None
        if job is None:
            if timers.was_busy:
                jobs.enqueue(jobs.OPTIMIZE, jobs.PRIORITY_HOUSEKEEPING, key="optimize")
                timers.was_busy = False
            stop.wait(IDLE_SECONDS)
            continue
        timers.was_busy = job["kind"] != jobs.OPTIMIZE
        run_job(job)
    pace.stop()


def main() -> None:
    vault.use_model_cache()
    logs.configure("worker")
    migrations.upgrade()
    netguard.install(lambda: bool(settings.get(settings.OFFLINE)))
    macos.lower_priority(os.getpid())
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    threading.Thread(target=_read_stdin, args=(stop,), daemon=True).start()
    threading.Thread(target=watcher.run, args=(stop,), daemon=True).start()
    startup()
    work_until(stop)


if __name__ == "__main__":
    main()
