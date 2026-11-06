"""Saved HTML through Docling, whose items carry no place in the page. This matches
each element back to a DOM anchor: the nearest id, or its path from the root."""

from lxml import html

from worker.hard_pages import normalize


def anchors_by_text(data: bytes) -> dict[str, str]:
    """Each block's normalized text, and an anchor for where it is."""
    root = html.fromstring(data)
    found: dict[str, str] = {}
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        text = normalize(element.text_content())
        if text and text not in found:
            found[text] = _anchor(element)
    return found


def _anchor(element: html.HtmlElement) -> str:
    node = element
    while node is not None:
        identifier = node.get("id")
        if identifier:
            return f"#{identifier}"
        node = node.getparent()
    return element.getroottree().getpath(element)
