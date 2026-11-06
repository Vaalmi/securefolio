"""First import on the packaged app: mixed synthetic documents on a temporary vault with
real models, unattended, with one kill partway. Records time, every failure's reason,
peak memory and search times during indexing, then checks nothing was lost or doubled.
Run: uv run python scripts/measure/first_import.py <app binary> [count]"""

import contextlib
import json
import os
import signal
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, cast

import httpx
import psutil

from data.build import library

POLL_SECONDS = 15
START_LIMIT = 300
QUERIES = ["rent deposit", "warranty end date", "insurance premium", "invoice total"]
GIB = 1024**3
KILL_AT_INDEXED = 250
REPORT = Path(__file__).parents[2] / ".cache" / "first_import.json"


def _generate(root: Path, count: int) -> int:
    seed, total = 1000, 0
    while total < count:
        out = root / f"set{seed}"
        builder = library.Builder(out, seed, prefix=f"S{seed} ")
        builder.build_all()
        builder.format_fixtures()
        total = sum(1 for p in root.rglob("*") if p.is_file())
        seed += 1
    return total


class App:
    def __init__(self, binary: str, vault: Path) -> None:
        self.binary = binary
        self.vault = vault
        self.process: subprocess.Popen | None = None
        self.client: httpx.Client | None = None

    def start(self) -> None:
        (self.vault / "runtime.json").unlink(missing_ok=True)
        environment = {
            **os.environ,
            "SECUREFOLIO_VAULT": str(self.vault),
            "SECUREFOLIO_MODEL_CACHE": os.environ["SECUREFOLIO_MODEL_CACHE"],
        }
        self.process = subprocess.Popen(  # noqa: S603
            [self.binary, "--headless"], env=environment, start_new_session=True
        )
        begun = time.monotonic()
        while not (self.vault / "runtime.json").exists():
            if time.monotonic() - begun > START_LIMIT:
                raise TimeoutError("the app didn't start")
            time.sleep(1)
        runtime = json.loads((self.vault / "runtime.json").read_text())
        self.client = httpx.Client(
            base_url=f"http://127.0.0.1:{runtime['port']}/api",
            headers={"Authorization": f"Bearer {runtime['app_token']}"},
            timeout=120,
            trust_env=False,
        )

    def api(self) -> httpx.Client:
        return cast("httpx.Client", self.client)

    def kill(self) -> None:
        """All of it at once, as a crash or a power cut would."""
        process = cast("subprocess.Popen", self.process)
        family = psutil.Process(process.pid).children(recursive=True)
        os.killpg(process.pid, signal.SIGKILL)
        for member in family:
            with contextlib.suppress(psutil.Error):
                member.kill()
        process.wait()

    def stop(self) -> None:
        """The backend outlives a shell that's only signalled, so it goes first."""
        if self.process and self.process.poll() is None:
            family = psutil.Process(self.process.pid).children(recursive=True)
            for member in family:
                with contextlib.suppress(psutil.Error):
                    member.terminate()
            os.killpg(self.process.pid, signal.SIGTERM)
            self.process.wait(timeout=60)

    def memory_gib(self) -> float:
        """Everything the app runs, Ollama aside."""
        process = psutil.Process(cast("subprocess.Popen", self.process).pid)
        tree = [process, *process.children(recursive=True)]
        total = 0
        for member in tree:
            with contextlib.suppress(psutil.Error):
                total += member.memory_info().rss
        return total / GIB


def _search_times(app: App) -> list[float]:
    found = []
    for query in QUERIES:
        begun = time.perf_counter()
        app.api().get("/search", params={"q": query, "include_private": "false"})
        found.append(time.perf_counter() - begun)
    return found


def _indexed_and_waiting(vault: Path) -> tuple[int, int]:
    """Documents indexed, and indexing jobs not yet finished. Cards come after, at low
    priority: a document is searchable before its full card is written."""
    connection = sqlite3.connect(vault / "library.db")
    try:
        indexed = connection.execute(
            "SELECT count(*) FROM documents WHERE status = 'indexed'"
        ).fetchone()[0]
        waiting = connection.execute(
            "SELECT count(*) FROM jobs WHERE state IN ('queued', 'running') "
            "AND kind != 'organize'"
        ).fetchone()[0]
    finally:
        connection.close()
    return indexed, waiting


def _drained(app: App) -> bool:
    live = app.api().get("/sources").json()
    scanning = [s for s in live if s["status"] == "scanning"]
    return not scanning and _indexed_and_waiting(app.vault)[1] == 0


def _cards(vault: Path) -> dict[str, int]:
    connection = sqlite3.connect(vault / "library.db")
    found = connection.execute(
        "SELECT state, count(*) FROM cards GROUP BY 1"
    ).fetchall()
    connection.close()
    return dict(found)


def _accounted(app: App, files: int) -> dict[str, Any]:
    rows, offset = [], 0
    while True:
        window = app.api().get("/library", params={"limit": 500, "offset": offset})
        found = window.json()["rows"]
        rows.extend(found)
        offset += len(found)
        if not found:
            break
    duplicates = len(rows) - len({r["id"] for r in rows})
    jobs = app.api().get("/jobs", params={"tab": "failed", "limit": 2000}).json()
    failed = jobs if isinstance(jobs, list) else jobs.get("rows", [])
    return {
        "files": files,
        "documents": len(rows),
        "duplicates": duplicates,
        "failed": len(failed),
        "failed_with_no_reason": sum(1 for j in failed if not j.get("detail")),
        "statuses": {
            status: sum(1 for r in rows if r["status"] == status)
            for status in {r["status"] for r in rows}
        },
    }


def main() -> None:
    binary = sys.argv[1]
    count = int(sys.argv[2]) if sys.argv[2:] else 1000
    work = Path(tempfile.mkdtemp(prefix="securefolio-import-"))
    folder = work / "documents"
    files = _generate(folder, count)
    vault = work / "vault"
    vault.mkdir()
    app = App(binary, vault)
    app.start()
    began = time.monotonic()
    app.api().post(
        "/sources",
        json={"path": str(folder), "sensitivity": "standard", "auto_organize": False},
    ).raise_for_status()
    killed, peak, times = False, 0.0, []
    while True:
        time.sleep(POLL_SECONDS)
        try:
            done = _drained(app)
            indexed, waiting = _indexed_and_waiting(vault)
            times.extend(_search_times(app))
        except httpx.HTTPError:
            continue
        peak = max(peak, app.memory_gib())
        if not killed and indexed > KILL_AT_INDEXED:
            app.kill()
            killed = True
            app.start()
            continue
        REPORT.write_text(
            json.dumps(
                {
                    "indexed": indexed,
                    "waiting": waiting,
                    "hours": (time.monotonic() - began) / 3600,
                }
            )
        )
        if done:
            break
    times.sort()
    REPORT.write_text(
        json.dumps(
            {
                "hours": round((time.monotonic() - began) / 3600, 2),
                "killed_once": killed,
                "peak_memory_gib": round(peak, 2),
                "search_p95_ms": round(times[int(len(times) * 0.95)] * 1000),
                "search_median_ms": round(statistics.median(times) * 1000),
                "cards": _cards(vault),
                **_accounted(app, files),
            },
            indent=1,
        )
    )
    app.stop()


if __name__ == "__main__":
    main()
