"""Builds the synthetic DocSynth v1 `metadata.db`, in the shape DocSynth v1's
metadata.db has, as assumed without a real v1 file to read."""

import json
import sqlite3
import sys
from pathlib import Path

TARGET = Path(__file__).parents[1] / "v1" / "metadata.db"
NOTES = [
    (
        "n1",
        "Roof quote",
        "Three quotes came in. Go with the middle one.",
        1_700_000_000.0,
    ),
    ("n2", "Lease reminder", "Give notice sixty days before the end.", 1_700_100_000.0),
]
SESSIONS = [
    (
        "s1",
        "Warranty question",
        json.dumps(
            [
                {"role": "user", "content": "When does the car warranty end?"},
                {"role": "assistant", "content": "March 2029, by the booklet."},
            ]
        ),
        1_700_200_000.0,
    ),
]
QUERIES = [("q1", "insurance renewal", 1_700_300_000.0)]


def build(target: Path = TARGET) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    connection = sqlite3.connect(target)
    with connection:
        connection.execute(
            "CREATE TABLE notes (id TEXT PRIMARY KEY, title TEXT, body TEXT, "
            "created_at REAL)"
        )
        connection.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, messages TEXT, "
            "created_at REAL)"
        )
        connection.execute(
            "CREATE TABLE saved_queries (id TEXT PRIMARY KEY, query TEXT, "
            "created_at REAL)"
        )
        connection.executemany("INSERT INTO notes VALUES (?, ?, ?, ?)", NOTES)
        connection.executemany("INSERT INTO sessions VALUES (?, ?, ?, ?)", SESSIONS)
        connection.executemany("INSERT INTO saved_queries VALUES (?, ?, ?)", QUERIES)
    connection.close()
    return target


if __name__ == "__main__":
    sys.stdout.write(f"{build()}\n")
