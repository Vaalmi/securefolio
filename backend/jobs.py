"""The job table's state machine. Every move is one conditional update."""

import time
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend import database, refusals, states
from backend.database import job_events, jobs

SCAN = "scan"
INGEST = "ingest"
HARD_PAGES = "hard_pages"
ORGANIZE = "organize"
BACKUP = "backup"
REINDEX = "reindex"
OPTIMIZE = "optimize"
# A local model for cards or figures is chosen or downloaded: the work waiting for it
# goes back on the queue now, not at the next slow pass.
LOCAL_READY = "local_ready"

# Highest first: dropped files; live changes; bulk scans; hard pages; Organize;
# housekeeping.
PRIORITY_DROP = 0
PRIORITY_CHANGE = 10
PRIORITY_BULK = 20
PRIORITY_HARD_PAGES = 30
PRIORITY_ORGANIZE = 40
PRIORITY_HOUSEKEEPING = 50

LEASE_SECONDS = 120
MAX_ATTEMPTS = 3


def _event(connection: Connection, job_id: str, event: str) -> None:
    connection.execute(
        job_events.insert().values(job_id=job_id, at=time.time(), event=event)
    )


def live_payload(connection: Connection, key: str) -> dict[str, Any] | None:
    """The payload of the live job that holds this key, if there is one."""
    found = connection.execute(
        sa.select(jobs.c.payload).where(
            jobs.c.idempotency_key == key,
            jobs.c.state.in_((states.QUEUED, states.RUNNING)),
        )
    ).scalar()
    return dict(found) if found is not None else None


def enqueue_in(connection: Connection, kind: str, priority: int, **fields: Any) -> str:
    """Queues a job, or returns the live job that already holds its key. Its fields
    are any of: document_id, source_id, key, payload, stage."""
    key = fields.get("key")
    if key is not None:
        live = connection.execute(
            sa.select(jobs.c.id).where(
                jobs.c.idempotency_key == key,
                jobs.c.state.in_((states.QUEUED, states.RUNNING)),
            )
        ).scalar()
        if live:
            return live
    now = time.time()
    job_id = uuid.uuid4().hex
    connection.execute(
        jobs.insert().values(
            id=job_id,
            kind=kind,
            priority=priority,
            document_id=fields.get("document_id"),
            source_id=fields.get("source_id"),
            idempotency_key=key,
            state=states.QUEUED,
            stage=fields.get("stage"),
            payload=fields.get("payload") or {},
            created_at=now,
            updated_at=now,
        )
    )
    _event(connection, job_id, "queued")
    return job_id


def enqueue(kind: str, priority: int, **fields: Any) -> str:
    with database.write() as connection:
        return enqueue_in(connection, kind, priority, **fields)


def claim(
    allowed_kinds: tuple[str, ...] | None = None, held: tuple[str, ...] = ()
) -> dict[str, Any] | None:
    """Takes the most urgent queued job whose folder isn't paused, with a lease."""
    with database.write() as connection:
        sources = database.sources
        paused = sa.select(sources.c.id).where(sources.c.status == states.PAUSED)
        query = (
            sa.select(jobs)
            .where(
                jobs.c.state == states.QUEUED,
                sa.or_(jobs.c.source_id.is_(None), jobs.c.source_id.not_in(paused)),
            )
            .order_by(jobs.c.priority, jobs.c.created_at)
            .limit(1)
        )
        if allowed_kinds is not None:
            query = query.where(jobs.c.kind.in_(allowed_kinds))
        if held:
            query = query.where(jobs.c.kind.not_in(held))
        row = connection.execute(query).mappings().first()
        if row is None:
            return None
        now = time.time()
        connection.execute(
            jobs.update()
            .where(jobs.c.id == row["id"], jobs.c.state == states.QUEUED)
            .values(
                state=states.RUNNING,
                lease_expires_at=now + LEASE_SECONDS,
                updated_at=now,
            )
        )
        _event(connection, row["id"], "running")
        return {**row, "state": states.RUNNING}


def heartbeat(job_id: str) -> bool:
    """Extends the lease; False when the lease was taken."""
    now = time.time()
    with database.write() as connection:
        moved = connection.execute(
            jobs.update()
            .where(jobs.c.id == job_id, jobs.c.state == states.RUNNING)
            .values(lease_expires_at=now + LEASE_SECONDS, updated_at=now)
        )
    return moved.rowcount == 1


def advance_stage(job_id: str, stage: str) -> bool:
    """Records the stage reached. False means cancel was asked for: stop now."""
    with database.write() as connection:
        moved = connection.execute(
            jobs.update()
            .where(
                jobs.c.id == job_id,
                jobs.c.state == states.RUNNING,
                jobs.c.cancel_requested.is_(False),
            )
            .values(stage=stage, updated_at=time.time())
        )
        if moved.rowcount == 1:
            _event(connection, job_id, f"stage {stage}")
    return moved.rowcount == 1


def requeue_expired(now: float | None = None) -> list[dict[str, Any]]:
    """Returns jobs whose lease ran out to the queue, or fails them on their third
    attempt, and hands back what moved so the caller can move their documents."""
    now = time.time() if now is None else now
    moved = []
    with database.write() as connection:
        rows = (
            connection.execute(
                sa.select(jobs).where(
                    jobs.c.state == states.RUNNING, jobs.c.lease_expires_at < now
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            attempts = row["attempts"] + 1
            exhausted = attempts >= MAX_ATTEMPTS
            state = states.FAILED if exhausted else states.QUEUED
            reason = "It stopped partway too many times." if exhausted else None
            code = "stopped" if exhausted else None
            connection.execute(
                jobs.update()
                .where(jobs.c.id == row["id"], jobs.c.state == states.RUNNING)
                .values(
                    state=state,
                    attempts=attempts,
                    lease_expires_at=None,
                    reason=reason,
                    error_code=code,
                    updated_at=now,
                )
            )
            _event(connection, row["id"], f"lease expired, {state}")
            moved.append(
                {
                    **row,
                    "state": state,
                    "attempts": attempts,
                    "reason": reason,
                    "error_code": code,
                }
            )
    return moved


def _finish(job_id: str, state: str, reason: str | None, code: str | None) -> None:
    with database.write() as connection:
        moved = connection.execute(
            jobs.update()
            .where(jobs.c.id == job_id, jobs.c.state == states.RUNNING)
            .values(
                state=state,
                reason=reason,
                error_code=code,
                lease_expires_at=None,
                updated_at=time.time(),
            )
        )
        if moved.rowcount != 1:
            raise refusals.conflict("This job isn't running.")
        _event(connection, job_id, state)


def succeed(job_id: str) -> None:
    _finish(job_id, states.SUCCEEDED, None, None)


def skip(job_id: str, reason: str, code: str) -> None:
    _finish(job_id, states.SKIPPED, reason, code)


def fail(job_id: str, reason: str, code: str) -> None:
    _finish(job_id, states.FAILED, reason, code)


def finish_cancelled(job_id: str) -> None:
    _finish(job_id, states.CANCELLED, None, None)


def get(job_id: str) -> dict[str, Any]:
    with database.read() as connection:
        row = (
            connection.execute(sa.select(jobs).where(jobs.c.id == job_id))
            .mappings()
            .first()
        )
    if row is None:
        raise refusals.not_found("There's no such job.")
    return dict(row)


def is_live(job_id: str) -> bool:
    with database.read() as connection:
        state = connection.execute(
            sa.select(jobs.c.state).where(jobs.c.id == job_id)
        ).scalar()
    return state in (states.QUEUED, states.RUNNING)


def document_has_live_work(document_id: str) -> bool:
    with database.read() as connection:
        return (
            connection.execute(
                sa.select(jobs.c.id)
                .where(
                    jobs.c.document_id == document_id,
                    jobs.c.state.in_((states.QUEUED, states.RUNNING)),
                )
                .limit(1)
            ).first()
            is not None
        )


def cancel_requested(job_id: str) -> bool:
    return bool(get(job_id)["cancel_requested"])


def request_cancel_in(connection: Connection, job_id: str) -> str:
    """Cancels a queued job at once, or asks a running one to stop within one stage.
    Returns the state it left the job in."""
    moved = connection.execute(
        jobs.update()
        .where(jobs.c.id == job_id, jobs.c.state == states.QUEUED)
        .values(state=states.CANCELLED, updated_at=time.time())
    )
    if moved.rowcount == 1:
        _event(connection, job_id, states.CANCELLED)
        return states.CANCELLED
    moved = connection.execute(
        jobs.update()
        .where(jobs.c.id == job_id, jobs.c.state == states.RUNNING)
        .values(cancel_requested=True, updated_at=time.time())
    )
    if moved.rowcount == 1:
        _event(connection, job_id, "cancel requested")
        return states.RUNNING
    raise refusals.conflict("This job has already finished.")


def retry_in(connection: Connection, job_id: str) -> None:
    moved = connection.execute(
        jobs.update()
        .where(jobs.c.id == job_id, jobs.c.state.in_((states.FAILED, states.CANCELLED)))
        .values(
            state=states.QUEUED,
            attempts=0,
            cancel_requested=False,
            reason=None,
            error_code=None,
            updated_at=time.time(),
        )
    )
    if moved.rowcount != 1:
        raise refusals.conflict("Only a failed or cancelled job can be retried.")
    _event(connection, job_id, "retried")


def timeline(job_id: str) -> list[str]:
    with database.read() as connection:
        rows = connection.execute(
            sa.select(job_events.c.event)
            .where(job_events.c.job_id == job_id)
            .order_by(job_events.c.id)
        ).scalars()
        return list(rows)
