"""The words a picture carries: its captions, then the text drawn inside it, such as
a chart's labels and values. Docling keeps that text as the picture's children,
which neither its chunker nor a plain walk of the document visits."""

from typing import Any


def captions(item: Any, document: Any) -> str:
    if not hasattr(item, "captions_resolved"):
        return ""
    return " ".join(c.text for c in item.captions_resolved(document))


def inside(item: Any, document: Any) -> str:
    """The text inside the picture, a line for each of Docling's text items."""
    caption_refs = {ref.cref for ref in getattr(item, "captions", [])}
    lines = [
        child.text
        for child, _level in document.iterate_items(root=item, traverse_pictures=True)
        if child is not item
        and child.self_ref not in caption_refs
        and getattr(child, "text", "").strip()
    ]
    return "\n".join(lines)


def text_of(item: Any, document: Any) -> str:
    return "\n".join(
        part for part in (captions(item, document), inside(item, document)) if part
    )
