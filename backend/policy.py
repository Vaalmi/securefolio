"""Decides, before any model call, whether a payload may go to the cloud, from the
sensitivity of each of its parts; thread taint; default scopes; where an answer goes."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import sqlalchemy as sa

from backend import (
    connectivity,
    database,
    keychain,
    model_catalog,
    model_store,
    providers,
    settings,
    spend,
    states,
)
from backend.database import collection_items, documents, memories, threads

LOCAL_LABEL = "Local AI"
PRIVATE_PAGE = "This document is Private, so its page can't go to a cloud model."
OFFLINE_REFUSAL = "Offline mode is on, so nothing can go to a cloud model."
PRIVATE_PART = "This request holds something Private, so it can't go to a cloud model."
TAINTED = "This chat has touched Private documents, so it can't go to a cloud model."
NO_LOCAL_HELPER = (
    "The helper model needs {provider}'s key, and no local model is installed."
)
LOCAL_GONE = "{tag} is no longer in Ollama. Choose another in Settings."
OLLAMA_DOWN = (
    "Ollama isn't running on this Mac, so Local AI can't answer. Start Ollama."
)
NO_DOCUMENT = "There's no such document."
STALE_PART = "Part of this request changed as it left, so it wasn't sent."
OVER_CAP = "This month's spending cap is reached, so this waits."


class Purpose(StrEnum):
    """Everything the privacy rules in docs/ARCHITECTURE.md allow to leave the Mac,
    and nothing else."""

    ANSWER = "answer"
    REWRITE = "rewrite"
    SUMMARY = "summary"
    MEMORY = "memory"
    CARD = "card"
    KEY_TEST = "key_test"
    PAGE_READ = "page_read"
    EVAL_JUDGE = "eval_judge"


class PartKind(StrEnum):
    QUESTION = "question"
    TURN = "turn"
    SUMMARY = "summary"
    MEMORY = "memory"
    PASSAGE = "passage"
    CARD = "card"
    TITLES = "titles"
    EXCERPT = "excerpt"
    TOTAL = "total"


@dataclass
class Part:
    """One piece of a cloud request, carrying what the policy needs to judge it."""

    kind: PartKind
    text: str
    sensitivity: str = states.STANDARD
    document_ids: list[str] = field(default_factory=list)
    memory_id: str | None = None
    summary_version: int | None = None


@dataclass(frozen=True)
class Route:
    """Who answers a question, and who runs its helper steps, decided before any call.
    `model` is a provider:model id, or the local tag."""

    local: bool
    model: str
    name: str = LOCAL_LABEL
    provider: str | None = None
    reason: str | None = None  # why it answers locally, when it falls back
    blocked: str | None = None  # why nothing can answer it now
    helper_local: bool = True
    helper_model: str = ""
    deep_cloud: bool = False  # whether a Deep run would go to the cloud
    # The local answers model, read with the route: a run's local answer, helpers and
    # fallback use this one, whatever Settings says by then.
    local_tag: str = ""
    # Whether it answers locally only because the chosen model's provider has no
    # ready key: the Ask box then says so, with the way to Keys and models.
    needs_key: bool = False


def scope_sensitivities(include_private: bool) -> list[str]:
    """What a scope reads. The library scope leaves Private out."""
    if include_private:
        return [states.STANDARD, states.PRIVATE]
    return [states.STANDARD]


def thread_is_tainted(thread_id: str) -> bool:
    with database.read() as connection:
        return bool(
            connection.execute(
                sa.select(threads.c.private_tainted).where(threads.c.id == thread_id)
            ).scalar()
        )


def _id_mark(provider: str) -> dict[str, Any]:
    """What the saved key's tests found about its ID: whether it needs one, and the
    ID it last worked with ("" for none). An older mark is a bare bool."""
    found = providers.PROVIDERS[provider]
    value = settings.get(found.needs_id_setting) if found.needs_id_setting else None
    if isinstance(value, dict):
        return {
            "requires": bool(value.get("requires")),
            "worked_with": value.get("worked_with"),
        }
    return {"requires": bool(value), "worked_with": None}


def _put_mark(provider: str, requires: bool, worked_with: str | None) -> None:
    found = providers.PROVIDERS[provider]
    if found.needs_id_setting:
        settings.put(
            found.needs_id_setting, {"requires": requires, "worked_with": worked_with}
        )


def mark_id_refused(provider: str, tried: str | None) -> None:
    """The saved key's test said it needs its ID. The ID it last worked with stands,
    unless that's the one refused now."""
    worked = _id_mark(provider)["worked_with"]
    _put_mark(provider, True, None if worked == (tried or "") else worked)


def mark_key_worked(
    provider: str, tried: str | None, new_key: bool, needs_id: bool = False
) -> None:
    """The key worked with this ID, or with none. One that worked with no ID needs
    none. A saved key that needs one keeps that, and so does a new key whose test
    found it needs one (needs_id); this ID is the one it works with. Any other new key
    needs none."""
    requires = bool(tried) and (
        needs_id or (_id_mark(provider)["requires"] and not new_key)
    )
    _put_mark(provider, requires, tried or "")


def forget_id_mark(provider: str) -> None:
    _put_mark(provider, False, None)


def needs_its_id(provider: str) -> bool:
    """A saved key that needs a Workspace or Project ID, unless the ID saved now is the
    one it last worked with: none saved, one refused, and one not yet tested all show
    the needs-ID state, and the key isn't ready, until a test passes with it."""
    found = providers.PROVIDERS[provider]
    mark = _id_mark(provider)
    saved = str(settings.get(found.id_setting) or "") if found.id_setting else ""
    return mark["requires"] and saved != mark["worked_with"]


def key_ready(provider: str) -> bool:
    """Saved, and not a key that needs a Workspace or Project ID it doesn't have."""
    if keychain.get_key(provider) is None:
        return False
    return not needs_its_id(provider)


def needs_key(model_id: str) -> str:
    provider = providers.of(model_id)
    model = model_catalog.get(model_id)
    name = model.name if model else model_id
    article = "an" if provider.name[0] in "AEIOU" else "a"
    extra = f" (or its {provider.id_label})" if provider.id_label else ""
    return (
        f"{name} needs {article} {provider.name} key{extra}. Add it in Settings, "
        "or choose another model."
    )


def cloud_choice(model_id: str, wait: bool = True) -> tuple[str, str | None]:
    """For one chosen model: "cloud", "local" (the two fallbacks, no key or no
    connection) or "blocked" (no known price; nothing switches on its own). With wait
    False, a connection not yet known counts as there (see connectivity.reachable)."""
    if model_id == settings.LOCAL or not providers.is_model_id(model_id):
        return "local", None
    why = spend.unpriced(model_id)
    if why is not None:
        return "blocked", f"{why} Refresh OpenRouter's list or choose another model."
    provider = providers.of(model_id)
    if not key_ready(provider.id):
        return "local", needs_key(model_id)
    if not connectivity.reachable(provider.id, wait):
        return "local", f"No connection to {provider.name}"
    return "cloud", None


def _local_answers() -> tuple[str, str | None]:
    """The local answers model, and why it can't answer: Ollama isn't running, or no
    longer has it."""
    tag = model_store.local_tag(settings.LOCAL_ANSWERS)
    if model_store.ollama_down():
        return tag, OLLAMA_DOWN
    if model_store.installed(tag) is False:
        return tag, LOCAL_GONE.format(tag=tag)
    return tag, None


def scope_is_private(
    document_ids: list[str] | None = None, collection_id: str | None = None
) -> bool:
    """Whether a chosen scope holds a Private document."""
    ids = list(document_ids or [])
    if collection_id is not None:
        with database.read() as connection:
            ids += connection.execute(
                sa.select(collection_items.c.document_id).where(
                    collection_items.c.collection_id == collection_id
                )
            ).scalars()
    return _any_private(ids)


def route(
    mode: str,
    include_private: bool = False,
    thread_private: bool = False,
    preview_deep: bool = False,
    wait: bool = True,
) -> Route:
    """Who answers, and who runs the helpers, decided here so no page or node works
    it out. The first case that applies wins. Whether Deep would go to the cloud is
    asked only for the Ask box to show (preview_deep): no answer, starting or
    running, waits on Deep's provider unless it's a Deep answer. With wait False, no
    connection is tried (see cloud_choice): the run routes again, waiting, once its
    first step shows."""
    tag, gone = _local_answers()
    helper = str(settings.get(settings.MODEL_HELPER))
    if settings.get(settings.OFFLINE):
        return Route(
            True, tag, reason="Offline", blocked=gone, helper_model=tag, local_tag=tag
        )
    if mode == "private" or include_private or thread_private:
        return Route(True, tag, blocked=gone, helper_model=tag, local_tag=tag)
    # Only now: whether a provider can be reached is asked of the network.
    deep_cloud = (
        preview_deep
        and cloud_choice(str(settings.get(settings.MODEL_DEEP)), wait)[0] == "cloud"
    )
    model = str(
        settings.get(settings.MODEL_DEEP if mode == "deep" else settings.MODEL_ANSWER)
    )
    kind, why = cloud_choice(model, wait)
    if kind == "blocked":
        return Route(
            False,
            model,
            model_name(model),
            _provider(model),
            blocked=why,
            helper_model=helper,
            deep_cloud=deep_cloud,
            local_tag=tag,
        )
    helper_kind, helper_why = cloud_choice(helper, wait)
    blocked = None
    if helper_kind == "blocked":
        blocked = helper_why
    elif helper_kind == "local" and gone:
        blocked = (
            NO_LOCAL_HELPER.format(provider=providers.of(helper).name)
            if providers.is_model_id(helper) and gone != OLLAMA_DOWN
            else gone
        )
    helper_local = helper_kind != "cloud"
    if kind == "local":
        return Route(
            True,
            tag,
            reason=why,
            blocked=blocked or gone,
            helper_local=helper_local,
            helper_model=helper,
            deep_cloud=deep_cloud,
            local_tag=tag,
            needs_key=providers.is_model_id(model)
            and not key_ready(providers.of(model).id),
        )
    return Route(
        False,
        model,
        model_name(model),
        _provider(model),
        blocked=blocked,
        helper_local=helper_local,
        helper_model=helper,
        deep_cloud=deep_cloud,
        local_tag=tag,
    )


def model_name(model_id: str) -> str:
    """A model as the app names it: the catalog's name, or the id (a local tag)."""
    model = model_catalog.get(model_id)
    return model.name if model else model_id


def _provider(model_id: str) -> str | None:
    return providers.split(model_id)[0] if providers.is_model_id(model_id) else None


def _any_private(document_ids: list[str]) -> bool:
    if not document_ids:
        return False
    with database.read() as connection:
        return (
            connection.execute(
                sa.select(documents.c.id)
                .where(
                    documents.c.id.in_(document_ids),
                    documents.c.sensitivity == states.PRIVATE,
                )
                .limit(1)
            ).first()
            is not None
        )


def _memory_is_current(memory_id: str) -> bool:
    """Still there, and still not Private. The Part's own sensitivity may be old."""
    with database.read() as connection:
        row = connection.execute(
            sa.select(memories.c.state, memories.c.sensitivity).where(
                memories.c.id == memory_id
            )
        ).first()
    return (
        row is not None and row.state == "active" and row.sensitivity == states.STANDARD
    )


def _summary_is_current(version: int, thread_id: str | None) -> bool:
    if thread_id is None:
        return True
    with database.read() as connection:
        now = connection.execute(
            sa.select(threads.c.summary_version).where(threads.c.id == thread_id)
        ).scalar()
    return now == version


def is_stale(part: Part, thread_id: str | None) -> bool:
    """Something a request can do without: gone since it was gathered, so it's taken out
    and the request rebuilt. A passage or total gone Private is refused instead."""
    if part.memory_id is not None and not _memory_is_current(part.memory_id):
        return True
    if part.summary_version is not None and not _summary_is_current(
        part.summary_version, thread_id
    ):
        return True
    return part.kind == PartKind.TITLES and _any_private(part.document_ids)


def refusal(parts: list[Part], thread_id: str | None = None) -> str | None:
    """Why this request may not go to the cloud right now, read fresh each time."""
    if settings.get(settings.OFFLINE):
        return OFFLINE_REFUSAL
    if thread_id is not None and thread_is_tainted(thread_id):
        return TAINTED
    if any(part.sensitivity != states.STANDARD for part in parts):
        return PRIVATE_PART
    if any(is_stale(part, thread_id) for part in parts):
        return STALE_PART
    named = sorted({d for part in parts for d in part.document_ids})
    return PRIVATE_PART if _any_private(named) else None


def page_cloud_problem(model: str, price: spend.Price | None) -> str | None:
    """Why reading a page in the cloud is unavailable now, whatever the document: no
    known price for the page model, or no ready key for its provider. Asked of the
    model and price the caller read once, so it's about what's shown and sent. Re-read
    this page, on this Mac, is never affected."""
    if price is None:
        return (
            f"{spend.no_price(model)} Refresh OpenRouter's list, or choose another "
            "page model."
        )
    if not key_ready(providers.of(model).id):
        return needs_key(model)
    return None


def page_image_refusal(document_id: str) -> str | None:
    """Why this document's page may not go to any cloud model right now, read
    fresh."""
    if settings.get(settings.OFFLINE):
        return OFFLINE_REFUSAL
    with database.read() as connection:
        level = connection.execute(
            sa.select(documents.c.sensitivity).where(documents.c.id == document_id)
        ).scalar()
    if level is None:
        return NO_DOCUMENT
    return PRIVATE_PAGE if level != states.STANDARD else None
