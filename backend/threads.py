"""Thread and message records, the run state machine, scope, memory-off and taint."""

import json
import time
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from backend import database, refusals, states
from backend.database import (
    collection_items,
    documents,
    messages,
    run_events,
    runs,
    thread_documents,
    thread_evidence,
    threads,
)

DEFAULT_SCOPE = {
    "kind": "library",
    "include_private": False,
    "label": "All · Private excluded",
}
RUNNING = "running"
PAUSED = "paused"
COMPLETED = "completed"
STOPPED = "stopped"
FAILED = "failed"
SUPERSEDED = "superseded"
ACTIVE = (RUNNING, PAUSED)
# Long enough for a follow-up. A longer run than the limit is taken to be stuck.
ASKING_AFTER_SECONDS = 30
ASKING_AT_MOST_SECONDS = 600
TITLE_CHARACTERS = 60
PAGE = 50
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
CARD_CHOICES = {
    "private_in_scope": ("local", "leave"),
    "deep_cost": ("deep", "standard"),
}


def scope_document_ids(
    connection: Connection, scope: dict[str, Any]
) -> list[str] | None:
    """The documents a scope names; None for the whole library."""
    if scope["kind"] == "collection":
        return list(
            connection.execute(
                sa.select(collection_items.c.document_id).where(
                    collection_items.c.collection_id == scope["id"]
                )
            ).scalars()
        )
    if scope["kind"] in ("documents", "search"):
        return list(scope["ids"])
    return None


def private_documents_in(
    connection: Connection, scope: dict[str, Any]
) -> list[dict[str, str]]:
    """The Private documents a scope names."""
    ids = scope_document_ids(connection, scope)
    if not ids:
        return []
    rows = connection.execute(
        sa.select(documents.c.id, documents.c.title).where(
            documents.c.id.in_(ids),
            documents.c.sensitivity == states.PRIVATE,
            documents.c.status != states.REMOVED,
        )
    ).all()
    return [{"id": row.id, "title": row.title} for row in rows]


def _starts_private(connection: Connection, scope: dict[str, Any]) -> bool:
    return bool(scope.get("include_private")) or bool(
        private_documents_in(connection, scope)
    )


def create(scope: dict[str, Any] | None, mode: str) -> dict[str, Any]:
    """A new thread with a Private selection or "All · Private included" is Private
    from its start."""
    chosen = scope or DEFAULT_SCOPE
    now = time.time()
    thread_id = uuid.uuid4().hex
    with database.write() as connection:
        connection.execute(
            threads.insert().values(
                id=thread_id,
                title="New chat",
                scope=chosen,
                mode=mode,
                private_tainted=_starts_private(connection, chosen),
                created_at=now,
                updated_at=now,
            )
        )
    return get(thread_id)


def get(thread_id: str) -> dict[str, Any]:
    with database.read() as connection:
        row = (
            connection.execute(sa.select(threads).where(threads.c.id == thread_id))
            .mappings()
            .first()
        )
    if row is None:
        raise refusals.not_found("There's no such chat.")
    return dict(row)


def page(offset: int = 0, limit: int = PAGE) -> list[dict[str, Any]]:
    """Pinned first, then the latest, a page at a time."""
    query = sa.select(threads).order_by(
        threads.c.pinned.desc(), threads.c.updated_at.desc(), threads.c.id
    )
    with database.read() as connection:
        rows = connection.execute(query.limit(limit).offset(offset)).mappings()
        return [dict(r) for r in rows]


def update(thread_id: str, **values: Any) -> None:
    allowed = {"title", "pinned", "memory_off"}
    if set(values) - allowed:
        raise refusals.invalid("That can't be changed on a chat.")
    with database.write() as connection:
        moved = connection.execute(
            threads.update().where(threads.c.id == thread_id).values(**values)
        )
    if moved.rowcount != 1:
        raise refusals.not_found("There's no such chat.")


def delete(thread_id: str) -> None:
    with database.write() as connection:
        active = connection.execute(
            sa.select(runs.c.id).where(
                runs.c.thread_id == thread_id, runs.c.state.in_(ACTIVE)
            )
        ).first()
        if active:
            raise refusals.conflict("Stop this chat's answer before deleting it.")
        ids = sa.select(runs.c.id).where(runs.c.thread_id == thread_id)
        connection.execute(run_events.delete().where(run_events.c.run_id.in_(ids)))
        connection.execute(runs.delete().where(runs.c.thread_id == thread_id))
        connection.execute(messages.delete().where(messages.c.thread_id == thread_id))
        connection.execute(
            thread_documents.delete().where(thread_documents.c.thread_id == thread_id)
        )
        connection.execute(
            thread_evidence.delete().where(thread_evidence.c.thread_id == thread_id)
        )
        connection.execute(threads.delete().where(threads.c.id == thread_id))


def set_scope(thread_id: str, scope: dict[str, Any], consented: bool) -> dict[str, Any]:
    """The only way a scope changes, and only the owner calls it. Adding Private to a
    Standard thread asks first, and answering yes makes it Private for good."""
    with database.write() as connection:
        thread = connection.execute(
            sa.select(threads.c.private_tainted).where(threads.c.id == thread_id)
        ).first()
        if thread is None:
            raise refusals.not_found("There's no such chat.")
        adding_private = _starts_private(connection, scope)
        if adding_private and not thread.private_tainted and not consented:
            return {
                "needs_consent": True,
                "documents": private_documents_in(connection, scope),
            }
        connection.execute(
            threads.update()
            .where(threads.c.id == thread_id)
            .values(
                scope=scope,
                private_tainted=bool(thread.private_tainted) or adding_private,
                ask_when_indexed=None,
            )
        )
    return {"needs_consent": False, "documents": []}


def taint(thread_id: str) -> None:
    with database.write() as connection:
        connection.execute(
            threads.update()
            .where(threads.c.id == thread_id)
            .values(private_tainted=True)
        )
        connection.execute(
            messages.update()
            .where(messages.c.thread_id == thread_id)
            .values(sensitivity=states.PRIVATE)
        )


def _add_documents_in(
    connection: Connection, thread_id: str, document_ids: list[str]
) -> list[str]:
    known = connection.execute(
        sa.select(sa.func.coalesce(sa.func.max(thread_documents.c.ordinal), 0)).where(
            thread_documents.c.thread_id == thread_id
        )
    ).scalar_one()
    added = []
    for offset, document_id in enumerate(document_ids, start=1):
        made = connection.exec_driver_sql(
            "INSERT OR IGNORE INTO thread_documents "
            "(thread_id, document_id, ordinal) VALUES (?, ?, ?)",
            (thread_id, document_id, known + offset),
        )
        if made.rowcount:
            added.append(document_id)
    return added


def add_documents(thread_id: str, document_ids: list[str]) -> None:
    """The thread's ordered documents, which follow-ups resolve against."""
    with database.write() as connection:
        _add_documents_in(connection, thread_id, document_ids)


def _is_live(connection: Connection, run_id: str) -> bool:
    return (
        connection.execute(
            sa.select(runs.c.id).where(runs.c.id == run_id, runs.c.state.in_(ACTIVE))
        ).first()
        is not None
    )


def _note_undo(connection: Connection, run_id: str, **values: Any) -> None:
    undo = connection.execute(
        sa.select(runs.c.undo).where(runs.c.id == run_id)
    ).scalar()
    connection.execute(
        runs.update().where(runs.c.id == run_id).values(undo={**(undo or {}), **values})
    )


def _taint_if_any_private(
    connection: Connection, thread_id: str, document_ids: list[str]
) -> None:
    """A source made Private as the answer was written: nothing ties it to this chat
    yet, so the taint that move makes would have missed it. For good, from here."""
    private = connection.execute(
        sa.select(documents.c.id)
        .where(
            documents.c.id.in_(document_ids),
            documents.c.sensitivity == states.PRIVATE,
        )
        .limit(1)
    ).first()
    if private is None:
        return
    connection.execute(
        threads.update().where(threads.c.id == thread_id).values(private_tainted=True)
    )
    connection.execute(
        messages.update()
        .where(messages.c.thread_id == thread_id)
        .values(sensitivity=states.PRIVATE)
    )


def save_turn(
    thread_id: str,
    run_id: str,
    message: dict[str, Any],
    document_ids: list[str],
    rested_on: list[str] | None = None,
) -> bool:
    """The answer and the documents it brought, or nothing for a run already stopped:
    work a cancel didn't reach can still finish after it."""
    with database.write() as connection:
        if not _is_live(connection, run_id):
            return False
        evidence = rested_on or document_ids
        _taint_if_any_private(connection, thread_id, evidence)
        for document_id in evidence:
            connection.execute(
                sa.insert(thread_evidence)
                .prefix_with("OR IGNORE")
                .values(thread_id=thread_id, document_id=document_id)
            )
        add_message_in(connection, thread_id, run_id, message)
        added = _add_documents_in(connection, thread_id, document_ids)
        _note_undo(connection, run_id, documents=added)
    return True


def discussed(thread_id: str) -> list[dict[str, str]]:
    with database.read() as connection:
        rows = connection.execute(
            sa.select(documents.c.id, documents.c.title)
            .join(thread_documents, thread_documents.c.document_id == documents.c.id)
            .where(thread_documents.c.thread_id == thread_id)
            .order_by(thread_documents.c.ordinal)
        ).all()
    return [{"id": row.id, "title": row.title} for row in rows]


def transcript(thread_id: str) -> list[dict[str, Any]]:
    with database.read() as connection:
        rows = connection.execute(
            sa.select(messages)
            .where(messages.c.thread_id == thread_id)
            .order_by(messages.c.ordinal)
        ).mappings()
        return [dict(row) for row in rows]


def add_message_in(
    connection: Connection, thread_id: str, run_id: str, message: dict[str, Any]
) -> str:
    """Idempotent on the run and role, so a replayed node can't write a turn twice."""
    role = message["role"]
    existing = connection.execute(
        sa.select(messages.c.id).where(
            messages.c.run_id == run_id, messages.c.role == role
        )
    ).scalar()
    if existing:
        return existing
    ordinal = connection.execute(
        sa.select(sa.func.coalesce(sa.func.max(messages.c.ordinal), 0)).where(
            messages.c.thread_id == thread_id
        )
    ).scalar_one()
    tainted = connection.execute(
        sa.select(threads.c.private_tainted).where(threads.c.id == thread_id)
    ).scalar()
    message_id = uuid.uuid4().hex
    connection.execute(
        messages.insert().values(
            id=message_id,
            thread_id=thread_id,
            run_id=run_id,
            ordinal=ordinal + 1,
            role=role,
            content=message["content"],
            answer=message.get("answer"),
            sensitivity=states.PRIVATE if tainted else states.STANDARD,
            created_at=time.time(),
        )
    )
    now = time.time()
    connection.execute(
        threads.update().where(threads.c.id == thread_id).values(updated_at=now)
    )
    return message_id


def set_title_if_new(thread_id: str, question: str) -> None:
    title = " ".join(question.split())[:TITLE_CHARACTERS]
    with database.write() as connection:
        connection.execute(
            threads.update()
            .where(threads.c.id == thread_id, threads.c.title == "New chat")
            .values(title=title)
        )


# ---- runs ----


def start_run(thread_id: str, question: str, checkpoint_id: str | None) -> str:
    """An active running run refuses a second; a new message supersedes a paused one."""
    now = time.time()
    run_id = uuid.uuid4().hex
    try:
        with database.write() as connection:
            connection.execute(
                threads.update()
                .where(threads.c.id == thread_id)
                .values(ask_when_indexed=None)
            )
            connection.execute(
                runs.insert().values(
                    id=run_id,
                    thread_id=thread_id,
                    state=RUNNING,
                    checkpoint_id=checkpoint_id,
                    question=question,
                    created_at=now,
                    updated_at=now,
                )
            )
            add_message_in(
                connection,
                thread_id,
                run_id,
                {"role": ROLE_USER, "content": question},
            )
    except IntegrityError as taken:
        raise refusals.conflict("An answer is already running in this chat.") from taken
    return run_id


def active_run(thread_id: str) -> dict[str, Any] | None:
    with database.read() as connection:
        row = (
            connection.execute(
                sa.select(runs).where(
                    runs.c.thread_id == thread_id, runs.c.state.in_(ACTIVE)
                )
            )
            .mappings()
            .first()
        )
    return dict(row) if row else None


def get_run(run_id: str) -> dict[str, Any]:
    with database.read() as connection:
        row = (
            connection.execute(sa.select(runs).where(runs.c.id == run_id))
            .mappings()
            .first()
        )
    if row is None:
        raise refusals.not_found("There's no such answer.")
    return dict(row)


def _move(
    run_id: str,
    from_states: tuple[str, ...],
    to: str,
    answering: str | None = None,
    **values: Any,
) -> bool:
    """One conditional update. With `answering`, only while that card is the one
    waiting: a late reply to a card since replaced moves nothing."""
    where = [runs.c.id == run_id, runs.c.state.in_(from_states)]
    if answering is not None:
        where.append(sa.func.json_extract(runs.c.card, "$.id") == answering)
    with database.write() as connection:
        moved = connection.execute(
            runs.update()
            .where(*where)
            .values(state=to, updated_at=time.time(), **values)
        )
    return moved.rowcount == 1


def pause_run(run_id: str, card: dict[str, Any]) -> bool:
    return _move(run_id, (RUNNING,), PAUSED, card=card)


def resume_run(run_id: str, card_id: str, choice: str) -> None:
    run = get_run(run_id)
    if run["state"] != PAUSED or (run["card"] or {}).get("id") != card_id:
        raise refusals.conflict("That question has already been answered.")
    card = run["card"]
    if choice not in CARD_CHOICES[card["kind"]]:
        raise refusals.invalid("That isn't an answer to this question.")
    # Kept with the card it answers: what was shown is what was agreed to.
    answered = {
        **(run.get("answered") or {}),
        card["kind"]: {"card": card, "choice": choice},
    }
    if not _move(run_id, (PAUSED,), RUNNING, card_id, card=None, answered=answered):
        raise refusals.conflict("That question has already been answered.")


def card_answer(run_id: str, kind: str) -> dict[str, Any] | None:
    """The owner's answer to this run's last card of this kind, with that card."""
    with database.read() as connection:
        found = connection.execute(
            sa.select(runs.c.answered).where(runs.c.id == run_id)
        ).scalar()
    return (found or {}).get(kind)


def complete_run(run_id: str) -> bool:
    return _move(run_id, (RUNNING,), COMPLETED)


def _undo_context(connection: Connection, run_id: str, thread_id: str) -> None:
    """Takes back what Save changed. A summary cleared since, by a deleted memory, stays
    cleared: the old one may quote it."""
    undo = (
        connection.execute(sa.select(runs.c.undo).where(runs.c.id == run_id)).scalar()
        or {}
    )
    added = undo.get("documents") or []
    if added:
        connection.execute(
            thread_documents.delete().where(
                thread_documents.c.thread_id == thread_id,
                thread_documents.c.document_id.in_(added),
            )
        )
    summary = undo.get("summary")
    if summary is None:
        return
    connection.execute(
        threads.update()
        .where(
            threads.c.id == thread_id,
            threads.c.summary_version == summary["version"],
        )
        .values(
            summary=summary["text"],
            summary_through=summary["through"],
            summary_version=threads.c.summary_version + 1,
        )
    )


def owner_is_asking() -> bool:
    """Whether a question is being answered or was just now, so cards can wait."""
    now = time.time()
    with database.read() as connection:
        found = connection.execute(
            sa.select(runs.c.id)
            .where(
                sa.or_(
                    sa.and_(
                        runs.c.state == RUNNING,
                        runs.c.created_at > now - ASKING_AT_MOST_SECONDS,
                    ),
                    runs.c.updated_at > now - ASKING_AFTER_SECONDS,
                )
            )
            .limit(1)
        ).first()
    return found is not None


def stop_run(
    run_id: str, to: str = STOPPED, error: str | None = None
) -> dict[str, Any]:
    """Takes the run's messages and context back. Stop never undoes the taint or the
    egress log."""
    with database.write() as connection:
        moved = connection.execute(
            runs.update()
            .where(runs.c.id == run_id, runs.c.state.in_(ACTIVE))
            .values(state=to, updated_at=time.time(), error=error, card=None)
        )
        if moved.rowcount != 1:
            raise refusals.conflict("This answer has already finished.")
        thread_id = connection.execute(
            sa.select(runs.c.thread_id).where(runs.c.id == run_id)
        ).scalar_one()
        connection.execute(messages.delete().where(messages.c.run_id == run_id))
        _undo_context(connection, run_id, thread_id)
    return get_run(run_id)


def last_failure(thread_id: str) -> dict[str, Any] | None:
    """The last run, if it failed: its reason, and the question to ask again. The
    question's message went with the run, so its text comes from the run's own row."""
    with database.read() as connection:
        row = connection.execute(
            sa.select(runs.c.state, runs.c.error, runs.c.question)
            .where(runs.c.thread_id == thread_id)
            .order_by(runs.c.created_at.desc())
            .limit(1)
        ).first()
    if row is None or row.state != FAILED:
        return None
    return {"error": row.error, "question": row.question}


def dead_runs() -> list[dict[str, Any]]:
    """Runs still marked running at startup died with their process."""
    with database.read() as connection:
        rows = connection.execute(
            sa.select(runs).where(runs.c.state == RUNNING)
        ).mappings()
        return [dict(row) for row in rows]


def add_event(run_id: str, event: str, data: dict[str, Any]) -> int:
    with database.write() as connection:
        made = connection.execute(
            run_events.insert().values(run_id=run_id, event=event, data=data)
        )
    return made.lastrowid


def events_after(run_id: str, after: int) -> list[dict[str, Any]]:
    with database.read() as connection:
        rows = connection.execute(
            sa.select(run_events)
            .where(run_events.c.run_id == run_id, run_events.c.id > after)
            .order_by(run_events.c.id)
        ).mappings()
        return [dict(row) for row in rows]


def clear_events(run_id: str) -> None:
    with database.write() as connection:
        connection.execute(run_events.delete().where(run_events.c.run_id == run_id))


def scope_label(scope: dict[str, Any]) -> str:
    return str(scope.get("label") or json.dumps(scope))


def ask_when_indexed(thread_id: str, text: str | None) -> None:
    with database.write() as connection:
        connection.execute(
            threads.update()
            .where(threads.c.id == thread_id)
            .values(ask_when_indexed=text)
        )


def waiting_for_indexing() -> list[dict[str, Any]]:
    with database.read() as connection:
        rows = connection.execute(
            sa.select(threads.c.id, threads.c.ask_when_indexed, threads.c.mode).where(
                threads.c.ask_when_indexed.is_not(None)
            )
        ).mappings()
        return [dict(row) for row in rows]


def set_memory_through(thread_id: str, ordinal: int) -> None:
    with database.write() as connection:
        connection.execute(
            threads.update()
            .where(threads.c.id == thread_id)
            .values(memory_through=ordinal)
        )


def idle_since(before: float) -> list[str]:
    with database.read() as connection:
        return list(
            connection.execute(
                sa.select(threads.c.id).where(threads.c.updated_at < before)
            ).scalars()
        )


def idle_for_memory(before: float) -> list[str]:
    """Threads quiet since `before` with turns memory hasn't seen."""
    latest = (
        sa.select(sa.func.max(messages.c.ordinal))
        .where(messages.c.thread_id == threads.c.id)
        .scalar_subquery()
    )
    with database.read() as connection:
        return list(
            connection.execute(
                sa.select(threads.c.id).where(
                    threads.c.updated_at < before,
                    threads.c.memory_off.is_(False),
                    latest > threads.c.memory_through,
                )
            ).scalars()
        )


def set_summary(
    thread_id: str, summary: str, through: int, run_id: str | None = None
) -> None:
    """For a run, written only while it's live, with what's needed to take it back."""
    with database.write() as connection:
        if run_id is not None and not _is_live(connection, run_id):
            return
        was = connection.execute(
            sa.select(
                threads.c.summary, threads.c.summary_through, threads.c.summary_version
            ).where(threads.c.id == thread_id)
        ).one()
        connection.execute(
            threads.update()
            .where(threads.c.id == thread_id)
            .values(
                summary=summary,
                summary_through=through,
                summary_version=was.summary_version + 1,
            )
        )
        if run_id is not None:
            _note_undo(
                connection,
                run_id,
                summary={
                    "text": was.summary,
                    "through": was.summary_through,
                    "version": was.summary_version + 1,
                },
            )
