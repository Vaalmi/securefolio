"""Owns what's remembered: scoring, extraction, editing, deletion, recap expiry."""

import asyncio
import re
from dataclasses import dataclass
from typing import Any

import numpy as np

from agent import api, models, prompts
from agent.budgets import PINNED_MEMORIES

TOP_MATCHES = 5
SAME_MEANING = 0.92
OWN_WORDS_SHARE = 0.5
WORD = re.compile(r"\w+")
SENTENCE = re.compile(r"(?<=[.!?])\s+")
QUOTED = re.compile(
    r'"[^"]*"|“[^”]*”|‘[^’]*’|`[^`]*`|(?<!\w)\'[^\']+\'(?!\w)|^\s*>.*$', re.M
)
LINKING_WORDS = {
    "a", "an", "and", "are", "at", "be", "by", "for", "from", "has", "have", "i", "in",
    "is", "it", "my", "of", "on", "or", "our", "the", "to", "was", "were", "with",
}  # fmt: skip
LEAST_QUOTE = 3  # words: shorter than this and a match means nothing
QUOTED_RUN = 5
QUOTE_CANDIDATES = 5
ACTIONS = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {
                        "type": "string",
                        "enum": ["ADD", "UPDATE", "DELETE", "NOOP"],
                    },
                    "kind": {"type": "string", "enum": ["preference", "fact", "recap"]},
                    "text": {"type": "string"},
                    "memory_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
                "required": ["op", "kind", "text", "memory_id"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["actions"],
    "additionalProperties": False,
    "title": "memory_actions",
}


def _words(text: str) -> set[str]:
    return {word.casefold() for word in WORD.findall(text)}


def _cosine(query: Any, rows: list[dict[str, Any]]) -> list[float]:
    found = []
    for row in rows:
        vector = api.from_blob(row["embedding"]) if row["embedding"] else None
        found.append(0.0 if vector is None else float(np.dot(query, vector)))
    return found


def recall(text: str, include_private: bool) -> list[dict[str, Any]]:
    """Pinned preferences up to their budget, then the top matches. A Private memory
    never comes back for a cloud route."""
    levels = api.policy.scope_sensitivities(include_private)
    rows = api.memories.all_active(levels)
    pinned, spent = [], 0
    for row in (r for r in rows if r["pinned"]):
        spent += api.count_tokens(row["text"])
        if spent <= PINNED_MEMORIES:
            pinned.append(row)
    rest = [r for r in rows if not r["pinned"]]
    scores = _cosine(api.embed_query(text), rest)
    order = sorted(range(len(rest)), key=lambda at: -scores[at])[:TOP_MATCHES]
    chosen = pinned + [rest[at] for at in order]
    api.memories.touch([row["id"] for row in chosen])
    return [
        {"id": r["id"], "text": r["text"], "sensitivity": r["sensitivity"]}
        for r in chosen
    ]


def _matches_tombstone(text: str, vector: Any) -> bool:
    known = api.memories.tombstones()
    wanted = api.memories.normalized_hash(text)
    for stone in known:
        if stone["text_hash"] == wanted:
            return True
        if stone["embedding"] is not None and (
            float(np.dot(vector, api.from_blob(stone["embedding"]))) >= SAME_MEANING
        ):
            return True
    return False


def scrub(summary: str) -> str:
    """Removes any sentence a tombstone matches, by hash or by meaning, so a rebuilt
    summary can't quote a deleted memory, whatever the model replied."""
    kept = [
        sentence
        for sentence in SENTENCE.split(summary)
        if sentence.strip()
        and not _matches_tombstone(sentence, api.embed_query(sentence))
    ]
    return " ".join(kept)


def _stated(content: str) -> str:
    """What the owner said themselves, without anything they quoted."""
    return QUOTED.sub(" ", content)


def _quotes_a_document(text: str) -> bool:
    """A run of words found in a document's own text. This is read here, on this Mac,
    whichever route the extraction took."""
    words = WORD.findall(text.casefold())
    if len(words) < LEAST_QUOTE:
        return False
    length = min(QUOTED_RUN, len(words))
    runs = [" ".join(words[at : at + length]) for at in range(len(words) - length + 1)]
    scope = api.search.Scope(include_private=True)
    for item in api.search.retrieve(text, scope, QUOTE_CANDIDATES):
        passage = " ".join(WORD.findall(item["text"].casefold()))
        if any(run in passage for run in runs):
            return True
    return False


@dataclass
class Extraction:
    """What one pass read: whose chat, the owner's turns in it, the memories listed."""

    thread: dict[str, Any]
    owner: list[dict[str, Any]]
    known: set[str]

    def sources_of(self, text: str, kind: str) -> list[str]:
        """The owner's messages a memory comes from, or none when nothing they said in
        their own words backs it. A recap may draw on several, and names them all."""
        words = _words(text)
        if not words or self._from_a_quote(words) or _quotes_a_document(text):
            return []
        shares = {
            turn["id"]: len(words & _words(_stated(turn["content"]))) / len(words)
            for turn in self.owner
        }
        if kind == "recap":
            said = {w for t in self.owner for w in _words(_stated(t["content"]))}
            if len(words & said) / len(words) < OWN_WORDS_SHARE:
                return []
            return [message for message, share in shares.items() if share > 0]
        best = max(shares, key=lambda message: shares[message], default=None)
        return [best] if best and shares[best] >= OWN_WORDS_SHARE else []

    def _from_a_quote(self, words: set[str]) -> bool:
        """A word that only something quoted supplied means the fact is the quote's,
        however it's been reworded: "the rent is two thousand" from a quoted lease."""
        said = {w for t in self.owner for w in _words(_stated(t["content"]))}
        whole = {w for t in self.owner for w in _words(t["content"])}
        return bool((words - LINKING_WORDS) & (whole - said))

    def provenance(self, message_ids: list[str]) -> dict[str, Any]:
        return {
            "source_thread_id": self.thread["id"],
            "source_label": self.thread["title"],
            "source_message_ids": message_ids,
        }


def _add(action: dict[str, Any], read: Extraction) -> None:
    text = action["text"].strip()
    message_ids = read.sources_of(text, action["kind"])
    if not message_ids:
        return
    vector = api.embed_query(text)
    if _matches_tombstone(text, vector):
        return
    api.memories.add(
        {
            "kind": action["kind"],
            "text": text,
            "embedding": api.to_blob(vector),
            "sensitivity": api.STANDARD,
            **read.provenance(message_ids),
        }
    )


def _update(action: dict[str, Any], read: Extraction) -> None:
    text = action["text"].strip()
    message_ids = read.sources_of(text, action["kind"])
    if not message_ids:
        return
    vector = api.embed_query(text)
    if not _matches_tombstone(text, vector):
        api.memories.edit(
            action["memory_id"], text, api.to_blob(vector), read.provenance(message_ids)
        )


def _apply(actions: list[dict[str, Any]], read: Extraction) -> None:
    """A model can only name memories it was shown."""
    for action in actions:
        if action["op"] == "ADD":
            _add(action, read)
        elif action.get("memory_id") in read.known:
            if action["op"] == "UPDATE":
                _update(action, read)
            elif action["op"] == "DELETE":
                delete(action["memory_id"])


def edit(memory_id: str, text: str) -> None:
    vector = api.embed_query(text)
    if _matches_tombstone(text, vector):
        raise api.refusals_conflict("You deleted this memory, so it can't come back.")
    api.memories.edit(memory_id, text, api.to_blob(vector))


def delete(memory_id: str) -> None:
    api.memories.delete(memory_id, None)


async def extract(thread_id: str) -> None:
    """Runs on the thread's own route once it's been idle. Only the owner's messages
    since the last pass are read."""
    thread = api.threads.get(thread_id)
    if thread["memory_off"] or not api.setting(api.MEMORY_ON):
        return
    turns = api.threads.transcript(thread_id)
    fresh = [t for t in turns if t["ordinal"] > thread["memory_through"]]
    owner = [t for t in fresh if t["role"] == api.threads.ROLE_USER]
    if not owner:
        return
    text = "\n".join(f"Owner: {t['content']}" for t in owner)
    known = api.memories.all_active(
        api.policy.scope_sensitivities(thread["private_tainted"])
    )
    parts = [
        api.Part(api.PartKind.TURN, text),
        *[
            api.Part(
                api.PartKind.MEMORY,
                f"{m['id']}: {m['text']}",
                sensitivity=m["sensitivity"],
                memory_id=m["id"],
            )
            for m in known
        ],
    ]
    reply = await models.structured(
        models.Task(
            api.Purpose.MEMORY,
            prompts.load("memory"),
            parts,
            thread_id,
            bool(thread["private_tainted"]),
        ),
        ACTIONS,
    )
    read = Extraction(thread, owner, {m["id"] for m in known})
    await asyncio.to_thread(_apply, reply.get("actions", []), read)
    await asyncio.to_thread(
        api.threads.set_memory_through, thread_id, turns[-1]["ordinal"]
    )
