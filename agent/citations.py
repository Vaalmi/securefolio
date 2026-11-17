"""Maps a model's citations to the elements it was given, and marks what's uncited.
Pure functions: nothing here touches a store or a model."""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

# A marker names one passage, a list of them or a range: `[1]`, `[1, 2]`, `[1-3]`.
NAMED = r"\d+(?:\s*[-\u2013]\s*\d+)?"
MARKER_BODY = rf"{NAMED}(?:\s*[,;]\s*{NAMED})*"
MARKER = re.compile(rf"\s*\[({MARKER_BODY})\]")
MARKER_WHOLE = re.compile(MARKER_BODY)
RANGE = re.compile(r"(\d+)\s*[-\u2013]\s*(\d+)")
AFTER_STOP = re.compile(
    rf"([.!?])((?:[ \t]*\[{MARKER_BODY}\])+)"
)  # "monthly. [1]" cites "monthly"
# A sentence ends at its stop, or at a line's end: a list's heading isn't its first row.
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=\S)|(?<=:)\s*\n+(?=\S)|\n+(?=\S)")
SENTENCE_END = re.compile(r"(?<=[.!?]\s)|(?<=\n)")
SENTENCE_DONE = re.compile(r"[.!?:]$")
HAS_WORD = re.compile(r"\w{2,}")
NO_EVIDENCE = "I couldn't find enough evidence in this scope"
BRACKETED = re.compile(r"\[([^\[\]\n]*)\]")


@dataclass
class Built:
    parts: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)


def _citation_of(evidence: dict[str, Any], element: dict[str, Any], quote: str) -> dict:
    return {
        "document_id": evidence["document_id"],
        "revision_id": evidence["revision_id"],
        "element_id": element["id"],
        "title": evidence["title"],
        "label": element["label"],
        "locator": element["locator"],
        "quote": quote,
        "sensitivity": evidence["sensitivity"],
    }


def _number_for(built: Built, citation: dict[str, Any]) -> int:
    for known in built.citations:
        if known["element_id"] == citation["element_id"]:
            return known["n"]
    number = len(built.citations) + 1
    built.citations.append({**citation, "n": number})
    return number


def _uncited(built: Built, text: str) -> None:
    """Text with no citation, sentence by sentence. Joining words aren't claims."""
    for piece in SENTENCE.split(text):
        if piece.strip():
            built.parts.append(
                {"text": piece + " ", "uncited": bool(HAS_WORD.search(piece))}
            )


def _close_sentence(built: Built, text: str, numbers: list[int]) -> None:
    if not text.strip():
        return
    if numbers:
        built.parts.append({"text": text, "cite": numbers})
    else:
        built.parts.append({"text": text, "uncited": bool(HAS_WORD.search(text))})


def _claude_numbers(
    block: dict[str, Any], evidence: list[dict[str, Any]], built: Built
) -> list[int]:
    numbers = []
    for found in block.get("citations") or []:
        at = found.get("search_result_index")
        if not isinstance(at, int) or not 0 <= at < len(evidence):
            continue
        given = evidence[at]["elements"]
        start = found.get("start_block_index", 0)
        end = found.get("end_block_index", start + 1)
        for element in given[start : max(end, start + 1)]:
            citation = _citation_of(evidence[at], element, found.get("cited_text", ""))
            number = _number_for(built, citation)
            if number not in numbers:
                numbers.append(number)
    return numbers


def from_claude(content: list[Any] | str, evidence: list[dict[str, Any]]) -> Built:
    """Claude cites fragments, so a sentence counts as cited if any fragment of it is.
    A citation matching nothing is dropped."""
    built = Built()
    blocks = (
        [{"type": "text", "text": content}] if isinstance(content, str) else content
    )
    sentence = ""
    numbers: list[int] = []
    for raw in blocks:
        block = {"type": "text", "text": raw} if isinstance(raw, str) else raw
        if block.get("type") != "text" or not block.get("text"):
            continue
        found = _claude_numbers(block, evidence, built)
        if SENTENCE_DONE.search(sentence) and block["text"][0].isspace():
            # The block starts the next sentence: the one before keeps what it cited,
            # even a passage this block cites again.
            _close_sentence(built, sentence, numbers)
            sentence, numbers = "", list(found)
        else:
            numbers.extend(n for n in found if n not in numbers)
        pieces = SENTENCE_END.split(block["text"])
        for at, piece in enumerate(pieces):
            sentence += piece
            if at < len(pieces) - 1:
                _close_sentence(built, sentence, numbers)
                sentence, numbers = "", []
    _close_sentence(built, sentence, numbers)
    return built


def from_markers(
    text: str, sources: list[tuple[dict[str, Any], dict[str, Any]]]
) -> Built:
    """`[n]` markers, read whole, so `[10]` is never taken for `[1]`, and a list or a
    range, `[1, 2]`, `[1][2]` or `[1-3]`, read as each passage. Markers after a
    sentence's stop, as in "Due monthly. [1]", cite that sentence."""
    built = Built()
    for sentence in SENTENCE.split(AFTER_STOP.sub(r"\2\1", text.strip())):
        numbers = []
        for found in MARKER.findall(sentence):
            for at in _named(found, len(sources)):
                evidence, element = sources[at - 1]
                number = _number_for(
                    built, _citation_of(evidence, element, element["text"])
                )
                if number not in numbers:
                    numbers.append(number)
        plain = MARKER.sub("", sentence).strip()
        if not plain:
            continue
        if numbers:
            built.parts.append({"text": plain + " ", "cite": numbers})
        else:
            _uncited(built, plain)
    return built


def is_refusal(text: str) -> bool:
    """The reply is the refusal alone, however it ends: models write it with its
    stop, "I couldn't find enough evidence in this scope.", or without."""
    said = text.strip().rstrip(".!").rstrip()
    return said.casefold() == NO_EVIDENCE.casefold()


def has_valid_citation(built: Built) -> bool:
    return bool(built.citations)


def shape_of(text: str, sources: int) -> dict[str, int | bool]:
    """Counts about a reply, for the log when none of it could be cited: never a word
    of it. Every bracketed span is counted once, as a marker, `[1]`, `[1, 2]` or
    `[1-3]`, or as another kind, like `[sic]`, so the two never overlap."""
    spans = BRACKETED.findall(text)
    markers = [span for span in spans if MARKER_WHOLE.fullmatch(span)]
    return {
        "chars": len(text),
        "sources": sources,
        "markers": len(markers),
        "in_range": sum(1 for span in markers if _named(span, sources)),
        "other_brackets": len(spans) - len(markers),
        "said_none": NO_EVIDENCE.casefold() in text.casefold(),
    }


def _source_number(digits: str, sources: int) -> int | None:
    """The source a marker names, or None. Every decimal digit, `٠١` as much as `01`,
    is read as ASCII first, its leading zeros dropped, and its length compared before
    `int` reads it: a run of digits too long for `int` names no source."""
    plain = "".join(str(unicodedata.decimal(digit)) for digit in digits).lstrip("0")
    if not plain or len(plain) > len(str(sources)):
        return None
    number = int(plain)
    return number if 1 <= number <= sources else None


def _named(marker: str, sources: int) -> list[int]:
    """The passages a marker's text names, each once, in its order: each number of a
    list on its own, as a single marker is, and a range only when both its ends name
    a passage and it runs forward, so it can never reach past the passages handed."""
    named: list[int] = []
    for item in re.split(r"\s*[,;]\s*", marker.strip()):
        ends = RANGE.fullmatch(item)
        if ends:
            first, last = (_source_number(end, sources) for end in ends.groups())
            wanted = range(first, last + 1) if first and last and first <= last else []
        else:
            one = _source_number(item, sources)
            wanted = [one] if one else []
        named.extend(n for n in wanted if n not in named)
    return named


def plain_text(built: Built) -> str:
    return "".join(part["text"] for part in built.parts).strip()
