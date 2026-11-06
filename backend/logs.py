"""Local structured logs. Callers log ids and counts, never document text."""

import json
import logging
import sys
import traceback
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

from backend import vault

FRAMES = 12  # the innermost, where the error was raised
_CODE = Path(__file__).resolve().parent.parent


def _short(filename: str) -> str:
    """A path from the package down, so a log names no folder of the Mac."""
    if "site-packages/" in filename:
        return filename.rsplit("site-packages/", 1)[1]
    try:
        return str(Path(filename).resolve().relative_to(_CODE))
    except ValueError:
        return Path(filename).name


def _where(trace: TracebackType | None) -> list[str]:
    """File, line and function of each frame: code, never a value from it."""
    frames = traceback.extract_tb(trace)[-FRAMES:]
    return [f"{_short(f.filename)}:{f.lineno} {f.name}" for f in frames]


class _JsonLines(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, str | list[str]] = {
            "at": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            # The type and where it was raised only: a message can quote a document
            # or a password, and so can a frame's values, so none are kept.
            entry["error"] = record.exc_info[0].__name__
            entry["where"] = _where(record.exc_info[2])
        return json.dumps(entry)


def configure(name: str) -> None:
    directory = vault.logs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        directory / f"{name}.log", maxBytes=5_000_000, backupCount=3
    )
    handler.setFormatter(_JsonLines())
    root = logging.getLogger()
    root.handlers = [handler, logging.StreamHandler(sys.stderr)]
    root.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # a line per request
