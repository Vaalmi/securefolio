"""Every local call names its context size, and a local answer's prompt is fitted to
it: the lowest-ranked passages are left out whole, never the rules, the question or
the best passage. Each fit is counted in the log and goes with the call's record."""

import asyncio
import logging
import math
from typing import Any

import pytest

from agent import api, harness, models, prompts
from backend.build_mode import STUB_OLLAMA_ENV
from testing.local_calls import TAG, chat_replying, recorded, task
from testing.stub_models import ollama_reply, stub_ollama

QUESTION = "When is the rent due?"
TEXTS = [  # by rank, best first
    "The rent is due on the first of each month. " * 20,
    "A late fee of $50 applies after the fifth. " * 20,
    "The landlord may enter with a day's notice. " * 20,
    "Pets need the landlord's written consent. " * 20,
]


@pytest.fixture
def ollama(library, monkeypatch):
    with stub_ollama() as stub:
        monkeypatch.setenv(STUB_OLLAMA_ENV, stub.url)
        stub.reply = ollama_reply
        yield stub


def _item(n: int, text: str) -> dict[str, Any]:
    return {
        "chunk_id": f"c{n}",
        "document_id": f"d{n}",
        "revision_id": f"r{n}",
        "title": f"Lease part {n}.pdf",
        "elements": [{"id": f"e{n}", "text": text, "locator": {"page": 1}}],
    }


def _gathered(table: dict[str, Any] | None = None) -> harness.Gathered:
    items = [_item(n, text) for n, text in enumerate(TEXTS, 1)]
    return harness.Gathered(items, [], 0, table)


ROUNDS = {"c1": 0, "c2": 0, "c3": 1}  # d1's best, d2's best, then d1's second


def _ref(chunk_id: str, document_id: str, score: float) -> dict[str, Any]:
    return {"chunk_id": chunk_id, "document_id": document_id, "score": score}


def _state(table: dict[str, Any] | None = None) -> Any:
    return {
        "question": QUESTION,
        "kind": "lookup",
        "local_tag": TAG,
        "table": table,
        "evidence": [],
    }


def _cost(n: int) -> int:
    item = _item(n, TEXTS[n - 1])
    return api.count_tokens(harness._tagged(n, item, item["elements"][0]))  # noqa: SLF001


def _context_holding(passages: int, system: str, rest: str) -> int:
    """A context size whose fitted room holds the best `passages` and half the next."""
    room = sum(_cost(n) for n in range(1, passages + 1)) + _cost(passages + 1) // 2
    fixed = models.LOCAL_ANSWER_TOKENS + api.count_tokens(system)
    fixed += api.count_tokens(rest)
    return math.ceil((room + fixed) / (1 - models.FIT_MARGIN))


async def _quiet(_text: str) -> None:
    return None


def _sent(ollama) -> list[dict[str, Any]]:
    return [r.body for r in ollama.requests if r.path == "/api/chat"]


def _send(state: Any, gathered: harness.Gathered):
    return recorded(harness._send_local(state, "t", gathered, _quiet))  # noqa: SLF001


class TestTheContextSize:
    def test_an_answer_names_it(self, ollama):
        ollama.reply = chat_replying("The rent is due on the first [1].")
        asyncio.run(models.ask_local("Rules.", "Question?", tag=TAG))
        assert {b["options"]["num_ctx"] for b in _sent(ollama)} == {
            models.LOCAL_CONTEXT
        }

    @pytest.mark.parametrize(
        "purpose", [api.Purpose.REWRITE, api.Purpose.MEMORY, api.Purpose.CARD]
    )
    def test_a_structured_helper_names_it(self, ollama, purpose):
        ollama.reply = chat_replying('{"query": "rent"}')
        asyncio.run(models.structured(task(purpose), harness.PLAN_SCHEMA))
        assert [b["options"]["num_ctx"] for b in _sent(ollama)] == [
            models.LOCAL_CONTEXT
        ]

    def test_a_summary_names_it(self, ollama):
        ollama.reply = chat_replying("A short summary.")
        asyncio.run(models.helper_text(task(api.Purpose.SUMMARY)))
        assert [b["options"]["num_ctx"] for b in _sent(ollama)] == [
            models.LOCAL_CONTEXT
        ]

    def test_a_picture_names_it(self, ollama):
        ollama.reply = chat_replying('{"kind": "figure", "description": "A cat."}')
        models.read_picture(b"\x89PNG", tag=TAG)
        assert [b["options"]["num_ctx"] for b in _sent(ollama)] == [
            models.LOCAL_CONTEXT
        ]


class TestTheFit:
    def test_a_full_context_leaves_out_the_lowest_ranked_passages_whole(
        self, ollama, monkeypatch, caplog
    ):
        ollama.reply = chat_replying("The rent is due on the first [1].")
        system = prompts.load("answer_local")
        size = _context_holding(2, system, f"\n{QUESTION}")
        monkeypatch.setattr(models, "LOCAL_CONTEXT", size)
        with caplog.at_level(logging.INFO, logger="agent.harness"):
            (reply, fitted), calls = _send(_state(), _gathered())
        assert [item["chunk_id"] for item in fitted.items] == ["c1", "c2"]
        [body] = _sent(ollama)
        assert body["options"]["num_ctx"] == size
        system_sent, user_sent = (m["content"] for m in body["messages"])
        assert system_sent == system, "the rules are whole"
        assert user_sent.endswith(QUESTION), "the question is there"
        assert TEXTS[0] in user_sent, "each kept passage whole"
        assert TEXTS[1] in user_sent
        assert TEXTS[2] not in user_sent
        assert TEXTS[3] not in user_sent
        assert '<source id="3"' not in user_sent
        assert reply.text == "The rent is due on the first [1]."
        [call] = calls
        assert call["passages_dropped"] == 2
        assert call["prompt_counted"] <= size * (1 - models.FIT_MARGIN)
        assert '"dropped": 2, "kept": 2' in caplog.text

    def test_the_best_passage_stays_even_when_it_alone_is_too_long(
        self, ollama, monkeypatch
    ):
        ollama.reply = chat_replying("The rent is due on the first [1].")
        monkeypatch.setattr(models, "LOCAL_CONTEXT", models.LOCAL_ANSWER_TOKENS)
        (_reply, fitted), calls = _send(_state(), _gathered())
        assert [item["chunk_id"] for item in fitted.items] == ["c1"]
        assert calls[0]["passages_dropped"] == 3

    def test_a_prompt_that_fits_is_sent_as_it_is(self, ollama, caplog):
        ollama.reply = chat_replying("The rent is due on the first [1].")
        gathered = _gathered()
        with caplog.at_level(logging.INFO, logger="agent.harness"):
            (_reply, fitted), calls = _send(_state(), gathered)
        assert fitted is gathered
        assert calls[0]["passages_dropped"] == 0
        assert calls[0]["prompt_counted"] > 0
        assert "fitted to its context" not in caplog.text
        assert all(text in _sent(ollama)[0]["messages"][1]["content"] for text in TEXTS)

    def test_a_lists_cards_left_out_are_counted_as_unseen(self, ollama, monkeypatch):
        ollama.reply = chat_replying("Four documents match [1].")
        table = {"rows": [{}] * 6, "wants_total": False, "pending": []}
        system = prompts.load("answer_list")
        rest = f"\n{QUESTION}\n{harness._result_line(table)}"  # noqa: SLF001
        monkeypatch.setattr(models, "LOCAL_CONTEXT", _context_holding(2, system, rest))
        gathered = harness.Gathered(_gathered().items, [], 2, table)
        (_reply, fitted), _calls = _send(_state(table), gathered)
        assert len(fitted.items) == 2
        assert fitted.unseen == 4, "the two never handed over, and the two left out"

    def test_a_comparison_names_a_document_no_longer_covered(self, ollama, monkeypatch):
        ollama.reply = chat_replying("The first says the rent is due [1].")
        system = prompts.load("answer_local")
        monkeypatch.setattr(
            models, "LOCAL_CONTEXT", _context_holding(2, system, f"\n{QUESTION}")
        )
        state = {
            **_state(),
            "kind": "comparison",
            "evidence": [
                {**_ref(f"c{n}", f"d{n}", 0.5), "round": 0} for n in range(1, 5)
            ],
        }
        (_reply, fitted), _calls = _send(state, _gathered())
        assert fitted.left_out == ["d3", "d4"]

    def test_a_comparison_keeps_each_rounds_best_scores_not_the_first_documents(
        self, ollama, monkeypatch
    ):
        """Round by round in the documents' order, as `_comparison` hands them over:
        d1's best, d2's best, then d1's next and d2's next."""
        ollama.reply = chat_replying("The second says the rent is due [1].")
        items = [_item(1, TEXTS[0]), _item(2, TEXTS[1]), _item(3, TEXTS[2])]
        items[2]["document_id"] = "d1"  # d1's second passage
        scores = {"c1": 0.2, "c2": 0.9, "c3": 0.8}
        system = prompts.load("answer_local")
        room = _cost(2) + _cost(1) // 2  # the best of round one, and half another
        fixed = models.LOCAL_ANSWER_TOKENS + api.count_tokens(system)
        fixed += api.count_tokens(f"\n{QUESTION}")
        size = math.ceil((room + fixed) / (1 - models.FIT_MARGIN))
        monkeypatch.setattr(models, "LOCAL_CONTEXT", size)
        state = {
            **_state(),
            "kind": "comparison",
            "evidence": [
                {**_ref(c, i["document_id"], scores[c]), "round": ROUNDS[c]}
                for c, i in zip(scores, items, strict=True)
            ],
        }
        (_reply, fitted), calls = _send(state, harness.Gathered(items, [], 0))
        assert [item["chunk_id"] for item in fitted.items] == ["c2"]
        assert fitted.left_out == ["d1"]
        [body] = _sent(ollama)
        assert TEXTS[1] in body["messages"][1]["content"]
        assert '<source id="2"' not in body["messages"][1]["content"]
        assert calls[0]["passages_dropped"] == 2

    def test_a_comparisons_next_round_comes_after_every_best(self, ollama, monkeypatch):
        ollama.reply = chat_replying("Both say the rent is due [1][2].")
        items = [_item(1, TEXTS[0]), _item(2, TEXTS[1]), _item(3, TEXTS[2])]
        items[2]["document_id"] = "d1"
        scores = {"c1": 0.2, "c2": 0.3, "c3": 0.9}  # d1's second scores best of all
        system = prompts.load("answer_local")
        room = _cost(1) + _cost(2) + _cost(3) // 2
        fixed = models.LOCAL_ANSWER_TOKENS + api.count_tokens(system)
        fixed += api.count_tokens(f"\n{QUESTION}")
        monkeypatch.setattr(
            models, "LOCAL_CONTEXT", math.ceil((room + fixed) / (1 - models.FIT_MARGIN))
        )
        state = {
            **_state(),
            "kind": "comparison",
            "evidence": [
                {**_ref(c, i["document_id"], scores[c]), "round": ROUNDS[c]}
                for c, i in zip(scores, items, strict=True)
            ],
        }
        (_reply, fitted), _calls = _send(state, harness.Gathered(items, [], 0))
        assert [item["chunk_id"] for item in fitted.items] == ["c1", "c2"]
        assert fitted.left_out == []

    def test_a_library_wide_comparison_keeps_its_score_order(self, ollama, monkeypatch):
        """Searched as a lookup, so handed over best first, with no rounds."""
        ollama.reply = chat_replying("The first says the rent is due [1].")
        items = [_item(1, TEXTS[0]), _item(2, TEXTS[1]), _item(3, TEXTS[2])]
        items[1]["document_id"] = "d1"  # d1's first and second, then d3's
        system = prompts.load("answer_local")
        room = _cost(1) + _cost(2) + _cost(3) // 2
        fixed = models.LOCAL_ANSWER_TOKENS + api.count_tokens(system)
        fixed += api.count_tokens(f"\n{QUESTION}")
        monkeypatch.setattr(
            models, "LOCAL_CONTEXT", math.ceil((room + fixed) / (1 - models.FIT_MARGIN))
        )
        state = {
            **_state(),
            "kind": "comparison",
            "evidence": [
                _ref("c1", "d1", 0.9),
                _ref("c2", "d1", 0.8),
                _ref("c3", "d3", 0.1),
            ],
        }
        (_reply, fitted), _calls = _send(state, harness.Gathered(items, [], 0))
        assert [item["chunk_id"] for item in fitted.items] == ["c1", "c2"]
        assert fitted.left_out == ["d3"]
