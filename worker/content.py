"""What every parser hands on: elements, each with its locator, in reading order."""

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any

from backend import locators, tokens
from backend.locators import Locator


@dataclass
class Element:
    kind: str  # text, table, picture, or attachment
    text: str
    locator: Locator
    reading_state: str | None = None
    self_ref: str | None = None


@dataclass
class PageFacts:
    """What the first index learned about a PDF page, for the hard-page routing."""

    page: int
    mean_grade: str | None = None
    low_grade: str | None = None
    broken_text_layer: bool = False
    picture_boxes: list[list[float]] = field(default_factory=list)
    tables_empty_share: list[float] = field(default_factory=list)
    table_has_span: bool = False
    table_continues: bool = False
    empty_or_garbled: bool = False


@dataclass
class Parsed:
    elements: list[Element]
    chunks: list["Chunk"] = field(default_factory=list)
    pages: int | None = None
    page_facts: list[PageFacts] = field(default_factory=list)


@dataclass
class Chunk:
    text: str
    element_ordinals: list[int]


MAX_TOKENS = 1024
TARGET_TOKENS = 512


def estimate_tokens(text: str) -> int:
    return tokens.count(text)


def split_to_limit(text: str, limit: int = MAX_TOKENS) -> list[str]:
    """Pieces of the text, each within the limit, broken at whitespace if it can."""
    if limit <= 0:
        raise ValueError("A piece of text needs room for at least one token.")
    parts: list[str] = []
    remaining = text
    while estimate_tokens(remaining) > limit:
        cut = _largest_cut(remaining, limit)
        space = remaining.rfind(" ", 0, cut)
        cut = space if space > 0 else cut
        parts.append(remaining[:cut])
        remaining = remaining[cut:].lstrip()
    if remaining or not parts:
        parts.append(remaining)
    return parts


def _largest_cut(text: str, limit: int) -> int:
    low, high = 1, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_tokens(text[:middle]) <= limit:
            low = middle
        else:
            high = middle - 1
    return low


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def element_rows(elements: list[Element]) -> tuple[list[dict[str, Any]], list[str]]:
    """The database rows, and each element's new id by ordinal."""
    rows, ids = [], []
    for ordinal, element in enumerate(elements):
        element_id = uuid.uuid4().hex
        ids.append(element_id)
        rows.append(
            {
                "id": element_id,
                "ordinal": ordinal,
                "kind": element.kind,
                "text": element.text,
                "text_hash": text_hash(element.text),
                "locator": locators.dump(element.locator),
                "reading_state": element.reading_state,
            }
        )
    return rows, ids
