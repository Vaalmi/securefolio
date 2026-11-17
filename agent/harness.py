"""The Ask graph: its nodes, edges, state, interrupts and streaming. Routing and scope
come from code; a model's output only ever fills in words."""

import asyncio
import html
import json
import logging
import re
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Annotated, Any, Required, TypedDict, cast

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    RemoveMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt

from agent import api, citations, figures, memory, models, plain, prompts
from agent.budgets import Budget, for_mode
from agent.plain import COUNTED_WORDS

logger = logging.getLogger(__name__)
_fits: Counter[str] = Counter()  # local prompts fitted to their context, this process

CANDIDATES = 24
KEEP = 12
PER_DOCUMENT = 3
DEEP_CANDIDATES = 72
DEEP_KEEP = 36
# Real scores: on-topic passages from about 0.1 up, off-topic ones under 0.001.
WEAK_SCORE = 0.02
LIST_WORDS = re.compile(
    r"\b(total|sum|spend|spending|spent|list (all|every|my)|all (of )?my)\b",
    re.I,
)
# "Which" or "what" before a plural noun may ask for a list ("what leases"). Before
# "is", "was", "does" or "has" it asks about one thing, which goes straight to search,
# unless the question also says list, every, each or all ("what is the list of ..."),
# as a word of its own, not in "all-in" or "each-way".
MAYBE_LIST_WORDS = re.compile(
    r"\b(which|what)\s+(?!(?:is|was|does|has)\b)\w+s\b"
    r"|\b(which|what)\s+(?:is|was|does|has)\b.*\b(lists?|every|each|all)\b(?!-)"
    r"|\bhow much\b",
    re.I,
)
LIBRARY_QUESTION = re.compile(
    r"\s*(?:how many|what(?:'s| is) the number of|number of|count(?: of)?(?: my)?)\s+"
    r"(?:documents?|docs?|files?)"
    r"(?:\s+(?:do i have|have i (?:got|added|indexed)|are (?:there|indexed)"
    r"|are (?:in|on) (?:my|the|this) (?:library|mac|scope|chat|collection)"
    r"|(?:in|on) (?:my|the|this) (?:library|mac|scope|chat|collection)))*"
    r"(?:\s+in (?:total|all))?\s*[?.!]*\s*",
    re.I,
)
# What a follow-up reads in place of the counts: a document gone Private since must not
# reach the cloud through a number or a type counted before it did.
COMPARE_WORDS = re.compile(r"\b(compare|versus|vs\.?|difference between)\b", re.I)
MONEY = re.compile(r"(?<!\w)([-−])?\$([-−])?\s?(\d[\d,]*(?:\.\d+)?)")
NUMBER = re.compile(r"(?<![\w.,])([-−])?(\d[\d,]*(?:\.\d+)?)")
NOT_SPENDING = re.compile(
    r"\b(limit|coverage|deductible|maximum|max|value|balance)\b", re.I
)
FIELD_STEM = 5  # "expires" and "expiration" share their first five letters
PART_OF_ELEMENT = 4  # no element takes more than this share of the evidence budget
LEAST_ROOM = 50  # tokens: less than this and an element isn't worth cutting
ASKING_WORDS = {
    "spend",
    "spent",
    "spending",
    "expense",
    "expenses",
    "cost",
    "costs",
    "payment",
    "payments",
    "paid",
    "pay",
    "total",
    "amount",
    "amounts",
    "money",
    "price",
}
GENERAL_DATE_WORDS = {"date", "dated", "year", "period", "when", "time"}
NO_CLOUD_CALL = "Local AI on this Mac · no cloud call"
PLAN_SCHEMA = {
    "type": "object",
    "title": "plan",
    "properties": {
        "query": {"type": "string"},
        "kind": {"type": "string", "enum": ["lookup", "comparison", "list"]},
        "date_from": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "date_to": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "date_field": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "amount_field": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": [
        "query",
        "kind",
        "date_from",
        "date_to",
        "keywords",
        "date_field",
        "amount_field",
    ],
    "additionalProperties": False,
}


class AskState(TypedDict, total=False):
    """Ids and small values. Large text lives in SQLite and is fetched by id."""

    question: Required[str]
    mode: Required[str]
    local: bool
    helper_local: bool  # the helper steps run on this Mac, decided with the answer
    helper_model: str  # the helper model routed with the answer, read once
    route_reason: str | None  # why the answer is local, when it falls back
    blocked: str | None  # why no model can answer: then passages are found instead
    model: str  # the cloud model its route chose; Deep's is the one its card showed
    local_tag: str  # the local answers model, read once as the run began
    price: api.Price | None  # the one price a Deep answer goes at, as its card showed
    fell_back: bool
    excluded_private: bool
    include_private: bool
    turns: Annotated[list[AnyMessage], add_messages]
    query: Required[str]
    kind: Required[str]
    date_from: str | None
    date_to: str | None
    keywords: list[str]
    date_field: str | None
    amount_field: str | None
    attempts: int
    near_miss: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    also_searched: list[dict[str, Any]]
    left_out: list[str]
    memory_ids: list[str]
    activity: list[str]
    table: dict[str, Any] | None
    answer: Required[dict[str, Any]]
    written: dict[str, Any] | None  # the newest turn's reply as written, for the eval
    cost_usd: float


def _text(message: AnyMessage) -> str:
    return str(message.text)


def _ids(config: RunnableConfig) -> tuple[str, str]:
    """The run's ids come from its context, never from a model."""
    configurable = config.get("configurable") or {}
    return configurable["thread_id"], configurable["run_id"]


def _emit(event: str, **data: Any) -> None:
    get_stream_writer()({"event": event, "data": data})


def _step(name: str, detail: str = "") -> None:
    _emit("step", name=name, detail=detail)


def _library_scope(
    scope: dict[str, Any], include_private: bool, state: AskState | None = None
) -> Any:
    fields: dict[str, Any] = {"include_private": include_private}
    if scope["kind"] == "collection":
        fields["collection_id"] = scope["id"]
    if scope["kind"] in ("documents", "search"):
        fields["document_ids"] = tuple(scope["ids"])
    if state is not None:
        fields["date_from"] = state.get("date_from")
        fields["date_to"] = state.get("date_to")
    return api.search.Scope(**fields)


# ---- Prepare ----


def _private_in_standard_thread(thread: dict[str, Any]) -> list[dict[str, str]]:
    if thread["private_tainted"]:
        return []
    with api.read() as connection:
        return api.threads.private_documents_in(connection, thread["scope"])


def _ask_about_private(found: list[dict[str, str]]) -> dict[str, Any]:
    """Nothing before this has a side effect: a resume runs the node again."""
    answer = interrupt(
        {"kind": "private_in_scope", "id": uuid.uuid4().hex, "documents": found}
    )
    return {"choice": answer}


def _route_of(thread: dict[str, Any], mode: str) -> Any:
    """`policy.route()`'s decision, the one place who answers is worked out. One that
    nothing can answer fails the run, with its reason."""
    route = api.route(mode, False, bool(thread["private_tainted"]))
    if route.blocked:
        raise models.LocalNotReady(route.blocked)
    return route


def _routed(mode: str, thread_private: bool) -> Any:
    """The run's route. Ollama is asked for its list only when a local model would
    run, and here, after the first step shows, so a slow Ollama never holds it back."""
    route = api.route(mode, False, thread_private)
    if route.local or route.helper_local:
        models.refresh_ollama_if_stale()
        route = api.route(mode, False, thread_private)
    return route


async def prepare(state: AskState, config: RunnableConfig) -> dict[str, Any]:
    _step("searching")
    thread_id, _ = _ids(config)
    thread = await asyncio.to_thread(api.threads.get, thread_id)
    found = await asyncio.to_thread(_private_in_standard_thread, thread)
    excluded = False
    if found:
        choice = _ask_about_private(found)["choice"]
        if choice == "local":
            await asyncio.to_thread(api.threads.taint, thread_id)
            thread = await asyncio.to_thread(api.threads.get, thread_id)
        else:
            excluded = True
    route = await asyncio.to_thread(
        _routed, state["mode"], bool(thread["private_tainted"])
    )
    include_private = bool(thread["private_tainted"])
    if route.blocked:
        # No model can answer: search still can, on this Mac, before any model call.
        return {
            "blocked": route.blocked,
            "local": True,  # nothing leaves the Mac
            # An earlier question's, which the thread's state keeps: none of it is
            # this run's.
            "evidence": [],
            "also_searched": [],
            "memory_ids": [],
            "excluded_private": excluded,
            "include_private": include_private,
            "activity": [],
            "table": None,
            "cost_usd": 0.0,
            "turns": [HumanMessage(state["question"])],
        }
    recalled = await asyncio.to_thread(
        memory.recall, state["question"], include_private
    )
    return {
        "blocked": None,  # the thread's state keeps an earlier question's block
        "local": route.local,
        "model": route.model,  # what was routed is what goes: never read again
        # The local model this run answers and helps with, if it does, read with the
        # route: a change in Settings is the next question's.
        "local_tag": route.local_tag,
        # The helpers' own route: a Private or Offline answer already sets it, and an
        # answer that falls back for want of its key leaves a ready helper in the cloud.
        "helper_local": route.helper_local,
        "helper_model": route.helper_model,
        "route_reason": route.reason,
        "fell_back": False,
        "excluded_private": excluded,
        "include_private": include_private,
        "memory_ids": [m["id"] for m in recalled],
        "attempts": 0,
        "near_miss": [],
        "activity": [],
        "table": None,
        "left_out": [],
        "cost_usd": 0.0,
        "turns": [HumanMessage(state["question"])],
    }


# ---- A question about the library itself ----


def after_prepare(state: AskState) -> str:
    if state.get("blocked"):
        return "passages"
    if LIBRARY_QUESTION.fullmatch(state["question"]):
        return "library_facts"
    return "plan"


# ---- Passages, when no model can answer ----

PASSAGES_SHOWN = 5


def _passages_found(state: AskState, thread: dict[str, Any]) -> dict[str, Any]:
    """What search finds for the owner's own words in the chat's scope, with no
    model: not an answer. It adds nothing up, says no list is complete, and is never
    summarised or remembered; the follow-up history gets no turn from it."""
    question = state["question"]
    reason = state.get("blocked") or ""
    allowed = _allowed_private(state, thread)
    scope = _library_scope(thread["scope"], allowed)
    ranked = _ranked(question, api.search.retrieve(question, scope, CANDIDATES))
    single = thread["scope"]["kind"] == "documents" and len(thread["scope"]["ids"]) == 1
    kept = _one_per_cap(ranked, PASSAGES_SHOWN, single)
    found = api.search.evidence([item["chunk_id"] for item in kept], allowed)
    coverage = api.search.coverage(scope)
    # From the saved questions, which a summary never folds away: this one is saved
    # as the run starts, so any other means it follows one.
    asked = api.threads.transcript(thread["id"])
    asked_before = sum(m["role"] == api.threads.ROLE_USER for m in asked) > 1
    return {
        "kind": "passages",
        "reason": reason,
        "follow_up": asked_before,
        "coverage": coverage,
        "found": [
            {
                "document_id": item["document_id"],
                "title": item["title"],
                "element_id": item["elements"][0]["id"] if item["elements"] else None,
                "label": item["elements"][0]["label"] if item["elements"] else "",
                "text": item["text"][:300],
            }
            for item in found
        ],
        "model": "",
        "local": True,
        "mode": state["mode"],
        "cost_usd": 0.0,
        "tokens": 0,
        "activity": [
            f"Searched “{question}” in "
            f"{_plural(coverage['searched'], 'document')}, on this Mac",
            f"No model answered: {reason}",
        ],
        "fell_back": False,
        "excluded_private": state.get("excluded_private", False),
    }


async def passages(state: AskState, config: RunnableConfig) -> dict[str, Any]:
    thread_id, _ = _ids(config)
    thread = await asyncio.to_thread(api.threads.get, thread_id)
    found = await asyncio.to_thread(_passages_found, state, thread)
    _step("reading", f"{len(found['found'])} passages")
    return {"answer": found}


def _plural(count: int, one: str) -> str:
    return f"{count:,} {one}" if count == 1 else f"{count:,} {one}s"


def _facts_in_words(facts: dict[str, Any], whole_library: bool) -> str:
    statuses = facts["statuses"]
    where = "in your library" if whole_library else "in this chat's scope"
    total = sum(statuses.values())
    indexing = statuses.get(api.states.QUEUED, 0) + statuses.get(api.states.INDEXING, 0)
    skipped = sum(
        statuses.get(status, 0)
        for status in (api.states.SKIPPED_PASSWORD, api.states.SKIPPED_UNSUPPORTED)
    )
    states = [
        (statuses.get(api.states.INDEXED, 0), "indexed"),
        (indexing, "still indexing"),
        (statuses.get(api.states.FAILED, 0), "failed"),
        (skipped, "skipped"),
    ]
    named = [f"{count:,} {word}" for count, word in states if count]
    words = f"There are {_plural(total, 'document')} {where}"
    words += f": {', '.join(named)}." if named else "."
    if facts["types"]:
        kinds = ", ".join(f"{count:,} {name}" for name, count in facts["types"].items())
        words += f" By type: {kinds}."
    if facts["private_folders"]:
        words += " Private folders aren't counted in this chat."
    return words


async def library_facts(state: AskState, config: RunnableConfig) -> dict[str, Any]:
    """From the database, so it takes no model and nothing leaves the Mac."""
    thread_id, _ = _ids(config)
    thread = await asyncio.to_thread(api.threads.get, thread_id)
    scope = _library_scope(thread["scope"], _allowed_private(state, thread))
    facts = await asyncio.to_thread(api.search.library_facts, scope)
    text = _facts_in_words(facts, thread["scope"]["kind"] == "library")
    return {
        "answer": {
            "kind": "answer",
            "counted": True,
            "parts": [{"text": text}],
            "citations": [],
            "model": "",
            "local": True,
            "mode": state["mode"],
            "cost_usd": 0.0,
            "tokens": 0,
            "activity": [f"Counted your documents, with {NO_CLOUD_CALL}"],
            "fell_back": False,
            "excluded_private": state.get("excluded_private", False),
        },
        "turns": [AIMessage(COUNTED_WORDS)],
    }


# ---- Plan ----


def _quick_kind(question: str, thread: dict[str, Any]) -> str:
    if COMPARE_WORDS.search(question):
        return "comparison"
    if LIST_WORDS.search(question):
        return "list"
    if (
        thread["scope"]["kind"] in ("documents", "search")
        and len(thread["scope"]["ids"]) > 1
    ):
        return "comparison"
    return "lookup"


def _plan_parts(
    state: AskState, thread: dict[str, Any], discussed: list[dict[str, str]]
) -> list[api.Part]:
    budget = for_mode(state["mode"])
    parts = []
    if thread["summary"]:
        parts.append(
            api.Part(
                api.PartKind.SUMMARY,
                thread["summary"],
                summary_version=thread["summary_version"],
            )
        )
    for turn in _fit_turns(state.get("turns", [])[:-1], budget):
        parts.append(api.Part(api.PartKind.TURN, f"{turn.type}: {_text(turn)}"))
    if discussed:
        parts.append(api.Part(api.PartKind.TITLES, "Documents discussed:"))
        parts.extend(
            api.Part(api.PartKind.TITLES, f"{at}. {d['title']}", document_ids=[d["id"]])
            for at, d in enumerate(discussed, start=1)
        )
    near_miss = state.get("near_miss")
    if near_miss:
        parts.append(api.Part(api.PartKind.TITLES, "Titles close to the question:"))
        parts.extend(
            api.Part(
                api.PartKind.TITLES,
                miss["title"],
                document_ids=[miss["id"]] if miss["id"] else [],
            )
            for miss in near_miss
        )
    parts.append(api.Part(api.PartKind.QUESTION, state["question"]))
    return parts


def _fit_turns(turns: list[AnyMessage], budget: Budget) -> list[AnyMessage]:
    """The newest turns that fit, in order."""
    kept: list[AnyMessage] = []
    spent = 0
    for turn in reversed(turns):
        spent += api.count_tokens(_text(turn))
        if spent > budget.turns:
            break
        kept.insert(0, turn)
    return kept


def _first_turn(state: AskState, thread: dict[str, Any]) -> bool:
    return len(state.get("turns", [])) <= 1 and not thread.get("summary")


def _skips_the_model(state: AskState, thread: dict[str, Any], kind: str) -> bool:
    """A first-turn lookup with no filter keeps the first word within 2 s."""
    first = _first_turn(state, thread)
    could_be_a_list = MAYBE_LIST_WORDS.search(state["question"]) is not None
    return (
        first
        and kind == "lookup"
        and not could_be_a_list
        and not state.get("near_miss")
    )


async def plan(state: AskState, config: RunnableConfig) -> dict[str, Any]:
    thread_id, _ = _ids(config)
    thread = await asyncio.to_thread(api.threads.get, thread_id)
    kind = _quick_kind(state["question"], thread)
    if _skips_the_model(state, thread, kind):
        return {"query": state["question"], "kind": kind, "keywords": []}
    discussed = await asyncio.to_thread(api.threads.discussed, thread_id)
    parts = _plan_parts(state, thread, discussed)
    found = await _call_plan(state, thread_id, parts)
    chosen = kind if kind != "lookup" else found.get("kind") or kind
    return {
        "query": found.get("query") or state["question"],
        "kind": _with_a_subject(chosen, found, thread),
        "date_from": found.get("date_from"),
        "date_to": found.get("date_to"),
        "keywords": found.get("keywords") or [],
        "date_field": _named_field(found.get("date_field")),
        "amount_field": _named_field(found.get("amount_field")),
    }


def _with_a_subject(kind: str, found: dict[str, Any], thread: dict[str, Any]) -> str:
    """A list of everything in the library doesn't answer a question about one thing."""
    if kind != "list" or thread["scope"]["kind"] != "library":
        return kind
    named = _subject_words(found.get("keywords") or [])
    return kind if named or found.get("date_from") or found.get("date_to") else "lookup"


def _named_field(word: Any) -> str | None:
    """A model may name the asking, not a kind of date or amount."""
    if not word or str(word).casefold() in {*ASKING_WORDS, *GENERAL_DATE_WORDS}:
        return None
    return str(word)


async def _call_plan(
    state: AskState, thread_id: str, parts: list[api.Part]
) -> dict[str, Any]:
    task = models.Task(
        api.Purpose.REWRITE,
        prompts.load("plan"),
        parts,
        thread_id,
        state.get("helper_local", state.get("local", False)),
        model=state.get("helper_model") or None,
        local_tag=state.get("local_tag"),
    )
    return await models.structured(task, PLAN_SCHEMA)


# ---- Retrieve ----


def _ranked(query: str, found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scores = api.rerank(query, [item["text"] for item in found])
    for item, score in zip(found, scores, strict=True):
        item["score"] = score
    return sorted(found, key=lambda item: -item["score"])


def _one_per_cap(
    ranked: list[dict[str, Any]], keep: int, single: bool
) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    per: dict[str, int] = {}
    for item in ranked:
        count = per.get(item["document_id"], 0)
        if not single and count >= PER_DOCUMENT:
            continue
        per[item["document_id"]] = count + 1
        kept.append(item)
        if len(kept) == keep:
            break
    return kept


def _reference(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": item["chunk_id"],
        "score": item["score"],
        "document_id": item["document_id"],
    }


def _allowed_private(state: AskState, thread: dict[str, Any]) -> bool:
    """A cloud route never reads Private, whatever the scope says."""
    return bool(thread["private_tainted"]) and state.get("local", False)


def _own_words(state: AskState, thread: dict[str, Any], dated: bool) -> str | None:
    """A follow-up's own words, searched too, so a poor rewording can't lose what they
    find. Skipped only when it would repeat the first search: the same words, and no
    dates the helper's search had and this one won't."""
    question = state.get("question") or ""
    if _first_turn(state, thread) or not question.strip():
        return None
    same = question.strip().casefold() == state["query"].strip().casefold()
    return None if same and not dated else question


def _best_of(*searches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merged by passage, each ranked by the better of its reranker scores."""
    best: dict[str, dict[str, Any]] = {}
    for found in searches:
        for item in found:
            held = best.get(item["chunk_id"])
            if held is None or item["score"] > held["score"]:
                best[item["chunk_id"]] = item
    return sorted(best.values(), key=lambda item: -item["score"])


def _lookup(state: AskState, thread: dict[str, Any]) -> dict[str, Any]:
    allowed = _allowed_private(state, thread)
    scope = _library_scope(thread["scope"], allowed, state)
    deep = state["mode"] == "deep"
    fetch = DEEP_CANDIDATES if deep else CANDIDATES
    keep = DEEP_KEEP if deep else KEEP
    found = api.search.retrieve(state["query"], scope, fetch)
    ranked = _ranked(state["query"], found)
    dated = bool(state.get("date_from") or state.get("date_to"))
    own = _own_words(state, thread, dated)
    if own is not None:
        # The thread's scope without the helper's dates, which could filter out
        # what my own words find.
        plain = _library_scope(thread["scope"], allowed)
        ranked = _best_of(ranked, _ranked(own, api.search.retrieve(own, plain, fetch)))
    single = thread["scope"]["kind"] == "documents" and len(thread["scope"]["ids"]) == 1
    kept = _one_per_cap(ranked, keep, single)
    used = {item["chunk_id"] for item in kept}
    return {
        "evidence": [_reference(i) for i in kept],
        "also_searched": [_reference(i) for i in ranked if i["chunk_id"] not in used][
            :8
        ],
    }


def _scope_ids(thread: dict[str, Any]) -> list[str]:
    with api.read() as connection:
        return api.threads.scope_document_ids(connection, thread["scope"]) or []


def _comparison(state: AskState, thread: dict[str, Any]) -> dict[str, Any]:
    """Each selected document retrieved on its own, then a passage each, in turn, until
    the budget is full. Anything left out is named, by code."""
    allowed = _allowed_private(state, thread)
    ids = _scope_ids(thread)
    own = _own_words(state, thread, dated=False)  # each document's scope has no dates
    per_document = []
    for document_id in ids:
        scope = api.search.Scope(include_private=allowed, document_ids=(document_id,))
        items = _ranked(state["query"], api.search.retrieve(state["query"], scope, 8))
        if own is not None:
            mine = _ranked(own, api.search.retrieve(own, scope, 8))
            items = _best_of(items, mine)[:8]
        per_document.append(items)
    budget = for_mode(state["mode"]).evidence
    chosen, spent, covered, round_no = [], 0, set(), 0
    while any(len(items) > round_no for items in per_document):
        for items in per_document:
            if len(items) <= round_no:
                continue
            cost = api.count_tokens(items[round_no]["text"])
            if spent + cost > budget:
                continue
            spent += cost
            chosen.append({**_reference(items[round_no]), "round": round_no})
            covered.add(items[round_no]["document_id"])
        round_no += 1
    return {
        "evidence": chosen,  # each with its round, which a local fit ranks by
        "also_searched": [],
        "left_out": [d for d in ids if d not in covered],
    }


def _subject_words(keywords: list[str]) -> list[str]:
    """A model may add words about the asking. Only the documents' subject counts."""
    return [k for k in keywords if k.casefold() not in ASKING_WORDS and not k.isdigit()]


def _matches_keywords(card: dict[str, Any], keywords: list[str]) -> bool:
    if not keywords:
        return True
    words = " ".join(
        [
            card["title"],
            card["summary"],
            card["doc_type"],
            *card["tags"],
            card["document_title"],
        ]
    ).casefold()
    return any(keyword.casefold() in words for keyword in keywords)


def _date_of(card: dict[str, Any], state: AskState) -> str | None:
    """The date the question is about: the one the owner named, or the card's first."""
    field = state.get("date_field")
    if not field:
        return card["doc_date"]
    stem = field.casefold()[:FIELD_STEM]
    for found in card["dates"]:
        if stem in str(found.get("label", "")).casefold():
            return api.cards.iso_date(str(found.get("value", "")))
    return None


def _has_period(state: AskState) -> bool:
    return bool(state.get("date_from") or state.get("date_to"))


def _in_period(dated: str | None, state: AskState) -> bool:
    if dated is None:
        return not _has_period(state)
    start, end = state.get("date_from"), state.get("date_to")
    return not ((start and dated < start) or (end and dated > end))


def _money_in(text: str) -> float | None:
    """Signed, and a dollar figure before any other number: "Premium 2025: $100"."""
    money = MONEY.search(text)
    if money:
        negative = money.group(1) or money.group(2)
        figure = money.group(3)
    else:
        number = NUMBER.search(text)
        if number is None:
            return None
        negative, figure = number.group(1), number.group(2)
    amount = float(figure.replace(",", ""))
    return -amount if negative else amount


def _amount_of(card: dict[str, Any], state: AskState) -> tuple[float | None, str]:
    """The amount asked about, if the owner named one, and no other. Otherwise the first
    that was spent: a coverage limit isn't money paid."""
    texts = [str(text) for text in card["amounts"]]
    field = state.get("amount_field")
    stem = field.casefold()[:FIELD_STEM] if field else None
    named = [text for text in texts if stem and stem in text.casefold()]
    spent = [text for text in texts if not NOT_SPENDING.search(text)]
    for text in named if stem else spent:
        amount = _money_in(text)
        if amount is not None:
            return amount, text
    return None, ""


def _table_row(
    card: dict[str, Any], dated: str | None, amount: tuple[float | None, str]
) -> dict[str, Any]:
    return {
        "document_id": card["document_id"],
        "revision_id": card["active_revision_id"],
        "title": card["document_title"],
        "doc_date": dated,
        "amount": amount[0],
        "amount_text": amount[1] or (card["amounts"][0] if card["amounts"] else ""),
        "summary": card["summary"],
        "sensitivity": card["sensitivity"],
        "doc_type": card["doc_type"],
    }


def _named_row(card: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "document_id": card["document_id"],
        "title": card["document_title"],
        "doc_date": card["doc_date"],
        "reason": reason,
    }


@dataclass
class Sorted:
    """Every card in scope: counted, named as not counted, or not checked at all."""

    rows: list[dict[str, Any]]
    named: list[dict[str, Any]]
    unchecked: int = 0


def _sort_complete(card: dict[str, Any], state: AskState, found: Sorted) -> None:
    dated = _date_of(card, state)
    if dated is None and state.get("date_field") and _has_period(state):
        found.named.append(_named_row(card, "no date"))
    elif _in_period(dated, state):
        amount = _amount_of(card, state)
        if amount[0] is None and _wants_total(state):
            found.named.append(_named_row(card, "no amount"))
        else:
            found.rows.append(_table_row(card, dated, amount))


def _sort_cards(state: AskState, cards: list[dict[str, Any]]) -> Sorted:
    """A complete card is matched on its fields. A pending one has only its title to go
    on: a match is named, and the rest are counted, so a total never claims more than it
    checked."""
    found = Sorted([], [])
    keywords = _subject_words(state.get("keywords", []))
    for card in cards:
        matches = _matches_keywords(card, keywords)
        if card["state"] != "complete":
            if matches:
                found.named.append(_named_row(card, "pending"))
            else:
                found.unchecked += 1
        elif matches:
            _sort_complete(card, state, found)
    return found


def _list_table(state: AskState, thread: dict[str, Any]) -> dict[str, Any]:
    """Worked out by code from the card of every document in scope."""
    allowed = _allowed_private(state, thread)
    with api.read() as connection:
        ids = api.threads.scope_document_ids(connection, thread["scope"])
    cards = api.cards.in_scope(ids, api.policy.scope_sensitivities(allowed))
    found = _sort_cards(state, cards)
    rows = sorted(found.rows, key=lambda r: (r["doc_date"] or "", r["title"]))
    total = round(sum(r["amount"] for r in rows if r["amount"] is not None), 2)
    return {
        "rows": rows,
        "pending": found.named,
        "unchecked_pending": found.unchecked,
        "total": total,
        "wants_total": _wants_total(state),
    }


def _wants_total(state: AskState) -> bool:
    return bool(
        re.search(r"\b(how much|total|sum|spend|spent|cost)\b", state["question"], re.I)
    )


def _retrieve(state: AskState, thread: dict[str, Any]) -> dict[str, Any]:
    if state["kind"] == "list":
        return {
            "table": _list_table(state, thread),
            "evidence": [],
            "also_searched": [],
        }
    if state["kind"] == "comparison" and thread["scope"]["kind"] in (
        "documents",
        "search",
        "collection",
    ):
        return _comparison(state, thread)
    return _lookup(state, thread)


async def retrieve(state: AskState, config: RunnableConfig) -> dict[str, Any]:
    thread_id, _ = _ids(config)
    thread = await asyncio.to_thread(api.threads.get, thread_id)
    found = await asyncio.to_thread(_retrieve, state, thread)
    label = api.threads.scope_label(thread["scope"])
    activity = [*state.get("activity", []), f"Searched “{state['query']}” in {label}"]
    _step("reading", f"{len(found.get('evidence', []))} passages")
    return {**found, "activity": activity}


# ---- Grade ----


def _near_miss_titles(state: AskState, thread: dict[str, Any]) -> list[dict[str, Any]]:
    """Each with its document, so policy can ask about it again as it leaves."""
    scope = _library_scope(thread["scope"], _allowed_private(state, thread), state)
    found = api.search.run(state["query"], scope)["documents"]
    return [{"id": d["id"], "title": d["title"]} for d in found]


async def grade(state: AskState, config: RunnableConfig) -> dict[str, Any]:
    """Allows one retry, with near-miss titles, when the top score is weak."""
    evidence = state.get("evidence", [])
    weak = state["kind"] == "lookup" and (
        not evidence or evidence[0]["score"] < WEAK_SCORE
    )
    if not weak or state.get("attempts", 0) >= 1:
        return {"attempts": state.get("attempts", 0) + 1, "near_miss": []}
    thread_id, _ = _ids(config)
    thread = await asyncio.to_thread(api.threads.get, thread_id)
    titles = await asyncio.to_thread(_near_miss_titles, state, thread)
    return {
        "attempts": state.get("attempts", 0) + 1,
        "near_miss": titles or [{"id": None, "title": state["query"]}],
    }


def after_grade(state: AskState) -> str:
    return "plan" if state.get("near_miss") else "answer"


# ---- Answer ----


def _fetch_evidence(state: AskState, thread: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetched by id, and checked again against the active revision and policy."""
    allowed = _allowed_private(state, thread)
    return api.search.evidence(
        [e["chunk_id"] for e in state.get("evidence", [])], allowed
    )


def _bounded(element: dict[str, Any], room: int) -> tuple[dict[str, Any], int]:
    """An element within `room` tokens, cut if it has to be, and what it comes to."""
    cost = api.count_tokens(element["text"])
    if cost <= room:
        return element, cost
    cut = element["text"]
    while cost > room:
        cut = cut[: int(len(cut) * room / cost * 0.95)]
        cost = api.count_tokens(cut)
    return {**element, "text": cut}, cost


def _fit_evidence(items: list[dict[str, Any]], budget: Budget) -> list[dict[str, Any]]:
    """What's counted is what's sent: every element a passage points at, each once, and
    none so long it crowds out the rest."""
    kept, spent, seen = [], 0, set()
    per_element = budget.evidence // PART_OF_ELEMENT
    for item in items:
        fitted = []
        for element in item["elements"]:
            if element["id"] in seen:
                continue
            room = min(per_element, budget.evidence - spent)
            if room < min(per_element, LEAST_ROOM):
                break
            bounded, cost = _bounded(element, room)
            seen.add(element["id"])
            spent += cost
            fitted.append(bounded)
        if fitted:
            kept.append({**item, "elements": fitted})
    return kept


def _card_evidence(
    table: dict[str, Any], budget: Budget
) -> tuple[list[dict[str, Any]], int]:
    """Cards as evidence, as many as fit. The totals still cover every one."""
    items, spent = [], 0
    for row in table["rows"]:
        facts = f"{row['title']}, {row['doc_type']}, {row['doc_date']}."
        text = f"{facts} {row['amount_text']}. {row['summary']}"
        spent += api.count_tokens(text)
        if spent > budget.evidence:
            break
        items.append(
            {
                "chunk_id": f"card:{row['document_id']}",
                "text": text,
                "document_id": row["document_id"],
                "revision_id": row["revision_id"],
                "title": row["title"],
                "sensitivity": row["sensitivity"],
                "elements": [
                    {
                        "id": f"card:{row['document_id']}",
                        "text": text,
                        "locator": {"kind": "card", "field": "amounts"},
                        "label": "Card · Amounts",
                    }
                ],
            }
        )
    return items, len(table["rows"]) - len(items)


def _source_uri(item: dict[str, Any]) -> str:
    place = f"{item['document_id']}/rev/{item['revision_id']}"
    return f"securefolio://doc/{place}#chunk={item['chunk_id']}"


def _search_results(
    parts: list[api.Part], items: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    blocks = []
    for part in parts:
        if part.kind not in (api.PartKind.PASSAGE, api.PartKind.CARD):
            continue
        item = items[part.text]
        blocks.append(
            {
                "type": "search_result",
                "source": _source_uri(item),
                "title": _titled(item),
                "content": [
                    {"type": "text", "text": e["text"]} for e in item["elements"]
                ],
                "citations": {"enabled": True},
            }
        )
    return blocks


def _titled(item: dict[str, Any]) -> str:
    """A search result's title, with a sheet's column names where its rows came
    without them. A title is never cited, so a quote stays the document's own words."""
    columns = item.get("columns")
    return f"{item['title']} · Columns: {columns}" if columns else item["title"]


def _context_text(parts: list[api.Part]) -> str:
    lines = []
    for part in parts:
        if part.kind == api.PartKind.SUMMARY:
            lines.append(f"<summary>{part.text}</summary>")
        elif part.kind == api.PartKind.MEMORY:
            lines.append(f"<memory>{part.text}</memory>")
        elif part.kind == api.PartKind.TURN:
            lines.append(f"<turn>{part.text}</turn>")
    return "\n".join(lines)


def _base_parts(
    state: AskState, thread: dict[str, Any], budget: Budget
) -> list[api.Part]:
    parts = []
    if thread["summary"]:
        parts.append(
            api.Part(
                api.PartKind.SUMMARY,
                thread["summary"],
                summary_version=thread["summary_version"],
            )
        )
    recalled = [
        m for m in api.memories.all_active() if m["id"] in state.get("memory_ids", [])
    ]
    spent = 0
    for item in recalled:
        spent += api.count_tokens(item["text"])
        if spent <= budget.memories:
            parts.append(
                api.Part(
                    api.PartKind.MEMORY,
                    item["text"],
                    sensitivity=item["sensitivity"],
                    memory_id=item["id"],
                )
            )
    for turn in _fit_turns(state.get("turns", [])[:-1], budget):
        parts.append(api.Part(api.PartKind.TURN, f"{turn.type}: {_text(turn)}"))
    return parts


def _answer_model(state: AskState) -> str:
    """The model its route chose. Only a run checkpointed before the route kept it
    reads Settings."""
    found = state.get("model")
    if found and not state.get("local"):
        return found
    return api.setting(api.MODEL_DEEP if state["mode"] == "deep" else api.MODEL_ANSWER)


def _deep_estimate(call: models.CloudCall, price: api.Price) -> float:
    """What the Deep request costs at most: the whole of it, built as it will be sent,
    and its reserved output."""
    return api.cost_at(price, models.sent_tokens(call), call.max_tokens)


@dataclass
class Gathered:
    """What an answer is written from."""

    items: list[dict[str, Any]]
    base: list[api.Part]
    unseen: int
    table: dict[str, Any] | None = None
    left_out: list[str] | None = None


def _result_line(table: dict[str, Any]) -> str:
    found = f"The result: {len(table['rows'])} matching documents"
    if table["wants_total"]:
        found += f", with a total of ${table['total']:,.2f}"
    return f"{found}. {len(table['pending'])} more couldn't be counted."


async def _send_cloud(
    state: AskState,
    thread_id: str,
    gathered: Gathered,
    budget: Budget,
    on_text: Callable[[str], Awaitable[None]],
) -> models.Reply:
    return await models.ask_cloud(
        _answer_call(state, thread_id, gathered, budget), on_text
    )


def _answer_call(
    state: AskState, thread_id: str, gathered: Gathered, budget: Budget
) -> models.CloudCall:
    """The cloud answer's request, built once for the call and the same way for its Deep
    estimate, so the card prices what goes."""
    items, base = gathered.items, gathered.base
    by_text = {item["chunk_id"]: item for item in items}
    parts = [
        *base,
        *[
            api.Part(
                api.PartKind.CARD
                if item["chunk_id"].startswith("card:")
                else api.PartKind.PASSAGE,
                item["chunk_id"],
                sensitivity=item["sensitivity"],
                document_ids=[item["document_id"]],
            )
            for item in items
        ],
        api.Part(api.PartKind.QUESTION, state["question"]),
    ]

    table = state.get("table")
    listing = table is not None
    if table is not None:
        parts.insert(
            -1,
            api.Part(
                api.PartKind.TOTAL,
                _result_line(table),
                document_ids=[
                    row["document_id"] for row in [*table["rows"], *table["pending"]]
                ],
            ),
        )

    def render(current: list[api.Part]) -> list[Any]:
        content: list[dict[str, Any]] = _search_results(current, by_text)
        context = _context_text(current)
        if context:
            content.append({"type": "text", "text": context})
        if table is not None:
            content.append({"type": "text", "text": _result_line(table)})
        content.append({"type": "text", "text": state["question"]})
        return [HumanMessage(cast("Any", content))]

    def render_marked(current: list[api.Part]) -> list[Any]:
        """For a provider without Claude's citations: the same parts, each passage in
        a numbered `<source>` tag, cited with `[n]` as the local model does."""
        kept = {part.text for part in current}
        sources = [pair for pair in _sources_of(items) if pair[0]["chunk_id"] in kept]
        body = _local_body(sources, current, state["question"])
        if table is not None:
            body = f"{body}\n{_result_line(table)}"
        return [HumanMessage(body)]

    model = _answer_model(state)
    marked = api.providers.of(model).citations == "markers"
    prompt = "answer_list" if listing else ("answer_local" if marked else "answer")
    return models.CloudCall(
        api.Purpose.ANSWER,
        model,
        prompts.load(prompt),
        parts,
        render_marked if marked else render,
        thread_id,
        budget.output,
        price=state.get("price"),  # a Deep answer goes at the price its card showed
    )


def _sources_of(
    items: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [(item, element) for item in items for element in item["elements"]]


def _tagged(at: int, item: dict[str, Any], element: dict[str, Any]) -> str:
    return (
        f'<source id="{at}" title="{item["title"]}"{_columns_of(item)}>'
        f"{element['text']}</source>"
    )


def _local_body(
    sources: list[tuple[dict[str, Any], dict[str, Any]]],
    base: list[api.Part],
    question: str,
) -> str:
    tagged = [
        _tagged(at, item, element) for at, (item, element) in enumerate(sources, 1)
    ]
    return "\n".join([*tagged, _context_text(base), question])


def _columns_of(item: dict[str, Any]) -> str:
    """A sheet's column names as the tag's attribute, escaped: they're document text."""
    columns = item.get("columns")
    return f' columns="{html.escape(columns, quote=True)}"' if columns else ""


def _by_rank(state: AskState, items: list[dict[str, Any]]) -> list[int]:
    """The passages' places, best first. Search hands them over in rank order, but for
    a comparison of chosen documents (`_comparison`), round by round, each document's
    next best in turn in the documents' order: those are ranked by their round, then
    their score. A comparison in a library-wide scope is searched as a lookup, in
    score order, and has no rounds."""
    rounds = {
        e["chunk_id"]: (e["round"], -e["score"])
        for e in state.get("evidence", [])
        if "round" in e
    }
    if not rounds:
        return list(range(len(items)))
    last = (len(items), 0.0)
    keys = [(*rounds.get(i["chunk_id"], last), at) for at, i in enumerate(items)]
    return [key[-1] for key in sorted(keys)]


def _fit_local(
    state: AskState, gathered: Gathered, system: str, rest: str
) -> tuple[Gathered, int]:
    """The local prompt fitted to its context. The rules, the question, the thread's
    context and the reserve for thinking and output come first; then the passages by
    rank, each whole, until the next wouldn't fit. The best is always kept. Those kept
    go in their own order. A list's cards left out are counted as unseen, and a
    comparison names any document no longer covered."""
    room = int(models.LOCAL_CONTEXT * (1 - models.FIT_MARGIN))
    room -= models.LOCAL_ANSWER_TOKENS + api.count_tokens(system)
    room -= api.count_tokens(rest)
    items = gathered.items
    chosen: set[int] = set()
    spent = 0
    for at in _by_rank(state, items):
        cost = sum(
            api.count_tokens(_tagged(at + 1, items[at], e))
            for e in items[at]["elements"]
        )
        if chosen and spent + cost > room:
            break
        chosen.add(at)
        spent += cost
    dropped = len(items) - len(chosen)
    if not dropped:
        return gathered, 0
    kept = [item for at, item in enumerate(items) if at in chosen]
    _fits["prompts"] += 1
    logger.info(
        "a local prompt was fitted to its context: %s",
        json.dumps({"dropped": dropped, "kept": len(kept), "fitted": _fits["prompts"]}),
    )
    if gathered.table is not None:
        fitted = replace(gathered, items=kept, unseen=gathered.unseen + dropped)
    else:
        fitted = replace(gathered, items=kept, left_out=_left_out(state, kept))
    return fitted, dropped


async def _send_local(
    state: AskState,
    thread_id: str,
    gathered: Gathered,
    on_text: Callable[[str], Awaitable[None]],
) -> tuple[models.Reply, Gathered]:
    """The reply, and what it was written from: the passages as fitted, which its
    markers number."""
    table = state.get("table")
    rest = "\n".join([_context_text(gathered.base), state["question"]])
    if table is not None:
        rest = f"{rest}\n{_result_line(table)}"
    system = prompts.load("answer_list" if table is not None else "answer_local")
    gathered, dropped = _fit_local(state, gathered, system, rest)
    body = _local_body(_sources_of(gathered.items), gathered.base, state["question"])
    if table is not None:
        body = f"{body}\n{_result_line(table)}"
    counted = api.count_tokens(system) + api.count_tokens(body)
    of = models.CallOf(thread_id, fit=models.Fit(counted, dropped))
    reply = await models.ask_local(system, body, on_text, state.get("local_tag"), of)
    return reply, gathered


def _stream_tokens() -> Callable[[str], Awaitable[None]]:
    writer = get_stream_writer()

    async def on_text(text: str) -> None:
        writer({"event": "token", "data": {"text": text}})

    return on_text


def _gather(state: AskState, thread: dict[str, Any], budget: Budget) -> Gathered:
    """Worked out again here, as what Retrieve saw may have changed since."""
    base = _base_parts(state, thread, budget)
    if state.get("table") is not None:
        table = _list_table(state, thread)
        items, unseen = _card_evidence(table, budget)
        return Gathered(items, base, unseen, table)
    items = _fit_evidence(_fetch_evidence(state, thread), budget)
    return Gathered(items, base, 0, left_out=_left_out(state, items))


def _left_out(state: AskState, items: list[dict[str, Any]]) -> list[str]:
    """A comparison names any document the fitted evidence no longer covers."""
    named = list(state.get("left_out", []))
    if state["kind"] != "comparison":
        return named
    covered = {item["document_id"] for item in items}
    for reference in state.get("evidence", []):
        document_id = reference["document_id"]
        if document_id not in covered and document_id not in named:
            named.append(document_id)
    return named


@dataclass
class Consent:
    """Whether the answer goes in Deep, and the one price it goes at."""

    choice: str  # "deep" or "standard"
    model: str | None = None  # the model the card showed, which is the one that goes
    price: api.Price | None = None


def _same_card(shown: dict[str, Any], card: dict[str, Any]) -> bool:
    """What the owner agreed to is what would go: the model, its price, the estimate."""
    return (
        shown.get("model") == card["model"]
        and list(shown.get("price") or []) == card["price"]
        and shown.get("estimate_usd") == card["estimate_usd"]
    )


def _ask_about_cost(
    state: AskState, gathered: Gathered, budget: Budget, ids: tuple[str, str]
) -> Consent:
    """Before any side effect in this node: a resume runs it again. The owner's answer
    is read from the run's record, with the card it answered, before anything else,
    never from a replay of the question. A "Standard" always stands. A "Deep" counts
    only while the model, its price and the estimate are what that card showed; if
    any changed, a new card asks."""
    if state["mode"] != "deep" or state.get("local"):
        return Consent("deep")
    model = _answer_model(state)
    price = api.price_of(model)  # read once: the card, the estimate and the call
    items = gathered.items
    card: dict[str, Any] | None = None
    estimate = 0.0
    if price is not None:
        found = api.catalog.get(model)
        built: AskState = {**state, "table": gathered.table}
        estimate = _deep_estimate(_answer_call(built, ids[0], gathered, budget), price)
        card = {
            "kind": "deep_cost",
            "question": state["question"],
            "model": model,  # the consent names the model it prices, on any provider
            "model_name": found.name if found else model,
            "price": list(price),
            "estimate_usd": round(estimate, 2),  # only what's shown is rounded
            "documents": len({i["document_id"] for i in items}),
            "spent_usd": round(api.spend.spent_this_month(), 2),
            "cap_usd": float(api.setting(api.spend_cap_key())),
        }
    while True:
        answered = api.threads.card_answer(ids[1], "deep_cost")
        if answered is not None and answered["choice"] == "standard":
            return Consent("standard")  # before anything else, the price included
        if card is None:
            return Consent("deep", model)  # no known price: the gate refuses it
        if answered is not None and _same_card(answered["card"], card):
            return Consent("deep", model, price)
        if estimate <= float(api.setting(api.ASK_FIRST)):
            return Consent("deep", model, price)
        # A replay returns an earlier answer here, which the record above has already
        # weighed; the next call with none waiting pauses on this card.
        interrupt({**card, "id": uuid.uuid4().hex})


async def answer(state: AskState, config: RunnableConfig) -> dict[str, Any]:
    thread_id, run_id = _ids(config)
    thread = await asyncio.to_thread(api.threads.get, thread_id)
    budget = for_mode(state["mode"])
    gathered = await asyncio.to_thread(_gather, state, thread, budget)
    consent = _ask_about_cost(state, gathered, budget, (thread_id, run_id))
    current: AskState = {**state, "table": gathered.table, "price": consent.price}
    if consent.model is not None:
        current["model"] = consent.model  # the card's model goes, at the card's price
    if consent.choice == "standard" and state["mode"] != "standard":
        # Standard instead of Deep is Standard's own route: its model may be local.
        route = await asyncio.to_thread(_route_of, thread, "standard")
        current = {
            **current,
            "mode": "standard",
            "local": route.local,
            "model": route.model,
            "route_reason": route.reason,
        }
    _step("writing", _writer(current))
    if not gathered.items:
        return _reply_update(current, None, gathered, False, run_id)
    on_text = _stream_tokens()
    if current.get("local"):
        reply, gathered = await _send_local(current, thread_id, gathered, on_text)
        return _reply_update(current, reply, gathered, False, run_id)
    try:
        reply = await _send_cloud(current, thread_id, gathered, budget, on_text)
    except models.NoConnection:
        provider = api.providers.of(_answer_model(current)).name
        _step("writing", _writer(current, f"No connection to {provider}"))
        reply, gathered = await _send_local(current, thread_id, gathered, on_text)
        return _reply_update(current, reply, gathered, True, run_id)
    return _reply_update(current, reply, gathered, False, run_id)


def _writer(state: AskState, fell_back: str | None = None) -> str:
    """Who writes the answer, as the writing step shows it: Cloud AI and the model, or
    Local AI and its tag, with why it fell back."""
    if fell_back is None and not state.get("local"):
        model = _answer_model(state)
        found = api.catalog.get(model)
        return f"Cloud AI, {found.name if found else model}"
    line = f"Local AI, {state.get('local_tag') or models.local_tag()}"
    why = fell_back or state.get("route_reason")
    if why and why != "Offline":
        line += f" · {why.split('. ')[0].rstrip('.')}"
    return line


def _handed(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The passages as they were sent, after fitting: what the model had to go on."""
    return [
        {
            "chunk_id": item["chunk_id"],
            "document_id": item["document_id"],
            "title": item["title"],
            "columns": item.get("columns"),
            "elements": [
                {"id": e["id"], "text": e["text"], "locator": e.get("locator")}
                for e in item["elements"]
            ],
        }
        for item in items
    ]


def _written(
    run_id: str, reply: models.Reply | None, gathered: Gathered
) -> dict[str, Any]:
    """Kept apart from the answer, which Verify replaces: a reply Verify throws away
    is still here to see. Each turn's replaces the last."""
    return {
        "run_id": run_id,
        "text": reply.text if reply else "",
        "content": reply.content if reply else "",
        "cites": _cites(reply),
        "local": bool(reply.local) if reply else False,
        "model": reply.model if reply else "",
        "handed": _handed(gathered.items),
        "table": gathered.table,  # a list's, worked out from the cards; else None
    }


def _reply_update(
    state: AskState,
    reply: models.Reply | None,
    gathered: Gathered,
    fell_back: bool,
    run_id: str,
) -> dict[str, Any]:
    activity = list(state.get("activity", []))
    why = state.get("route_reason")
    if state.get("local") and why and why != "Offline":
        activity.append(f"{why.rstrip('.')}, so Local AI on this Mac answered")
    if fell_back:
        provider = api.providers.of(_answer_model(state)).name
        activity.append(
            f"No connection to {provider}, so Local AI on this Mac answered"
        )
    return {
        "mode": state["mode"],
        "table": gathered.table,
        "left_out": gathered.left_out or [],
        "fell_back": fell_back,
        "activity": activity,
        "answer": {
            "text": reply.text if reply else "",
            "content": reply.content if reply else "",
            "model": reply.model if reply else "",
            "local": reply.local if reply else bool(state.get("local")),
            "cites": _cites(reply),
            "tokens": reply.tokens if reply else 0,
            "items": gathered.items,
            "unseen": gathered.unseen,
        },
        "written": _written(run_id, reply, gathered),
        "cost_usd": reply.cost_usd if reply else 0.0,
    }


# ---- Verify ----


def _no_evidence(state: AskState, thread: dict[str, Any]) -> dict[str, Any]:
    allowed = _allowed_private(state, thread)
    scope = _library_scope(thread["scope"], allowed)
    closest = api.search.evidence(
        [
            e["chunk_id"]
            for e in [*state.get("evidence", []), *state.get("also_searched", [])]
        ][:3],
        allowed,
    )
    return {
        "kind": "no_evidence",
        "coverage": api.search.coverage(scope),
        "closest": [
            {
                "document_id": item["document_id"],
                "title": item["title"],
                "element_id": item["elements"][0]["id"] if item["elements"] else None,
                "label": item["elements"][0]["label"] if item["elements"] else "",
                "text": item["text"][:300],
            }
            for item in closest
        ],
    }


def _named_titles(ids: list[str]) -> list[str]:
    return [api.search.title_of(d) for d in ids]


def _also_searched(state: AskState, allowed: bool) -> list[dict[str, Any]]:
    items = api.search.evidence(
        [e["chunk_id"] for e in state.get("also_searched", [])], allowed
    )
    return [
        {
            "document_id": item["document_id"],
            "title": item["title"],
            "element_id": item["elements"][0]["id"] if item["elements"] else None,
            "label": item["elements"][0]["label"] if item["elements"] else "",
            "snippet": item["text"][:240],
        }
        for item in items
    ]


def _table_payload(
    table: dict[str, Any], items: list[dict[str, Any]]
) -> dict[str, Any]:
    """Each row's citation opens its card and preview."""
    rows = []
    for number, row in enumerate(table["rows"], start=1):
        rows.append(
            {**row, "n": number, "locator": {"kind": "card", "field": "amounts"}}
        )
    return {
        "rows": rows,
        "total": table["total"],
        "wants_total": table["wants_total"],
        "pending": table["pending"],
        "unchecked_pending": table["unchecked_pending"],
        "shown_to_model": len(items),
    }


def _built_from(reply: dict[str, Any], items: list[dict[str, Any]]) -> citations.Built:
    if not reply["text"].strip() or citations.is_refusal(reply["text"]):
        return citations.Built()
    if reply["local"] or reply.get("cites") == "markers":
        return citations.from_markers(reply["text"], _sources_of(items))
    return citations.from_claude(reply["content"], items)


def _cites(reply: models.Reply | None) -> str:
    """How this answer cites: Claude's `search_result` citations, or `[n]` markers,
    from the local model or a cloud model without Claude's."""
    if reply is None or reply.local:
        return "markers"
    return api.providers.of(reply.model).citations


def _verified(state: AskState, thread: dict[str, Any]) -> dict[str, Any]:
    reply = state["answer"]
    items = reply["items"]
    allowed = _allowed_private(state, thread)
    table = state.get("table")
    if table is not None and table["rows"]:
        built = _built_from(reply, items)
        shown = _table_payload(table, items)
        return {
            "kind": "list",
            "parts": _with_figures(built, items, shown),
            "citations": built.citations,
            "table": shown,
        }
    if not items:
        return _no_evidence(state, thread)
    built = _built_from(reply, items)
    if not citations.has_valid_citation(built):
        shape = citations.shape_of(reply["text"], len(_sources_of(items)))
        logger.info(
            "an answer had no citation it could use: %s",
            json.dumps({"local": reply["local"], **shape}),
        )
        return _no_evidence(state, thread)
    payload: dict[str, Any] = {
        "kind": "answer",
        "parts": _with_figures(built, items, None),
        "citations": built.citations,
        "also_searched": _also_searched(state, allowed),
    }
    left_out = state.get("left_out")
    if left_out:
        payload["left_out"] = _named_titles(left_out)
    return payload


def _with_figures(
    built: citations.Built, items: list[dict[str, Any]], table: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Each cited sentence with its figures checked against the passages it cites, as
    the model was given them, with their documents' titles. Nothing is rewritten: a
    part only gains what the check found, and a list's total is never marked."""
    given = {
        element["id"]: figures.Passage(element["text"], item["title"])
        for item, element in _sources_of(items)
    }
    passages = {
        c["n"]: given[c["element_id"]]
        for c in built.citations
        if c["element_id"] in given
    }
    found = figures.check(built.parts, passages, figures.worked_of(table))
    return [
        {**part, "figures": checked} if checked else part
        for part, checked in zip(built.parts, found, strict=True)
    ]


def _figures_line(payload: dict[str, Any]) -> str | None:
    """Activity's line when a figure is marked: counts only."""
    every = [f for part in payload.get("parts", []) for f in part.get("figures") or []]
    unshown = sum(1 for f in every if f["state"] == figures.UNSHOWN)
    if not unshown:
        return None
    logger.info(
        "figures checked: %s", json.dumps({"checked": len(every), "unshown": unshown})
    )
    return (
        f"Checked {len(every)} figures against their passages: {unshown} not in "
        "its sources"
    )


def _cloud_line(model: str, tokens: int, cost: float) -> str:
    found = api.catalog.get(model)
    return (
        f"Cloud AI, {found.name if found else model} · {tokens:,} tokens · ${cost:.2f}"
    )


async def verify(state: AskState, config: RunnableConfig) -> dict[str, Any]:
    thread_id, _ = _ids(config)
    thread = await asyncio.to_thread(api.threads.get, thread_id)
    payload = await asyncio.to_thread(_verified, state, thread)
    reply = state["answer"]
    written = (
        f"Local AI, {reply['model']} · no cloud call"
        if reply["local"]
        else _cloud_line(reply["model"], reply["tokens"], state.get("cost_usd", 0.0))
    )
    marked = _figures_line(payload)
    activity = [
        *state.get("activity", []),
        f"Wrote the answer with {written}",
        *([marked] if marked else []),
    ]
    found = None if reply["local"] else api.catalog.get(reply["model"])
    payload.update(
        {
            "model": reply["model"],
            # As the answer shows it: the cloud model's name, or the local tag.
            "model_name": found.name if found else reply["model"],
            "local": reply["local"],
            # Whether the helper steps stayed on this Mac too: a local answer's may go
            # to a cloud model, at a cost the answer's own doesn't hold.
            "helper_local": bool(state.get("helper_local", reply["local"])),
            "mode": state["mode"],
            "cost_usd": state.get("cost_usd", 0.0),
            "tokens": reply["tokens"],
            "activity": activity,
            "fell_back": state.get("fell_back", False),
            "excluded_private": state.get("excluded_private", False),
        }
    )
    return {
        "answer": payload,
        "turns": [AIMessage(plain.text_of(payload) or citations.NO_EVIDENCE)],
    }


# ---- Save + remember ----


def _documents_used(payload: dict[str, Any]) -> list[str]:
    """In the order the answer gives them, so "the second one" means its second row."""
    table = payload.get("table") or {"rows": []}
    used = [c["document_id"] for c in payload.get("citations", [])]
    used.extend(row["document_id"] for row in table["rows"])
    return list(dict.fromkeys(used))


def _rested_on(state: AskState, payload: dict[str, Any]) -> list[str]:
    """Everything the answer may have drawn on, cited or not: a list's documents that
    couldn't be counted included, as the model is given them in its total."""
    found = [e["document_id"] for e in state.get("evidence", [])]
    table = state.get("table") or {"rows": []}
    found.extend(row["document_id"] for row in table["rows"])
    found.extend(row["document_id"] for row in table.get("pending", []))
    found.extend(_documents_used(payload))
    return list(dict.fromkeys(found))


def _save_turn(thread_id: str, run_id: str, state: AskState) -> bool:
    # What it rested on and the memories it was given go with it, so a report of it
    # can tell, each time it's run, whether it's Private.
    payload = {
        **state["answer"],
        "rested_on": _rested_on(state, state["answer"]),
        "memory_ids": list(state.get("memory_ids") or []),
    }
    return api.threads.save_turn(
        thread_id,
        run_id,
        {
            "role": api.threads.ROLE_ASSISTANT,
            "content": plain.text_of(payload),
            "answer": payload,
        },
        _documents_used(payload),
        _rested_on(state, payload),
    )


async def _rebuild_summary(
    state: AskState, thread: dict[str, Any], run_id: str, turns: list[str]
) -> bool:
    """Whether a summary was written: turns may leave the checkpoint only if one was."""
    thread_id = thread["id"]
    previous = []
    if thread["summary"]:
        previous.append(
            api.Part(
                api.PartKind.SUMMARY,
                thread["summary"],
                summary_version=thread["summary_version"],
            )
        )
    parts = [*previous, *[api.Part(api.PartKind.TURN, t) for t in turns]]
    task = models.Task(
        api.Purpose.SUMMARY,
        prompts.load("summary"),
        parts,
        thread_id,
        bool(
            state.get("helper_local", state.get("local")) or thread["private_tainted"]
        ),
        model=state.get("helper_model") or None,
        local_tag=state.get("local_tag"),
    )
    try:
        text = await models.helper_text(task)
    except (models.LocalNotReady, models.HelperCut):
        return False  # the turns stay as they are, and the next turn can summarize
    clean = await asyncio.to_thread(memory.scrub, text)
    ordinal = await asyncio.to_thread(_latest_ordinal, thread_id)
    await asyncio.to_thread(api.threads.set_summary, thread_id, clean, ordinal, run_id)
    return True


def _latest_ordinal(thread_id: str) -> int:
    rows = api.threads.transcript(thread_id)
    return rows[-1]["ordinal"] if rows else 0


def _cleared_summary_turns(thread: dict[str, Any], kept: set[str]) -> list[str]:
    """A summary cleared by a deleted memory comes back from the transcript, without
    that memory's source message."""
    rows = api.threads.transcript(thread["id"])
    sources = api.memories.deleted_sources(thread["id"])
    return [
        f"{row['role']}: {row['content']}"
        for row in rows
        if row["ordinal"] <= thread["summary_through"]
        and row["id"] not in sources
        and row["content"] not in kept
    ]


def _deleted_texts(thread_id: str) -> set[str]:
    sources = api.memories.deleted_sources(thread_id)
    rows = api.threads.transcript(thread_id)
    return {row["content"] for row in rows if row["id"] in sources}


async def save(state: AskState, config: RunnableConfig) -> dict[str, Any]:
    thread_id, run_id = _ids(config)
    await asyncio.to_thread(_save_turn, thread_id, run_id, state)
    if state.get("blocked"):
        # Passages found, with no model: nothing is summarised now, as a summary
        # takes a model. The next question a model answers folds the history.
        return {}
    thread = await asyncio.to_thread(api.threads.get, thread_id)
    budget = for_mode(state["mode"])
    turns = state.get("turns", [])
    total = sum(api.count_tokens(_text(turn)) for turn in turns)
    folded: list[Any] = []
    if total > budget.turns:
        folded = turns[: max(len(turns) // 2, 1)]
    needs_rebuild = not thread["summary"] and thread["summary_through"] > 0
    if folded or needs_rebuild:
        gone = await asyncio.to_thread(_deleted_texts, thread_id)
        text = [f"{t.type}: {_text(t)}" for t in folded if _text(t) not in gone]
        if needs_rebuild:
            kept = {_text(t) for t in turns}
            older = await asyncio.to_thread(_cleared_summary_turns, thread, kept)
            text = [*older, *text]
        if not await _rebuild_summary(state, thread, run_id, text):
            folded = []
    return {"turns": [RemoveMessage(id=t.id) for t in folded]}


def build(checkpointer: Any) -> Any:
    graph = StateGraph(AskState)
    graph.add_node("prepare", prepare)
    graph.add_node("library_facts", library_facts)
    graph.add_node("passages", passages)
    graph.add_node("plan", plan)
    graph.add_node("retrieve", retrieve)
    graph.add_node("grade", grade)
    graph.add_node("answer", answer)
    graph.add_node("verify", verify)
    graph.add_node("save", save)
    graph.add_edge(START, "prepare")
    graph.add_conditional_edges(
        "prepare", after_prepare, ["passages", "library_facts", "plan"]
    )
    graph.add_edge("passages", "save")
    graph.add_edge("library_facts", "save")
    graph.add_edge("plan", "retrieve")
    graph.add_edge("retrieve", "grade")
    graph.add_conditional_edges("grade", after_grade, ["plan", "answer"])
    graph.add_edge("answer", "verify")
    graph.add_edge("verify", "save")
    graph.add_edge("save", END)
    return graph.compile(checkpointer=checkpointer)
