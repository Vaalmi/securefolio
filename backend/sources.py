"""Watched folders and their state machine, including the built-in source that
holds files dragged in."""

import time
import uuid
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend import (
    database,
    documents,
    jobs,
    model_catalog,
    policy,
    providers,
    refusals,
    sensitivity,
    settings,
    spend,
    states,
    vault,
)
from backend.database import document_locations, sources
from backend.database import jobs as jobs_table

LIBRARY_NAME = "Copied into library"


def ensure_library_source() -> None:
    with database.write() as connection:
        found = connection.execute(
            sa.select(sources.c.id).where(sources.c.id == states.LIBRARY_SOURCE_ID)
        ).first()
        if found is None:
            connection.execute(
                sources.insert().values(
                    id=states.LIBRARY_SOURCE_ID,
                    path=str(vault.objects_dir()),
                    name=LIBRARY_NAME,
                    builtin=True,
                    sensitivity=states.STANDARD,
                    mcp_enabled=True,
                    status=states.WATCHING,
                    created_at=time.time(),
                )
            )


def _get_in(connection: Connection, source_id: str) -> dict[str, Any]:
    row = (
        connection.execute(sa.select(sources).where(sources.c.id == source_id))
        .mappings()
        .first()
    )
    if row is None:
        raise refusals.not_found("There's no such folder.")
    return dict(row)


def get(source_id: str) -> dict[str, Any]:
    with database.read() as connection:
        return _get_in(connection, source_id)


def live() -> list[dict[str, Any]]:
    with database.read() as connection:
        rows = (
            connection.execute(
                sa.select(sources)
                .where(sources.c.status != states.REMOVED)
                .order_by(sources.c.created_at)
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


def live_with_counts() -> list[dict[str, Any]]:
    """The Sources page's rows. A document counts once for each folder it's in."""
    with database.read() as connection:
        counts = dict(
            connection.execute(
                sa.select(document_locations.c.source_id, sa.func.count())
                .join(
                    database.documents,
                    database.documents.c.id == document_locations.c.document_id,
                )
                .where(database.documents.c.status != states.REMOVED)
                .group_by(document_locations.c.source_id)
            )
            .tuples()
            .all()
        )
    return [{**row, "items": counts.get(row["id"], 0)} for row in live()]


def waiting_for_cards(opting_in: str | None = None) -> set[str]:
    """Documents waiting for Haiku: pending, Standard, and every folder holding them has
    opted in. The worker's choice of path and the estimate both read this. With
    opting_in, that folder's own documents, as if it had opted in: what turning
    organizing on there would give the helper to write."""
    if settings.get(settings.MODEL_HELPER) == settings.LOCAL:
        return set()
    holds = sa.select(document_locations.c.document_id)
    refusing = holds.join(
        database.sources, document_locations.c.source_id == database.sources.c.id
    ).where(database.sources.c.auto_organize.is_(False))
    if opting_in is not None:
        holds = holds.where(document_locations.c.source_id == opting_in)
        refusing = refusing.where(database.sources.c.id != opting_in)
    with database.read() as connection:
        return set(
            connection.execute(
                sa.select(database.documents.c.id)
                .join(
                    database.cards,
                    database.cards.c.document_id == database.documents.c.id,
                )
                .where(
                    database.cards.c.state == states.PENDING,
                    database.documents.c.status == states.INDEXED,
                    database.documents.c.sensitivity == states.STANDARD,
                    database.documents.c.id.in_(holds),
                    database.documents.c.id.not_in(refusing),
                )
            ).scalars()
        )


# A batch still answering for its documents, and an item still waiting in one: the
# worker's own states (worker/organize.py uses these).
LIVE_BATCH = ("planned", "submitting", "submitted", "submit_unknown", "ended")
ITEM_WAITING = "waiting"


def in_live_batches() -> set[str]:
    """Documents a batch still answers for. One dropped from its batch isn't."""
    with database.read() as connection:
        return set(
            connection.execute(
                sa.select(database.batch_items.c.document_id)
                .join(
                    database.message_batches,
                    database.message_batches.c.id == database.batch_items.c.batch_id,
                )
                .where(
                    database.message_batches.c.state.in_(LIVE_BATCH),
                    database.batch_items.c.state == ITEM_WAITING,
                )
            ).scalars()
        )


def organize_estimate(source_id: str) -> dict[str, Any]:
    """From the folder's real stored text, capped at what an excerpt holds: only for
    the cards turning organizing on there would have the helper write, the pending ones
    no live batch already answers for. A card already written isn't written again."""
    busy = in_live_batches()
    work = waiting_for_cards(opting_in=source_id) - busy
    # The work is this folder's documents already, each once: joining its locations
    # would count a document kept in two places twice.
    with database.read() as connection:
        rows = connection.execute(
            sa.select(
                database.documents.c.id,
                sa.func.sum(database.chunks.c.token_count),
            )
            .join(
                database.chunks,
                database.chunks.c.revision_id
                == database.documents.c.active_revision_id,
            )
            .where(database.documents.c.id.in_(work))
            .group_by(database.documents.c.id)
        ).all()
    tokens = sum(min(row[1] or 0, spend.EXCERPT_TOKENS) for row in rows)
    # The backlog the worker would batch (worker/organize.py _backlog), with this
    # folder opted in: so the batch price shown is the one charged.
    backlog = (waiting_for_cards() | work) - busy
    # The amounts and the rates shown for approval come from one read of the price.
    estimate = spend.card_estimate(tokens, len(rows), len(backlog))
    model, price = estimate["model"], estimate["price"]
    cloud = providers.is_model_id(model)
    kind, why = policy.cloud_choice(model) if cloud else ("local", None)
    # The backfill's rates: the batch price where it's batched, else the full one.
    share = spend.BATCH_SHARE if estimate["batched"] else 1.0
    return {
        "backfill_usd": estimate["backfill_usd"],
        "per_document_usd": estimate["per_document_usd"],
        "batched": estimate["batched"],
        "model": model,
        "model_name": policy.model_name(model),
        "provider": providers.of(model).name if cloud else None,
        "input_usd": price[0] if price else None,
        "output_usd": price[1] if price else None,
        "backfill_input_usd": price[0] * share if price else None,
        "backfill_output_usd": price[1] * share if price else None,
        # Whether it can be turned on: the helper's provider has a ready key.
        "key_ready": not cloud or policy.key_ready(providers.of(model).id),
        # Why the helper can't write this folder's cards now, for its switch to say.
        "unavailable": why if kind != "cloud" else None,
    }


ORGANIZE_COST_CHANGED = (
    "The helper model or its price has changed since this estimate was shown. Check "
    "the new estimate, then turn it on again."
)


def organize_cost_refusal(
    model: str | None,
    input_usd: float | None,
    output_usd: float | None,
    shown_for: tuple[str, bool | None] | None = None,
) -> str | None:
    """Organizing goes on at the helper model and price its estimate showed, or not at
    all: a helper or price changed since then is refused. For a folder whose estimate
    was shown (shown_for: its id, and whether it was at the batch price), the batch
    price must still apply too, from one fresh estimate."""
    if shown_for is not None:
        source_id, batched = shown_for
        now = organize_estimate(source_id)
        same = (now["model"], now["input_usd"], now["output_usd"], now["batched"]) == (
            model,
            input_usd,
            output_usd,
            batched,
        )
        return None if same else ORGANIZE_COST_CHANGED
    helper = str(settings.get(settings.MODEL_HELPER))
    price = model_catalog.price(helper) if providers.is_model_id(helper) else None
    shown = (
        None if input_usd is None and output_usd is None else (input_usd, output_usd)
    )
    if model != helper or price != shown:
        return ORGANIZE_COST_CHANGED
    return None


def organize_refusal() -> str | None:
    """Why cloud organizing can't be turned on now: the helper model's provider has
    no ready key. The local model writes the cards meanwhile. The routes ask it, so
    each way the app turns organizing on goes through it."""
    model = str(settings.get(settings.MODEL_HELPER))
    if providers.is_model_id(model) and not policy.key_ready(providers.of(model).id):
        return policy.needs_key(model)
    return None


def add_source(
    path: str, level: str, auto_organize: bool = False, mcp_enabled: bool = True
) -> dict[str, Any]:
    folder = Path(path)
    if not folder.is_dir():
        raise refusals.conflict("That isn't a folder.")
    vault_root = vault.root().resolve()
    if folder.resolve() == vault_root or vault_root in folder.resolve().parents:
        raise refusals.conflict("That folder is inside SecureFolio's own library.")
    private = level == states.PRIVATE
    source_id = uuid.uuid4().hex
    with database.write() as connection:
        watched = connection.execute(
            sa.select(sources.c.id).where(
                sources.c.path == str(folder), sources.c.status != states.REMOVED
            )
        ).first()
        if watched:
            raise refusals.conflict("That folder is already watched.")
        connection.execute(
            sources.insert().values(
                id=source_id,
                path=str(folder),
                name=folder.name or str(folder),
                sensitivity=level,
                auto_organize=auto_organize and not private,
                mcp_enabled=mcp_enabled and not private,
                status=states.SCANNING,
                created_at=time.time(),
            )
        )
        jobs.enqueue_in(
            connection,
            jobs.SCAN,
            jobs.PRIORITY_BULK,
            source_id=source_id,
            key=f"scan:{source_id}",
            payload={"new_folder": True},
        )
    return get(source_id)


def rescan(source_id: str, by_owner: bool = True) -> None:
    """Scans now. The owner may scan a paused folder once; the timer never does."""
    with database.write() as connection:
        row = _get_in(connection, source_id)
        status = row["status"]
        if status == states.SCANNING and _has_live_scan(connection, source_id):
            return
        if status == states.PAUSED:
            if not by_owner:
                raise refusals.conflict("This folder is paused.")
            values = {"status": states.SCANNING, "return_to_paused": True}
        elif status in (states.WATCHING, states.NEEDS_ATTENTION, states.SCANNING):
            values = {"status": states.SCANNING, "return_to_paused": False}
        else:
            raise refusals.conflict("This folder isn't watched any more.")
        connection.execute(
            sources.update().where(sources.c.id == source_id).values(**values)
        )
        priority = jobs.PRIORITY_CHANGE if by_owner else jobs.PRIORITY_BULK
        jobs.enqueue_in(
            connection,
            jobs.SCAN,
            priority,
            source_id=None if status == states.PAUSED else source_id,
            key=f"scan:{source_id}",
            payload={"source_id": source_id, "by_owner": by_owner},
        )


def _has_live_scan(connection: Connection, source_id: str) -> bool:
    return (
        connection.execute(
            sa.select(jobs_table.c.id)
            .where(
                jobs_table.c.idempotency_key == f"scan:{source_id}",
                jobs_table.c.state.in_((states.QUEUED, states.RUNNING)),
            )
            .limit(1)
        ).first()
        is not None
    )


def settle_scans_with_no_job() -> None:
    """A folder left scanning by a cancelled or lost scan goes back to what it was, so
    Rescan works."""
    for source in live():
        if source["status"] != states.SCANNING:
            continue
        with database.write() as connection:
            if _has_live_scan(connection, source["id"]):
                continue
            status = states.PAUSED if source["return_to_paused"] else states.WATCHING
            connection.execute(
                sources.update()
                .where(
                    sources.c.id == source["id"], sources.c.status == states.SCANNING
                )
                .values(status=status, return_to_paused=False)
            )


def finish_scan(source_id: str, holds_not_downloaded: bool) -> None:
    with database.write() as connection:
        row = _get_in(connection, source_id)
        if row["status"] != states.SCANNING:
            return
        if row["return_to_paused"]:
            status = states.PAUSED
        else:
            status = states.NEEDS_ATTENTION if holds_not_downloaded else states.WATCHING
        connection.execute(
            sources.update()
            .where(sources.c.id == source_id, sources.c.status == states.SCANNING)
            .values(
                status=status,
                status_reason=None,
                return_to_paused=False,
                last_scan_at=time.time(),
            )
        )


def fail_scan(source_id: str, reason: str) -> None:
    with database.write() as connection:
        row = _get_in(connection, source_id)
        if row["status"] != states.SCANNING:
            return
        status = states.PAUSED if row["return_to_paused"] else states.NEEDS_ATTENTION
        connection.execute(
            sources.update()
            .where(sources.c.id == source_id, sources.c.status == states.SCANNING)
            .values(status=status, status_reason=reason, return_to_paused=False)
        )


def pause(source_id: str) -> None:
    with database.write() as connection:
        moved = connection.execute(
            sources.update()
            .where(
                sources.c.id == source_id,
                sources.c.status.in_(
                    (states.WATCHING, states.SCANNING, states.NEEDS_ATTENTION)
                ),
            )
            .values(status=states.PAUSED, return_to_paused=False)
        )
    if moved.rowcount != 1:
        raise refusals.conflict("This folder can't be paused now.")


def resume(source_id: str) -> None:
    with database.write() as connection:
        moved = connection.execute(
            sources.update()
            .where(sources.c.id == source_id, sources.c.status == states.PAUSED)
            .values(status=states.SCANNING)
        )
        if moved.rowcount != 1:
            raise refusals.conflict("This folder isn't paused.")
        jobs.enqueue_in(
            connection,
            jobs.SCAN,
            jobs.PRIORITY_BULK,
            source_id=source_id,
            key=f"scan:{source_id}",
        )


def stop_watching(source_id: str) -> None:
    """The files stay where they are. Documents with no other location leave the
    library; their notes are kept."""
    with database.write() as connection:
        row = _get_in(connection, source_id)
        if row["builtin"]:
            raise refusals.conflict(
                "This one holds the files you copied in, and stays."
            )
        doc_ids = list(
            connection.execute(
                sa.select(document_locations.c.document_id).where(
                    document_locations.c.source_id == source_id
                )
            ).scalars()
        )
        connection.execute(
            document_locations.delete().where(
                document_locations.c.source_id == source_id
            )
        )
        connection.execute(
            sources.update()
            .where(sources.c.id == source_id)
            .values(status=states.REMOVED)
        )
        orphans = []
        for document_id in doc_ids:
            left = connection.execute(
                sa.select(document_locations.c.id).where(
                    document_locations.c.document_id == document_id
                )
            ).first()
            if left is None:
                orphans.append(document_id)
            else:
                sensitivity.apply_in(connection, document_id)
    documents.remove_from_library(orphans)
    sensitivity.follow_in_index()


def set_sensitivity(source_id: str, level: str) -> None:
    """To Private: its switches go off, and each document moves through the one path.
    To Standard: a document follows only if no other location is Private."""
    with database.write() as connection:
        _get_in(connection, source_id)
        values: dict[str, Any] = {"sensitivity": level}
        if level == states.PRIVATE:
            values |= {"auto_organize": False, "mcp_enabled": False}
        connection.execute(
            sources.update().where(sources.c.id == source_id).values(**values)
        )
        connection.execute(
            document_locations.update()
            .where(document_locations.c.source_id == source_id)
            .values(sensitivity=level)
        )
        doc_ids = connection.execute(
            sa.select(document_locations.c.document_id).where(
                document_locations.c.source_id == source_id
            )
        ).scalars()
        for document_id in list(doc_ids):
            sensitivity.apply_in(connection, document_id)
    sensitivity.follow_in_index()


def set_switches(
    source_id: str, auto_organize: bool | None = None, mcp_enabled: bool | None = None
) -> None:
    with database.write() as connection:
        row = _get_in(connection, source_id)
        private = row["sensitivity"] == states.PRIVATE
        if private and (auto_organize or mcp_enabled):
            raise refusals.conflict(
                "A Private folder can't be sent to any cloud model or through MCP."
            )
        values = {}
        if auto_organize is not None:
            values["auto_organize"] = auto_organize
        if mcp_enabled is not None:
            values["mcp_enabled"] = mcp_enabled
        if values:
            connection.execute(
                sources.update().where(sources.c.id == source_id).values(**values)
            )
