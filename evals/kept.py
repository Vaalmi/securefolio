"""Kept runs: each evaluation run in a folder of its own, never overwritten. A run over
the synthetic library is kept in the repo's cache, outside git; one over a set of the
owner's own is kept in their vault. Rows hold document text, so nothing here logs."""

import datetime
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RUNNING = "running"
FINISHED = "finished"
STOPPED_AT_LIMIT = "stopped_at_limit"
FAILED = "failed"
ENDED = (FINISHED, STOPPED_AT_LIMIT, FAILED)
MANIFEST = "manifest.json"
ROWS = "rows.jsonl"
REPORT = "report.json"
UNSAFE = re.compile(r"[^\w.-]+")


class RunEnded(Exception):
    """A kept run that has ended never changes."""


class AlreadyRecorded(Exception):
    """Each question has one row in a run."""


class NotItsOwnFigures(Exception):
    """A run's report holds its own figures section, counted from its own rows."""


@dataclass
class Kept:
    folder: Path

    @property
    def manifest(self) -> dict[str, Any]:
        return json.loads((self.folder / MANIFEST).read_text())

    @property
    def state(self) -> str:
        return str(self.manifest["state"])

    def rows(self) -> list[dict[str, Any]]:
        path = self.folder / ROWS
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    def report(self) -> dict[str, Any] | None:
        path = self.folder / REPORT
        return json.loads(path.read_text()) if path.exists() else None

    def _running(self) -> dict[str, Any]:
        manifest = self.manifest
        if manifest["state"] != RUNNING:
            raise RunEnded(f"{self.folder.name} has {manifest['state']}")
        return manifest

    def row(self, row: dict[str, Any]) -> None:
        self._running()
        if any(kept["id"] == row["id"] for kept in self.rows()):
            raise AlreadyRecorded(f"{row['id']} already has a row")
        with (self.folder / ROWS).open("a") as rows:
            rows.write(json.dumps(row) + "\n")

    def _end(self, state: str, report: dict[str, Any] | None, **more: Any) -> None:
        manifest = self._running()
        name = self.folder.name
        if report is not None and (report.get("figures") or {}).get("run") != name:
            # Failed, never left running, and never kept with the wrong figures.
            manifest.update(state=FAILED, ended_at=_now(), reason="figures not its own")
            (self.folder / MANIFEST).write_text(json.dumps(manifest, indent=1))
            raise NotItsOwnFigures(f"{name}'s report has no figures of its own")
        if report is not None:
            (self.folder / REPORT).write_text(json.dumps(report, indent=1))
        manifest.update(state=state, ended_at=_now(), **more)
        (self.folder / MANIFEST).write_text(json.dumps(manifest, indent=1))

    def finish(self, report: dict[str, Any], asked: list[str], **more: Any) -> None:
        """`more` joins the manifest: what could only be known once the run was over,
        such as the context Ollama gave a model it loaded during the run."""
        recorded = {row["id"] for row in self.rows()}
        missing = [question for question in asked if question not in recorded]
        if missing:
            raise ValueError(f"no row for {', '.join(missing)}")
        self._end(FINISHED, report, **more)

    def stop_at_limit(self, report: dict[str, Any]) -> None:
        self._end(STOPPED_AT_LIMIT, report)

    def fail(self, reason: str) -> None:
        self._end(FAILED, None, reason=reason)


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def start(root: Path, model: str, manifest: dict[str, Any]) -> Kept:
    """A new folder, named for when it began and what answered."""
    began = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%S%fZ")
    folder = root / f"{began}-{UNSAFE.sub('_', model)}"
    folder.mkdir(parents=True, exist_ok=False)
    kept = Kept(folder)
    record = {**manifest, "state": RUNNING, "started_at": _now()}
    (folder / MANIFEST).write_text(json.dumps(record, indent=1))
    return kept


def finished_runs(root: Path) -> list[Kept]:
    """Every finished run under root, oldest first."""
    if not root.exists():
        return []
    found = [Kept(folder) for folder in sorted(root.iterdir()) if folder.is_dir()]
    return [
        kept
        for kept in found
        if (kept.folder / MANIFEST).exists() and kept.state == FINISHED
    ]
