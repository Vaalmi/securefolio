"""Kept runs a judge read, and OpenAI's side for a run on OpenAI alone, for the tests
of judging again."""

import json

from evals import kept as keeping
from evals.tests.kept_runs import LOCAL, SET, answer, kept_run
from evals.tests.stub_providers import answering_with
from testing.stub_models import completion

TERRA = "openai:gpt-6.1-terra"
JUDGE_SAYS = "You check one"  # how both judge instructions begin
ANSWER = answering_with("The pet deposit is $299.11. [1]")


def handed(*given: tuple[str, str, str]) -> list[dict]:
    """What an answer was handed, as a kept row keeps it: for each (document id,
    title, text), one passage with one element."""
    return [
        {
            "chunk_id": f"chunk-{at}",
            "document_id": document,
            "title": title,
            "elements": [{"id": f"element-{at}", "text": text}],
        }
        for at, (document, title, text) in enumerate(given)
    ]


def openai_judging(supported: bool):
    """OpenAI's side for a run on OpenAI alone: it plans, answers and judges."""

    def reply(path: str, body: dict) -> dict:
        if JUDGE_SAYS in json.dumps(body):
            return completion(body, json.dumps({"supported": supported}))
        return ANSWER(path, body)

    return reply


def judged_row(question: str = "q1") -> dict:
    """As `run._judged` keeps one: a cited sentence, an uncited one, and a sentence
    citing a Private passage left for the hand check."""
    return answer(
        question,
        True,
        sentences=[
            {"text": "The deposit is $299.11. ", "cite": [1]},
            {"text": "It is paid at signing. ", "uncited": True},
            {"text": "Private. ", "cite": [2]},
        ],
        citations=[
            {"n": 1, "title": "Apartment lease"},
            {"n": 2, "title": "Tax return"},
        ],
        claims=[
            {
                "claim": "The deposit is $299.11.",
                "title": "Apartment lease",
                "words": "$299.11",
                "passage": "A pet deposit of $299.11.",
                "supported": True,
            },
            {"claim": "It is paid at signing.", "uncited": True, "supported": False},
            {"claim": "Private.", "title": "Tax return", "by_hand": True},
        ],
        model="gemma4:26b",
        written={
            "handed": handed(("lease", "Apartment lease", "A pet deposit of $299.11.")),
            "table": None,
        },
        calls=[{"purpose": "eval_judge", "input_tokens": 1000}],
    )


def judged_run(root, rows=None, **manifest):
    rows = rows or [judged_row()]
    report = {"spent_usd": 0.42, "limit_usd": 1.0, "answer_model": LOCAL}
    manifest = {"asked": "asked-1", **manifest}
    folder = kept_run(root, LOCAL, rows, question_set=SET, **manifest)
    (folder / keeping.REPORT).write_text(json.dumps(report))
    return folder
