"""Search's parts: recall@10 and MRR with each part turned off in turn, asking no
model, and the index's words-only and meaning-only searches they rest on."""

import json

import numpy as np
import pytest

from backend import embedder, index, search
from evals import cli, run, search_parts
from evals.tests.questions import QUESTION


def test_every_part_is_measured_and_no_model_is_asked(lease):
    anthropic, openai = lease
    found = search_parts.measure([QUESTION])
    assert set(found) == set(search_parts.PARTS)
    whole = found["hybrid_reranked"]
    assert whole["questions"] == 1
    assert whole["recall_at_10"] == 1.0, "the lease's deposit, as recall finds it"
    assert found["words_only"]["recall_at_10"] == 1.0, "its words are in the lease"
    assert anthropic.requests == []
    assert openai.requests == []


def test_the_evaluations_recall_is_the_whole_search(lease, monkeypatch):
    asked = []
    real = search_parts.rank
    monkeypatch.setattr(
        search_parts, "rank", lambda q, part: asked.append(part) or real(q, part)
    )
    assert run._rank(QUESTION) == real(QUESTION, search_parts.WHOLE)  # noqa: SLF001
    assert asked == [search_parts.WHOLE]


def test_only_answerable_standard_questions_are_measured():
    private = {**QUESTION, "id": "q25", "private": True}
    refusal = {**QUESTION, "id": "q7", "category": "unanswerable"}
    owed_nothing = {**QUESTION, "id": "q9", "answer": None}
    asked = search_parts.measured([QUESTION, private, refusal, owed_nothing])
    assert [q["id"] for q in asked] == ["q1"]


def test_the_command_prints_the_table_and_asks_no_model(
    lease, tmp_path, monkeypatch, capsys
):
    anthropic, openai = lease
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps([QUESTION]))
    monkeypatch.setattr(run, "QUESTIONS", questions)
    assert cli.main(["--search-parts"]) == 0
    printed = capsys.readouterr().out
    assert "| hybrid_reranked | 1.000 |" in printed
    assert "| meaning_only |" in printed
    assert anthropic.requests == []
    assert openai.requests == []


def test_words_only_never_embeds_the_question(lease, monkeypatch):
    def never(_text):
        raise AssertionError("embedded")

    monkeypatch.setattr(embedder, "embed_query", never)
    found = search.retrieve(QUESTION["question"], search.Scope(), 40, kind=index.WORDS)
    assert any(item["title"] == "Apartment lease" for item in found)


def test_meaning_only_finds_passages_with_none_of_the_words(lease):
    nonsense = "zqxv wkjp"
    assert search.retrieve(nonsense, search.Scope(), 40, kind=index.WORDS) == []
    assert search.retrieve(nonsense, search.Scope(), 40, kind=index.MEANING)


def test_meaning_needs_a_vector_and_an_unknown_kind_is_refused(lease):
    assert index.search("deposit", None, None, 10, index.MEANING) == []
    with pytest.raises(ValueError, match="kind"):
        index.search("deposit", np.zeros(embedder.DIMENSION), None, 10, "both")


def test_ask_searches_hybrid_unless_told(lease, monkeypatch):
    kinds = []
    real = index.search
    monkeypatch.setattr(
        index,
        "search",
        lambda *given, **named: (
            kinds.append(given[4:] or named.get("kind")) or real(*given, **named)
        ),
    )
    search.retrieve(QUESTION["question"], search.Scope(), 40)
    assert kinds == [(index.HYBRID,)]
