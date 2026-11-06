"""Loads the instructions for each model call."""

from functools import cache
from pathlib import Path

FOLDER = Path(__file__).parent / "instructions"


@cache
def load(name: str) -> str:
    return (FOLDER / f"{name}.md").read_text(encoding="utf-8").strip()
