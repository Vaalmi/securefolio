"""Single .eml files, split into messages. Docling's email items carry no message,
so this reader keeps it. Attachments are listed, not indexed."""

import email
import email.policy
import re
from email.message import EmailMessage

from lxml import html

from backend.locators import MessageLocator
from worker.content import Element

REPLY_MARK = re.compile(
    r"^(On .{5,120} wrote:|-{2,} ?Original Message ?-{2,}|From: .+)$", re.MULTILINE
)


def read(data: bytes) -> list[Element]:
    message = email.message_from_bytes(data, policy=email.policy.default)
    assert isinstance(message, EmailMessage)  # noqa: S101 - the default policy's type
    found = [_header_element(message)]
    body = _body_text(message)
    for number, part in enumerate(_split_replies(body), start=1):
        found += _paragraphs(part, number)
    for attachment in message.iter_attachments():
        name = attachment.get_filename() or "unnamed attachment"
        found.append(
            Element(
                "attachment",
                f"Attachment: {name}",
                MessageLocator(message=1, start_line=1, end_line=1),
            )
        )
    return found


def _header_element(message: EmailMessage) -> Element:
    headers = [
        f"{key}: {message[key]}"
        for key in ("From", "To", "Date", "Subject")
        if message[key]
    ]
    return Element(
        "text",
        "\n".join(headers),
        MessageLocator(message=1, start_line=1, end_line=len(headers) or 1),
    )


def _body_text(message: EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    content = part.get_content()
    if part.get_content_type() == "text/html":
        # Text only: remote images and scripts never survive this.
        return html.fromstring(content).text_content()
    return content


def _split_replies(body: str) -> list[str]:
    """Newest message first, then each quoted one, with quote marks removed."""
    parts = []
    last = 0
    for mark in REPLY_MARK.finditer(body):
        parts.append(body[last : mark.start()])
        last = mark.end()
    parts.append(body[last:])
    cleaned = [
        "\n".join(line.lstrip("> ") for line in part.splitlines()).strip()
        for part in parts
    ]
    return [part for part in cleaned if part] or [""]


def _paragraphs(text: str, message: int) -> list[Element]:
    found = []
    start = None
    lines = text.splitlines()
    for number, line in enumerate([*lines, ""], start=1):
        if line.strip():
            start = number if start is None else start
        elif start is not None:
            chunk = "\n".join(lines[start - 1 : number - 1])
            locator = MessageLocator(
                message=message, start_line=start, end_line=number - 1
            )
            found.append(Element("text", chunk, locator))
            start = None
    return found
