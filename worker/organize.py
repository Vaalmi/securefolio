"""Cards: a plain job. The local model for cards by default; the helper model for a
folder that opted in, one by one, or through worker/batches.py for a backlog where its
provider has a batch API. Until a model can, the card stays basic and pending, with the
reason."""

import asyncio
import logging
from typing import Any

import sqlalchemy as sa

from agent import models
from backend import (
    cards,
    database,
    embedder,
    jobs,
    model_catalog,
    policy,
    revisions,
    settings,
    sources,
    spend,
    states,
)
from backend.database import document_locations
from backend.tokens import count

logger = logging.getLogger(__name__)
EXCERPT_TOKENS = spend.EXCERPT_TOKENS
FIRST_PAGES = 3
BACKLOG_ALONE = spend.BATCH_ABOVE
LIVE_BATCH = sources.LIVE_BATCH
ITEM_WAITING = sources.ITEM_WAITING
PENDING_PASS = 200
CARD_SCHEMA = {
    "type": "object",
    "title": "card",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "doc_type": {"type": "string", "enum": cards.TYPES},
        "dates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["label", "value"],
                "additionalProperties": False,
            },
        },
        "people": {"type": "array", "items": {"type": "string"}},
        "organizations": {"type": "array", "items": {"type": "string"}},
        "amounts": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "title",
        "summary",
        "doc_type",
        "dates",
        "people",
        "organizations",
        "amounts",
        "tags",
    ],
    "additionalProperties": False,
}


def _is_near_the_start(element: dict[str, Any]) -> bool:
    locator = element["locator"]
    return (
        locator.get("page", 1) <= FIRST_PAGES and locator.get("slide", 1) <= FIRST_PAGES
    )


def excerpt(document: dict[str, Any]) -> str:
    """The file's name and path, the first pages, then every heading, within budget."""
    rows = revisions.active_elements(document["id"])
    places = [row["path"] for row in sources_paths(document["id"])]
    lines = [f"File: {document['file_name']}", *[f"Path: {p}" for p in places[:1]]]
    start = [e["text"] for e in rows if _is_near_the_start(e)]
    headings = [e["text"] for e in rows if e["kind"] == "heading"]
    spent = sum(count(line) for line in lines)
    for text in [*start, *headings]:
        cost = count(text)
        if spent + cost > EXCERPT_TOKENS:
            break
        spent += cost
        lines.append(text)
    return "\n".join(lines)


def sources_paths(document_id: str) -> list[dict[str, Any]]:
    with database.read() as connection:
        rows = connection.execute(
            sa.select(document_locations.c.path).where(
                document_locations.c.document_id == document_id
            )
        ).mappings()
        return [dict(row) for row in rows]


def wants_claude(document: dict[str, Any]) -> bool:
    """Standard, and every folder holding it has opted in. Read fresh."""
    if document["sensitivity"] != states.STANDARD:
        return False
    with database.read() as connection:
        return cards.cloud_consent_in(connection, document["id"])


def apply(
    document_id: str,
    revision_id: str,
    fields: dict[str, Any],
    by: str,
    cloud: bool = False,
) -> bool:
    """Merges the card; an edited field stays. Refused for an old revision, and a
    cloud card for consent taken back while it was written (cards.write_card)."""
    if not cards.write_card(document_id, revision_id, fields, by, cloud):
        return False
    words = " ".join(
        [fields.get("title", ""), fields.get("summary", ""), *fields.get("tags", [])]
    )
    cards.set_embedding(
        document_id, embedder.to_blob(embedder.embed_documents([words])[0])
    )
    return True


def writer_name(model_id: str) -> str:
    """How a card names the cloud model that wrote it."""
    found = model_catalog.get(model_id)
    return found.name if found else model_id


def _write_with(
    document: dict[str, Any], local: bool, by: str, model: str | None = None
) -> None:
    """`model` is the cloud model the card goes to, read once with its name `by`. A
    cut connection isn't caught here: the caller writes the card locally, as such."""
    revision_id = document["active_revision_id"]
    text = excerpt(document)

    def still_wanted() -> bool:
        """As the request leaves: the same revision, and consent not taken back."""
        now = _document(document["id"])
        return (
            now is not None
            and now["active_revision_id"] == revision_id
            and wants_claude(now)
        )

    task = models.card_task(document, text, local, still_wanted, model)
    fields = asyncio.run(models.structured(task, CARD_SCHEMA, fall_back=False))
    usable = {k: v for k, v in fields.items() if v not in ("", None)}
    if usable:
        # A cloud card refused for consent taken back stays pending, for the local
        # model's next pass (queue_pending_when_ready).
        apply(document["id"], revision_id, usable, by, cloud=not local)
    else:
        cards.fail_card(document["id"], "The model's card couldn't be read.")


def in_live_batches() -> set[str]:
    """Documents a batch still answers for: the backend's own count, which the
    organizing estimate uses too."""
    return sources.in_live_batches()


def _backlog() -> int:
    """Documents waiting for the helper across every opted-in folder, and not already
    in a live batch: the backlog worker/batches.py would batch. A batch still waiting,
    for a provider since changed, say, holds no new card back."""
    busy = in_live_batches()
    return len([d for d in waiting_for_claude() if d["id"] not in busy])


def waiting_for_claude() -> list[dict[str, Any]]:
    found = [_document(document_id) for document_id in sources.waiting_for_cards()]
    return [document for document in found if document is not None]


def _document(document_id: str) -> dict[str, Any] | None:
    with database.read() as connection:
        row = (
            connection.execute(
                sa.select(database.documents).where(
                    database.documents.c.id == document_id
                )
            )
            .mappings()
            .first()
        )
    return dict(row) if row else None


def _in_the_cloud(document: dict[str, Any]) -> tuple[bool, str | None]:
    """Whether the cloud wrote the card, or will: by a batch, or now. Otherwise why
    not, for the local model to stand in. None for a helper with no known price, whose
    card neither the cloud nor the local model writes."""
    helper = str(settings.get(settings.MODEL_HELPER))
    kind, why = policy.cloud_choice(helper)
    if kind == "blocked":
        cards.keep_pending(document["id"], str(why))
        return True, None
    if kind == "local" or spend.over_cap():
        # No ready key or no connection, the two fallbacks; or the cap, which stops
        # background work. The local model stands in.
        return False, why if kind == "local" else "The monthly cap is reached"
    if document["id"] in in_live_batches():
        return True, None  # its batch brings the card, or queues it again
    if spend.batched(helper) and _backlog() > BACKLOG_ALONE:
        return True, None  # worker/batches.py takes a backlog
    try:
        _write_with(document, False, writer_name(helper), helper)
    except models.CARD_ERRORS as failed:
        logger.info("the card's cloud call didn't go: %s", type(failed).__name__)
        return False, f"{writer_name(helper)} couldn't be reached"
    return True, None


def run(job: dict[str, Any]) -> None:
    """The opted-in folder's cloud model, or the local model for cards. When neither
    can write it, the card stays pending with the reason."""
    document = _document(job["document_id"])
    if document is None or document["active_revision_id"] is None:
        return
    why = None
    if wants_claude(document):
        # The price is asked before the cap: a helper with no known price writes no
        # card, cloud or local, whether or not the cap is reached.
        done, why = _in_the_cloud(document)
        if done:
            return
    tag = models.local_tag(settings.LOCAL_CARDS)
    if not models.local_ready(settings.LOCAL_CARDS):
        waiting = f"The local model for cards, {tag}, isn't ready in Ollama."
        cards.keep_pending(document["id"], f"{why}. {waiting}" if why else waiting)
        return
    _write_with(document, True, tag, tag)  # the tag read once, labelled as what ran


def queue_pending_when_ready() -> int:
    """Rewrites pending cards once the local model is installed."""
    if not models.local_ready(settings.LOCAL_CARDS):
        return 0
    with database.read() as connection:
        ids = list(
            connection.execute(
                sa.select(database.cards.c.document_id)
                .where(database.cards.c.state == states.PENDING)
                .limit(PENDING_PASS)
            ).scalars()
        )
    with database.write() as connection:
        for document_id in ids:
            jobs.enqueue_in(
                connection,
                jobs.ORGANIZE,
                jobs.PRIORITY_ORGANIZE,
                document_id=document_id,
                key=f"organize:{document_id}",
            )
    return len(ids)
