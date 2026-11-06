"""The MCP tools: read-only, and only what the owner has opened to them. What's open
is read fresh each call, so a folder switched off or made Private just now is gone."""

import re
from typing import Any

import sqlalchemy as sa
from mcp.server.mcpserver import MCPServer
from sqlalchemy.engine import Connection

from backend import cards, database, refusals, search, states
from backend.database import (
    chunks,
    collection_items,
    collections,
    document_locations,
    documents,
    sources,
)

MCP_PREFIX = "/mcp"
NO_SUCH = "There's no such passage or document open to this tool."
# docsynth:// is the same link from before the rename, as older answers still hold.
URI = re.compile(r"^(?:securefolio|docsynth)://doc/([^/#]+)/rev/([^/#]+)#chunk=(.+)$")
FETCH_EXTRA = 3
MOST_RESULTS = 50


def _open_documents(connection: Connection) -> set[str]:
    """Standard and indexed, in folders with their MCP switch on, every one of them: a
    folder's switch off covers its copy of a file wherever else the file is."""
    closed = (
        sa.select(document_locations.c.document_id)
        .join(sources, sources.c.id == document_locations.c.source_id)
        .where(
            sa.or_(
                sources.c.mcp_enabled.is_(False),
                sources.c.sensitivity != states.STANDARD,
            )
        )
    )
    return set(
        connection.execute(
            sa.select(documents.c.id)
            .join(
                document_locations,
                document_locations.c.document_id == documents.c.id,
            )
            .where(
                documents.c.sensitivity == states.STANDARD,
                documents.c.status == states.INDEXED,
                documents.c.id.not_in(closed),
            )
        ).scalars()
    )


def _open_now() -> set[str]:
    with database.read() as connection:
        return _open_documents(connection)


def _uri(document_id: str, revision_id: str, chunk_id: str) -> str:
    return f"securefolio://doc/{document_id}/rev/{revision_id}#chunk={chunk_id}"


def search_library(query: str, limit: int) -> list[dict[str, Any]]:
    """Passages from the folders open to this tool, best first, each with a link."""
    with database.read() as connection:
        allowed = _open_documents(connection)
    if not allowed:
        return []
    wanted = max(1, min(limit, MOST_RESULTS))
    scope = search.Scope(include_private=False, document_ids=tuple(sorted(allowed)))
    found = search.retrieve(query, scope, wanted * FETCH_EXTRA)
    allowed = _open_now()  # asked again: retrieval takes a while
    return [
        {
            "uri": _uri(item["document_id"], item["revision_id"], item["chunk_id"]),
            "document_id": item["document_id"],
            "title": item["title"],
            "where": item["elements"][0]["label"] if item["elements"] else "",
            "text": item["text"],
        }
        for item in found[:wanted]
        if item["document_id"] in allowed
    ]


def _passage_row(connection: Connection, document_id: str, chunk_id: str) -> Any:
    return connection.execute(
        sa.select(chunks.c.ordinal, chunks.c.text, chunks.c.revision_id)
        .join(documents, documents.c.id == chunks.c.document_id)
        .where(
            chunks.c.id == chunk_id,
            chunks.c.document_id == document_id,
            documents.c.active_revision_id == chunks.c.revision_id,
        )
    ).first()


def _neighbor(connection: Connection, row: Any, ordinal: int) -> str | None:
    """Within this revision of this document, so it stays inside what was opened."""
    return connection.execute(
        sa.select(chunks.c.text).where(
            chunks.c.revision_id == row.revision_id, chunks.c.ordinal == ordinal
        )
    ).scalar()


def read_passage(uri: str) -> dict[str, Any]:
    """A passage by its link, with the passages either side of it in that document."""
    found = URI.match(uri)
    if found is None:
        raise refusals.not_found(NO_SUCH)
    document_id, _revision, chunk_id = found.groups()
    with database.read() as connection:
        row = None
        if document_id in _open_documents(connection):
            row = _passage_row(connection, document_id, chunk_id)
        if row is None:
            raise refusals.not_found(NO_SUCH)
        return {
            "uri": _uri(document_id, row.revision_id, chunk_id),
            "text": row.text,
            "before": _neighbor(connection, row, row.ordinal - 1),
            "after": _neighbor(connection, row, row.ordinal + 1),
        }


def get_document(document_id: str) -> dict[str, Any]:
    """A document's card and details, with a link that opens it in SecureFolio."""
    with database.read() as connection:
        if document_id not in _open_documents(connection):
            raise refusals.not_found(NO_SUCH)
        row = (
            connection.execute(
                sa.select(
                    documents.c.title,
                    documents.c.file_name,
                    documents.c.doc_type,
                    documents.c.doc_date,
                ).where(documents.c.id == document_id)
            )
            .mappings()
            .one()
        )
    card = cards.get(document_id)
    if document_id not in _open_now():  # asked again, now the card is in hand
        raise refusals.not_found(NO_SUCH)
    return {
        **dict(row),
        "id": document_id,
        "link": f"securefolio://doc/{document_id}",
        "summary": card["summary"],
        "dates": card["dates"],
        "people": card["people"],
        "organizations": card["organizations"],
        "amounts": card["amounts"],
        "tags": card["tags"],
    }


def list_collections() -> list[dict[str, Any]]:
    """Collections wholly open to this tool. One holding anything that isn't is left
    out, as its name and size would say something about it."""
    with database.read() as connection:
        allowed = _open_documents(connection)
        rows = connection.execute(sa.select(collections.c.id, collections.c.name)).all()
        found = []
        for row in rows:
            members = set(
                connection.execute(
                    sa.select(collection_items.c.document_id).where(
                        collection_items.c.collection_id == row.id
                    )
                ).scalars()
            )
            if members and members <= allowed:
                found.append(
                    {"id": row.id, "name": row.name, "documents": sorted(members)}
                )
    return found


def build() -> MCPServer:
    server = MCPServer("SecureFolio")
    for tool in (search_library, read_passage, get_document, list_collections):
        server.add_tool(tool)
    return server
