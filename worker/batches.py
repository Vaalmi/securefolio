"""A backlog of cards through the Message Batches API. Each move is a conditional
update; nothing is sent twice while its outcome is unknown."""

import logging
import math
import time
import uuid
from collections.abc import Callable
from typing import Any

import sqlalchemy as sa

from agent import models
from backend import database, documents, jobs, policy, providers, settings, spend
from backend.database import batch_items, message_batches
from worker import organize

logger = logging.getLogger(__name__)
PLANNED = "planned"
SUBMITTING = "submitting"
SUBMITTED = "submitted"
SUBMIT_UNKNOWN = "submit_unknown"
ENDED = "ended"
APPLIED = "applied"
DROPPED = "dropped"
ITEM_WAITING = organize.ITEM_WAITING
ITEM_APPLIED = "applied"
ITEM_DROPPED = "dropped"
ALONE = organize.BACKLOG_ALONE
GROUP_LEAST = 100
GROUP_MOST = 200
TOO_LATE_SECONDS = 25 * 3600
POLL_SECONDS = 300


def partition(document_ids: list[str]) -> list[list[str]]:
    """51 to 99 are one group, since they can't reach 100. From 100, even groups of 100
    to 200. 50 or fewer go one by one."""
    total = len(document_ids)
    if total <= ALONE:
        return []
    if total < GROUP_LEAST:
        return [document_ids]
    groups = math.ceil(total / GROUP_MOST)
    size, extra = divmod(total, groups)
    found, at = [], 0
    for group in range(groups):
        ends = at + size + (1 if group < extra else 0)
        found.append(document_ids[at:ends])
        at = ends
    return found


def plan() -> int:
    """The helper is read once: a batch is planned for it only if its provider has a
    batch API here, and keeps it."""
    model = str(settings.get(settings.MODEL_HELPER))
    if not providers.is_model_id(model) or not spend.batched(model):
        return 0  # organize.py writes the backlog one card at a time
    if policy.cloud_choice(model)[0] != "cloud":
        return 0  # no ready key, no connection or no price: organize.py decides
    busy = organize.in_live_batches()
    waiting = [d for d in organize.waiting_for_claude() if d["id"] not in busy]
    by_id = {d["id"]: d for d in waiting}
    groups = partition(sorted(by_id))
    for group in groups:
        _plan_one([by_id[document_id] for document_id in group], model)
    return len(groups)


def _plan_one(group: list[dict[str, Any]], model: str) -> str:
    """A batch keeps the provider and model it's planned for: it's sent, polled,
    settled and logged against them, whatever the helper is set to by then."""
    batch_id = uuid.uuid4().hex
    with database.write() as connection:
        connection.execute(
            message_batches.insert().values(
                id=batch_id,
                submission_id=uuid.uuid4().hex[:16],
                provider=providers.split(model)[0],
                model=model,
                state=PLANNED,
                created_at=time.time(),
            )
        )
        connection.execute(
            batch_items.insert(),
            [
                {
                    "batch_id": batch_id,
                    "document_id": d["id"],
                    "revision_id": d["active_revision_id"],
                    "excerpt_hash": "",
                    "state": ITEM_WAITING,
                }
                for d in group
            ],
        )
    return batch_id


def _in_state(state: str) -> list[dict[str, Any]]:
    with database.read() as connection:
        return [
            dict(row)
            for row in connection.execute(
                sa.select(message_batches).where(message_batches.c.state == state)
            ).mappings()
        ]


def _move(batch_id: str, was: str, to: str, **values: Any) -> bool:
    with database.write() as connection:
        moved = connection.execute(
            message_batches.update()
            .where(message_batches.c.id == batch_id, message_batches.c.state == was)
            .values(state=to, **values)
        )
    return moved.rowcount == 1


def _items_of(batch_id: str) -> list[dict[str, Any]]:
    with database.read() as connection:
        return [
            dict(row)
            for row in connection.execute(
                sa.select(batch_items).where(batch_items.c.batch_id == batch_id)
            ).mappings()
        ]


def _set_item(batch_id: str, document_id: str, state: str) -> None:
    with database.write() as connection:
        connection.execute(
            batch_items.update()
            .where(
                batch_items.c.batch_id == batch_id,
                batch_items.c.document_id == document_id,
            )
            .values(state=state)
        )


def _still_wanted(item: dict[str, Any]) -> dict[str, Any] | None:
    document = documents.get(item["document_id"])
    current = document["active_revision_id"] == item["revision_id"]
    return document if current and organize.wants_claude(document) else None


def _wanted_now(batch: dict[str, Any]) -> Callable[[str], bool]:
    items = {item["document_id"]: item for item in _items_of(batch["id"])}
    return lambda document_id: _still_wanted(items[document_id]) is not None


def _no_batch_api(batch: dict[str, Any]) -> None:
    """Its provider has no batch API here: nothing is sent, and its cards go back to be
    written one at a time."""
    _move(batch["id"], PLANNED, DROPPED)
    for item in _items_of(batch["id"]):
        _set_item(batch["id"], item["document_id"], ITEM_DROPPED)
        _queue_again(item["document_id"])


def _submit_one(batch: dict[str, Any]) -> None:
    """Marks first, then sends. A send that may have reached the provider is never sent
    again until it's been looked for."""
    built = []
    for item in _items_of(batch["id"]):
        document = _still_wanted(item)
        if document is None:
            _set_item(batch["id"], item["document_id"], ITEM_DROPPED)
            continue
        custom_id = f"{batch['submission_id']}-{item['document_id']}"
        built.append(
            models.BatchItem(custom_id, item["document_id"], organize.excerpt(document))
        )
    if not built:
        _move(batch["id"], PLANNED, DROPPED)
        return
    if not _move(batch["id"], PLANNED, SUBMITTING, marked_at=time.time()):
        return
    try:
        provider_id, sent = models.submit_batch(
            built,
            organize.CARD_SCHEMA,
            models.BatchOf(batch["model"], batch["submission_id"]),
            _wanted_now(batch),
        )
    except models.BatchRefused:
        _move(batch["id"], SUBMITTING, PLANNED)
        return
    except models.BatchUnknown:
        _move(batch["id"], SUBMITTING, SUBMIT_UNKNOWN)
        return
    if not provider_id:
        _move(batch["id"], SUBMITTING, DROPPED)
        return
    for item in built:
        if item.document_id not in sent:
            _set_item(batch["id"], item.document_id, ITEM_DROPPED)
    _move(batch["id"], SUBMITTING, SUBMITTED, provider_id=provider_id)


def _reconcile(batch: dict[str, Any]) -> None:
    """Finds a submission whose outcome wasn't known, by its id in each item's custom
    id. Never re-sends until every candidate has ended with no match, after 25 hours."""
    prefix = f"{batch['submission_id']}-"
    all_ended = True
    for candidate in models.batches_since(batch["model"], batch["marked_at"] - 60):
        if candidate.get("submission_id") == batch["submission_id"]:
            # OpenAI's metadata names the submission: found, ended or not.
            ended = candidate["status"] == "ended"
            to = ENDED if ended else SUBMITTED
            _move(batch["id"], SUBMIT_UNKNOWN, to, provider_id=candidate["id"])
            return
        if candidate["status"] != "ended":
            all_ended = False
            continue
        results = models.batch_results(batch["model"], candidate["id"])
        if any(r["custom_id"].startswith(prefix) for r in results):
            _move(batch["id"], SUBMIT_UNKNOWN, ENDED, provider_id=candidate["id"])
            return
    too_late = time.time() - batch["marked_at"] > TOO_LATE_SECONDS
    if all_ended and too_late:
        # In this order: a crash between them leaves it to be looked for again, and
        # closing an attempt twice changes nothing.
        models.batch_never_accepted(batch["submission_id"])
        _move(batch["id"], SUBMIT_UNKNOWN, PLANNED)


def _poll(batch: dict[str, Any]) -> None:
    if models.batch_status(batch["model"], batch["provider_id"]) == "ended":
        _move(batch["id"], SUBMITTED, ENDED)


def _apply(batch: dict[str, Any]) -> None:
    """Checks the current revision and folder policy per item. One now stale or Private
    is dropped, and goes back to its folder's current path."""
    prefix = f"{batch['submission_id']}-"
    results = {
        r["custom_id"][len(prefix) :]: r
        for r in models.batch_results(batch["model"], batch["provider_id"])
        if r["custom_id"].startswith(prefix)
    }
    models.settle_batch(batch["submission_id"], results.values())
    for item in _items_of(batch["id"]):
        if item["state"] != ITEM_WAITING:
            continue
        found = results.get(item["document_id"])
        applied = False
        if found and found["fields"] and _still_wanted(item) is not None:
            applied = organize.apply(
                item["document_id"],
                item["revision_id"],
                found["fields"],
                organize.writer_name(batch["model"]),
                cloud=True,
            )
        _set_item(
            batch["id"], item["document_id"], ITEM_APPLIED if applied else ITEM_DROPPED
        )
        if not applied:
            _queue_again(item["document_id"])
    _move(batch["id"], ENDED, APPLIED)


def _queue_again(document_id: str) -> None:
    with database.write() as connection:
        jobs.enqueue_in(
            connection,
            jobs.ORGANIZE,
            jobs.PRIORITY_ORGANIZE,
            document_id=document_id,
            key=f"organize:{document_id}",
        )


def _key_ready(batch: dict[str, Any]) -> bool:
    """Its own provider's key, not the helper's or Anthropic's: without it the batch
    waits, as it is, for the next pass."""
    return policy.key_ready(str(batch["provider"]))


def _submit_planned() -> None:
    for batch in _in_state(PLANNED):
        if spend.over_cap():
            break
        if not spend.batched(batch["model"]):
            _no_batch_api(batch)
        elif _key_ready(batch):
            _each(_submit_one, batch)


def _each(step: Callable[[dict[str, Any]], Any], batch: dict[str, Any]) -> None:
    """One batch's step: if its provider can't be reached now, that batch waits for the
    next pass, and the others, perhaps another provider's, go on."""
    try:
        step(batch)
    except models.BATCH_WAITS:
        logger.info("batch %s waits for the next pass", batch["id"])


def tick() -> None:
    """One pass. Offline defers it all; the cap stops new sends, not results already
    paid for."""
    if settings.get(settings.OFFLINE):
        return
    try:
        for batch in _in_state(SUBMITTING):
            # A crash between marking and sending: its outcome isn't known.
            _move(batch["id"], SUBMITTING, SUBMIT_UNKNOWN)
        if not spend.over_cap():
            plan()
            _submit_planned()
        for state, step in ((SUBMIT_UNKNOWN, _reconcile), (SUBMITTED, _poll)):
            for batch in filter(_key_ready, _in_state(state)):
                _each(step, batch)
        for batch in filter(_key_ready, _in_state(ENDED)):
            _each(_apply, batch)
    except models.BATCH_WAITS:
        logger.info("batches wait for the next pass")
