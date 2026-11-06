"""Turns file events into scan hints. Events can be lost; the scan is the truth."""

import logging
import threading
from collections.abc import Iterable
from pathlib import Path

from watchfiles import watch

from backend import jobs, sources, states

logger = logging.getLogger(__name__)
DEBOUNCE_MS = 3000
TIMEOUT_MS = 5000


def _folders() -> list[dict]:
    return [
        s
        for s in sources.live()
        if not s["builtin"]
        and s["status"] != states.PAUSED
        and Path(s["path"]).is_dir()
    ]


def hint(changes: Iterable[tuple[object, str]], folders: list[dict]) -> None:
    """Queues a scan of just the changed paths, per folder."""
    paths = [path for _change, path in changes]
    for folder in folders:
        root = Path(folder["path"]).resolve()
        mine = [p for p in paths if Path(p).resolve().is_relative_to(root)]
        if mine:
            jobs.enqueue(
                jobs.SCAN,
                jobs.PRIORITY_CHANGE,
                source_id=folder["id"],
                payload={"paths": mine},
            )


def run(stop: threading.Event) -> None:
    """Watches until told to stop, starting over when the folders change."""
    while not stop.is_set():
        folders = _folders()
        if not folders:
            stop.wait(TIMEOUT_MS / 1000)
            continue
        watched = {f["id"] for f in folders}
        for changes in watch(
            *[f["path"] for f in folders],
            stop_event=stop,
            debounce=DEBOUNCE_MS,
            yield_on_timeout=True,
            rust_timeout=TIMEOUT_MS,
        ):
            if changes:
                hint(changes, folders)
            if {f["id"] for f in _folders()} != watched:
                break
