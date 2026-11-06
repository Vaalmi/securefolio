"""Serves a document's verified bytes to the viewer, with the sandbox headers."""

import hashlib
import importlib
import io
from dataclasses import dataclass
from pathlib import Path

import sqlalchemy as sa

from backend import database, documents, originals, refusals, states
from backend.database import revisions

SANDBOX = "default-src 'none'; sandbox"
ATTACHMENT_KINDS = {"HTML", "HTM", "SVG", "XML"}
MEDIA_TYPES = {
    "PDF": "application/pdf",
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "HEIC": "image/heic",
}
BINARY = "application/octet-stream"
CHANGED = "The original file changed after it was indexed."
GONE = "The original file isn't on this Mac."


@dataclass(frozen=True)
class Served:
    data: bytes
    media_type: str
    attachment: bool
    file_name: str


def _active_hash(document: dict) -> str | None:
    if document["active_revision_id"] is None:
        return document["content_sha256"]
    with database.read() as connection:
        return connection.execute(
            sa.select(revisions.c.content_sha256).where(
                revisions.c.id == document["active_revision_id"]
            )
        ).scalar()


def verified_bytes(document: dict) -> bytes:
    """The bytes the active revision was read from, or a refusal the viewer turns
    into its banner, since a highlight on other bytes points at the wrong place."""
    expected = _active_hash(document)
    newest: bytes | None = None
    for row in sorted(
        documents.locations(document["id"]),
        key=lambda r: r["storage_mode"] != states.MANAGED,
    ):
        path = Path(row["path"])
        seen = originals.look(path)
        if seen is None or seen.dataless:
            continue
        data = originals.read_bytes(path)
        if hashlib.sha256(data).hexdigest() == expected:
            return data
        newest = data
    if newest is not None:
        raise refusals.conflict(CHANGED)
    raise refusals.conflict(GONE)


def serve(document_id: str) -> Served:
    document = documents.get(document_id)
    kind = document["file_kind"]
    return Served(
        data=verified_bytes(document),
        media_type=BINARY
        if kind in ATTACHMENT_KINDS
        else MEDIA_TYPES.get(kind, BINARY),
        attachment=kind in ATTACHMENT_KINDS or kind not in MEDIA_TYPES,
        file_name=document["file_name"],
    )


def preview(document_id: str) -> bytes:
    """HEIC reaches the WebView as a JPEG."""
    document = documents.get(document_id)
    data = verified_bytes(document)
    image_module = importlib.import_module("PIL.Image")
    importlib.import_module("pillow_heif").register_heif_opener()
    buffer = io.BytesIO()
    image_module.open(io.BytesIO(data)).convert("RGB").save(buffer, "JPEG", quality=88)
    return buffer.getvalue()


def view_state(document: dict) -> str:
    """Which viewer state a document opens in: ready, missing, changed, not_downloaded
    or stored (indexed text with no original to show)."""
    if document["status"] == states.NOT_DOWNLOADED:
        return "not_downloaded"
    rows = documents.locations(document["id"])
    if document["status"] == states.MISSING or all(r["missing"] for r in rows):
        return "missing"
    if all(r["dataless"] for r in rows):
        return "not_downloaded" if document["active_revision_id"] is None else "stored"
    try:
        verified_bytes(document)
    except refusals.Refused as refused:
        return "changed" if refused.detail == CHANGED else "missing"
    return "ready"
