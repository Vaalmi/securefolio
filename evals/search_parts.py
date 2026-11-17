"""`npm run eval -- --search-parts`: recall@10 and MRR for search as Ask runs it, and
with each of its parts turned off in turn: no reranker, words only, meaning only. It
asks no model; the embedder and the reranker run on this Mac. The evaluation's own
recall is the first of these."""

from typing import Any, NamedTuple

from backend import agent_service, index, search, vault
from backend.migrations import run as migrations
from evals import scopes, score

FETCH = 40
TOP = 10


class Part(NamedTuple):
    kind: str  # index.HYBRID, index.WORDS or index.MEANING
    reranked: bool


WHOLE = Part(index.HYBRID, reranked=True)  # as Ask searches
PARTS = {
    "hybrid_reranked": WHOLE,
    "no_reranker": Part(index.HYBRID, reranked=False),
    "words_only": Part(index.WORDS, reranked=True),
    "meaning_only": Part(index.MEANING, reranked=True),
}


def rank(question: dict[str, Any], part: Part) -> int | None:
    """Where the expected passage came, within the top 10, searching this way: for
    a follow-up, searching for its stand-alone wording."""
    chosen = scopes.selected(question)
    scope = search.Scope(
        include_private=bool(question["private"]),
        document_ids=tuple(chosen) if chosen else None,
    )
    # A follow-up is searched by the question it stands for alone.
    words = question.get("standalone") or question["question"]
    found = search.retrieve(words, scope, FETCH, kind=part.kind)
    ordered = agent_service.ranked(words, found) if part.reranked else found
    return score.hit(question, ordered, TOP)


def measured(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The questions recall is reported on: Standard first questions, answerable,
    and saying what's owed, as the evaluation's report counts them."""
    return [
        q
        for q in questions
        if not q["private"]
        and not q.get("follows")
        and q["category"] not in score.NO_ANSWER
        and score.assessable(q)
    ]


def measure(questions: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Recall@10 and MRR for each way of searching."""
    asked = measured(questions)
    found = {}
    for name, part in PARTS.items():
        ranks = [rank(question, part) for question in asked]
        hits = [at for at in ranks if at is not None]
        found[name] = {
            "recall_at_10": len(hits) / len(asked) if asked else 0.0,
            "mrr": sum(1 / at for at in hits) / len(asked) if asked else 0.0,
            "questions": len(asked),
        }
    return found


def table(found: dict[str, dict[str, float]]) -> str:
    """The table, in Markdown, for a status page or a report."""
    lines = ["| Search | Recall@10 | MRR | Questions |", "| --- | --- | --- | --- |"]
    lines += [
        f"| {name} | {each['recall_at_10']:.3f} | {each['mrr']:.3f} | "
        f"{each['questions']} |"
        for name, each in found.items()
    ]
    return "\n".join(lines) + "\n"


def main(questions: list[dict[str, Any]]) -> str:
    """On the vault SECUREFOLIO_VAULT names, brought up to date as the app's start
    does, with the models from the shared cache."""
    vault.use_model_cache()
    migrations.upgrade()
    return table(measure(questions))
