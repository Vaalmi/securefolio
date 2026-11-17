"""An answer in words, its marks carried in words: what Copy puts on the clipboard,
what a note keeps, the chat's saved content and what a follow-up reads. The one place
an answer becomes plain text, so a mark can't be dropped on one path and kept on
another. Pure functions."""

from typing import Any

from agent import figures

COUNTED_WORDS = "I counted the documents in the library."
UNSHOWN_LEGEND = (
    f"{figures.UNSHOWN_WORDS}: the passage its sentence cites doesn't show this "
    "figure. It may have been worked out by the model."
)
UNCITED_LEGEND = (
    f"{figures.UNCITED_WORDS}: no passage given to the model supports this sentence."
)


def _unshown(part: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for f in part.get("figures") or [] if f["state"] == figures.UNSHOWN]


def sentence(part: dict[str, Any], numbered: bool = False) -> str:
    """One part in words: each unshown figure followed by "(not in its sources)", an
    uncited sentence ended by "(uncited)", and, numbered, its citations' markers."""
    text = part.get("text", "")
    for figure in sorted(_unshown(part), key=lambda f: f["end"], reverse=True):
        end = figure["end"]
        text = f"{text[:end]} {figures.UNSHOWN_WORDS}{text[end:]}"
    body = text.rstrip()
    tail = text[len(body) :]
    if part.get("uncited"):
        body += f" {figures.UNCITED_WORDS}"
    markers = "".join(f"[{n}]" for n in part.get("cite") or []) if numbered else ""
    return body + markers + tail if numbered else body + tail


def _rows(payload: dict[str, Any]) -> list[str]:
    table = payload.get("table")
    if not table:
        return []
    lines = [
        f"{row['title']}, {row['doc_date'] or 'no date'}: {row['amount_text']}"
        for row in table["rows"]
    ]
    if table["wants_total"]:
        lines.append(f"Total: ${table['total']:,.2f}")
    return lines


def text_of(payload: dict[str, Any]) -> str:
    """What a note keeps, the chat saves, and a follow-up reads: the words with their
    marks, and a list's rows and total."""
    if payload.get("counted"):
        return COUNTED_WORDS
    words = "".join(sentence(part) for part in payload.get("parts", []))
    rows = _rows(payload)
    return "\n".join([words, *rows] if words else rows) if rows else words


def copy_of(payload: dict[str, Any]) -> str:
    """What Copy puts on the clipboard: each sentence with its markers and its marks
    in words, a list's rows and total, a line per citation, and a line saying what
    each kind of mark means, when the answer has one. A count is copied as it reads."""
    parts = payload.get("parts", [])
    words = "".join(sentence(part, numbered=True) for part in parts)
    sources = [
        f"[{c['n']}] {c['title']}, {c['label']}" for c in payload.get("citations", [])
    ]
    legend = []
    if any(_unshown(part) for part in parts):
        legend.append(UNSHOWN_LEGEND)
    if any(part.get("uncited") for part in parts):
        legend.append(UNCITED_LEGEND)
    return "\n".join([words, *_rows(payload), *sources, *legend])
