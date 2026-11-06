"""Markdown and plain text, with line-range locators. Docling's Markdown path keeps
no source lines, so this small reader does."""

from backend.locators import LinesLocator
from worker.content import Element


def read(data: bytes) -> list[Element]:
    """One element per run of non-blank lines."""
    lines = data.decode("utf-8", errors="replace").splitlines()
    found: list[Element] = []
    start: int | None = None
    for number, line in enumerate(lines, start=1):
        if line.strip():
            start = number if start is None else start
        elif start is not None:
            found.append(_element(lines, start, number - 1))
            start = None
    if start is not None:
        found.append(_element(lines, start, len(lines)))
    return found


def _element(lines: list[str], start: int, end: int) -> Element:
    text = "\n".join(lines[start - 1 : end])
    return Element("text", text, LinesLocator(start=start, end=end))
