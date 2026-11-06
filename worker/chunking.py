"""Chunks from elements, for a revision the second reading changed: tables keep
their rows, and one too long for a chunk splits by rows with its header repeated."""

from backend.locators import PdfLocator
from worker.content import (
    MAX_TOKENS,
    TARGET_TOKENS,
    Chunk,
    Element,
    estimate_tokens,
    split_to_limit,
)

HEADER_LINES = 2
MAX_HEADER_TOKENS = 256


def _split_table(text: str) -> list[str]:
    """By rows, with the header repeated; a row too long alone is split too."""
    lines = text.splitlines()
    header, rows = lines[:HEADER_LINES], lines[HEADER_LINES:]
    if estimate_tokens("\n".join(header)) > MAX_HEADER_TOKENS:
        return split_to_limit(text)  # too wide to repeat
    room = MAX_TOKENS - estimate_tokens("\n".join(header))
    parts: list[str] = []
    current: list[str] = []
    for row in rows:
        for piece in split_to_limit(row, room):
            if (
                current
                and estimate_tokens("\n".join([*header, *current, piece]))
                > TARGET_TOKENS
            ):
                parts.append("\n".join([*header, *current]))
                current = []
            current.append(piece)
    if current or not parts:
        parts.append("\n".join([*header, *current]))
    return parts


def from_elements(elements: list[Element]) -> list[Chunk]:
    chunks: list[Chunk] = []
    text: list[str] = []
    ordinals: list[int] = []

    def flush() -> None:
        if text:
            chunks.append(Chunk("\n\n".join(text), list(ordinals)))
        text.clear()
        ordinals.clear()

    for ordinal, element in enumerate(elements):
        if not element.text.strip():
            continue
        if element.kind == "table":
            flush()
            chunks += [Chunk(part, [ordinal]) for part in _split_table(element.text)]
        elif element.kind == "picture":
            flush()
            chunks += [Chunk(part, [ordinal]) for part in split_to_limit(element.text)]
        else:
            for piece in split_to_limit(element.text):
                if estimate_tokens("\n\n".join([*text, piece])) > TARGET_TOKENS:
                    flush()
                text.append(piece)
                ordinals.append(ordinal)
    flush()
    return chunks


def is_on_page(element: Element, page: int) -> bool:
    return isinstance(element.locator, PdfLocator) and element.locator.page == page
