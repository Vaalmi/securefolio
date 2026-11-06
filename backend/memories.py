"""The memories table and its tombstones. What to remember is agent/memory.py's."""

import hashlib
import json
import re
import time
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend import database, refusals, states, vault
from backend.database import memories, memory_tombstones, threads

ACTIVE = "active"
DELETED = "deleted"
EXPIRED = "expired"
RECAP = "recap"
RECAP_SECONDS = 180 * 24 * 3600
SPACE = re.compile(r"\s+")


def normalized_hash(text: str) -> str:
    plain = SPACE.sub(" ", text.casefold()).strip()
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def all_active(sensitivities: list[str] | None = None) -> list[dict[str, Any]]:
    query = sa.select(memories).where(memories.c.state == ACTIVE)
    if sensitivities is not None:
        query = query.where(memories.c.sensitivity.in_(sensitivities))
    with database.read() as connection:
        return [dict(r) for r in connection.execute(query).mappings()]


def is_active(memory_id: str) -> bool:
    with database.read() as connection:
        return (
            connection.execute(
                sa.select(memories.c.id).where(
                    memories.c.id == memory_id, memories.c.state == ACTIVE
                )
            ).first()
            is not None
        )


def tombstones() -> list[dict[str, Any]]:
    with database.read() as connection:
        return [
            dict(r) for r in connection.execute(sa.select(memory_tombstones)).mappings()
        ]


def _is_private_source(connection: Connection, thread_id: str | None) -> bool:
    if thread_id is None:
        return False
    return bool(
        connection.execute(
            sa.select(threads.c.private_tainted).where(threads.c.id == thread_id)
        ).scalar()
    )


def add(fields: dict[str, Any]) -> str:
    """Private if its chat is Private when it's written, not when it was read."""
    memory_id = uuid.uuid4().hex
    now = time.time()
    expires = now + RECAP_SECONDS if fields["kind"] == RECAP else None
    with database.write() as connection:
        if _is_private_source(connection, fields.get("source_thread_id")):
            fields = {**fields, "sensitivity": states.PRIVATE}
        connection.execute(
            memories.insert().values(
                id=memory_id, state=ACTIVE, created_at=now, expires_at=expires, **fields
            )
        )
    return memory_id


def edit(
    memory_id: str,
    text: str,
    embedding: bytes | None,
    source: dict[str, Any] | None = None,
) -> None:
    """With a `source`, an extraction's update: it takes that chat's provenance, and a
    Private chat makes the memory Private. It never goes the other way."""
    values: dict[str, Any] = {"text": text, "embedding": embedding}
    with database.write() as connection:
        if source is not None:
            values.update(source)
            if _is_private_source(connection, source.get("source_thread_id")):
                values["sensitivity"] = states.PRIVATE
        moved = connection.execute(
            memories.update()
            .where(memories.c.id == memory_id, memories.c.state == ACTIVE)
            .values(**values)
        )
    if moved.rowcount != 1:
        raise refusals.conflict("This memory has been deleted.")


def set_pinned(memory_id: str, pinned: bool) -> None:
    """A pinned recap doesn't expire; unpinned, it does again."""
    with database.write() as connection:
        row = connection.execute(
            sa.select(memories.c.kind, memories.c.created_at).where(
                memories.c.id == memory_id, memories.c.state == ACTIVE
            )
        ).first()
        if row is None:
            raise refusals.conflict("This memory has been deleted.")
        if not pinned and row.kind != RECAP:
            raise refusals.conflict("Only a recap can be unpinned here.")
        expires = None if pinned else row.created_at + RECAP_SECONDS
        connection.execute(
            memories.update()
            .where(memories.c.id == memory_id)
            .values(pinned=pinned, expires_at=expires)
        )


def touch(memory_ids: list[str]) -> None:
    if not memory_ids:
        return
    with database.write() as connection:
        connection.execute(
            memories.update()
            .where(memories.c.id.in_(memory_ids))
            .values(last_used_at=time.time())
        )


def delete(memory_id: str, embedding: bytes | None) -> None:
    """A tombstone holds a hash and an embedding, never the text. Any summary quoting
    the memory is cleared at once, and rebuilt on that thread's next turn."""
    with database.write() as connection:
        row = (
            connection.execute(
                sa.select(memories).where(
                    memories.c.id == memory_id, memories.c.state == ACTIVE
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise refusals.conflict("This memory has already been deleted.")
        text_hash = normalized_hash(row["text"])
        connection.exec_driver_sql(
            "INSERT OR REPLACE INTO memory_tombstones "
            "(text_hash, embedding, created_at) VALUES (?, ?, ?)",
            (text_hash, embedding or row["embedding"], time.time()),
        )
        connection.execute(
            memories.update().where(memories.c.id == memory_id).values(state=DELETED)
        )
        _clear_summaries_in(connection, row)
    _append_to_ledger(text_hash)


def _quotes(summary: str, text: str) -> bool:
    return SPACE.sub(" ", text.casefold()).strip() in SPACE.sub(" ", summary.casefold())


def _clear_summaries_in(connection: Any, row: Any) -> None:
    candidates = connection.execute(
        sa.select(threads.c.id, threads.c.summary).where(threads.c.summary != "")
    ).all()
    for thread in candidates:
        if thread.id == row["source_thread_id"] or _quotes(thread.summary, row["text"]):
            connection.execute(
                threads.update()
                .where(threads.c.id == thread.id)
                .values(summary="", summary_version=threads.c.summary_version + 1)
            )


def _append_to_ledger(text_hash: str) -> None:
    """Sits beside library.db and in each backup. Restore applies the union."""
    with vault.tombstone_ledger().open("a", encoding="utf-8") as ledger:
        ledger.write(json.dumps({"text_hash": text_hash}) + "\n")


def deleted_sources(thread_id: str) -> set[str]:
    """Messages a deleted memory came from, which a rebuilt summary leaves out."""
    with database.read() as connection:
        lists = connection.execute(
            sa.select(memories.c.source_message_ids).where(
                memories.c.source_thread_id == thread_id,
                memories.c.state == DELETED,
            )
        ).scalars()
        return {message for found in lists for message in found or []}


def expire_recaps(now: float | None = None) -> int:
    moment = time.time() if now is None else now
    with database.write() as connection:
        moved = connection.execute(
            memories.update()
            .where(
                memories.c.state == ACTIVE,
                memories.c.pinned.is_(False),
                memories.c.expires_at.is_not(None),
                memories.c.expires_at < moment,
            )
            .values(state=EXPIRED)
        )
    return moved.rowcount
