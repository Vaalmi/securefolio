"""Each local helper call has a length and a time limit. A structured helper cut at
one returns what an unreadable reply does, so the question carries on; a summary cut
at one leaves the thread's summary and turns as they were. Each cut is recorded and
counted in the log."""

import asyncio
import logging
import time
from typing import Any

import pytest

from agent import api, harness, models, prompts
from agent.budgets import Budget
from backend import threads
from backend.build_mode import STUB_OLLAMA_ENV
from testing.local_calls import TAG, chat_replying, recorded, task
from testing.stub_models import ollama_reply, stub_ollama

STALL = 3.0  # seconds a stub that never answers in time takes
QUICK = models.Limit(1024, 0.3)


@pytest.fixture
def ollama(library, monkeypatch):
    with stub_ollama() as stub:
        monkeypatch.setenv(STUB_OLLAMA_ENV, stub.url)
        stub.reply = ollama_reply
        yield stub


class TestAStructuredHelper:
    def test_one_that_never_answers_ends_at_its_limit_as_unreadable(
        self, ollama, monkeypatch, caplog
    ):
        ollama.reply = chat_replying('{"query": "rent"}', stall=STALL)
        monkeypatch.setitem(models.HELPER_LIMITS, api.Purpose.REWRITE, QUICK)
        began = time.monotonic()
        with caplog.at_level(logging.INFO, logger="agent.models"):
            found, calls = recorded(
                models.structured(task(api.Purpose.REWRITE), harness.PLAN_SCHEMA)
            )
        assert found == {}, "what an unreadable reply returns"
        assert time.monotonic() - began < STALL, "it didn't wait for the reply"
        assert [c["outcome"] for c in calls] == [models.CUT]
        assert '"purpose": "rewrite", "by": "time"' in caplog.text

    def test_one_cut_at_its_length_reads_as_unreadable(self, ollama, caplog):
        ollama.reply = chat_replying('{"query": "re', done_reason="length")
        with caplog.at_level(logging.INFO, logger="agent.models"):
            found, calls = recorded(
                models.structured(task(api.Purpose.MEMORY), {"type": "object"})
            )
        assert found == {}
        assert [c["outcome"] for c in calls] == [models.CUT]
        assert '"purpose": "memory", "by": "length"' in caplog.text

    @pytest.mark.parametrize(
        "purpose", [api.Purpose.REWRITE, api.Purpose.MEMORY, api.Purpose.CARD]
    )
    def test_its_length_limit_goes_with_the_call(self, ollama, purpose):
        ollama.reply = chat_replying('{"query": "rent"}')
        asyncio.run(models.structured(task(purpose), harness.PLAN_SCHEMA))
        sent = [r.body for r in ollama.requests if r.path == "/api/chat"]
        limit = models.HELPER_LIMITS[purpose].tokens
        assert [body["options"]["num_predict"] for body in sent] == [limit]


class TestASummary:
    def test_it_goes_once_without_thinking_within_its_length(self, ollama):
        ollama.reply = chat_replying("A short summary.")
        text = asyncio.run(models.helper_text(task(api.Purpose.SUMMARY)))
        assert text == "A short summary."
        sent = [r.body for r in ollama.requests if r.path == "/api/chat"]
        assert len(sent) == 1, "no retry"
        assert sent[0]["think"] is False
        limit = models.HELPER_LIMITS[api.Purpose.SUMMARY].tokens
        assert sent[0]["options"]["num_predict"] == limit

    @pytest.mark.parametrize(
        ("content", "done_reason", "stall"),
        [
            ("A summary that ran o", "length", 0.0),  # cut at its length
            ("A summary.", "stop", STALL),  # never answered in time
            ("   ", "stop", 0.0),  # empty
        ],
    )
    def test_one_cut_or_empty_raises(
        self, ollama, monkeypatch, content, done_reason, stall
    ):
        ollama.reply = chat_replying(content, done_reason, stall)
        monkeypatch.setitem(models.HELPER_LIMITS, api.Purpose.SUMMARY, QUICK)
        with pytest.raises(models.HelperCut):
            asyncio.run(models.helper_text(task(api.Purpose.SUMMARY)))

    def test_one_cut_at_its_limit_leaves_the_threads_summary_and_turns(
        self, ollama, monkeypatch
    ):
        ollama.reply = chat_replying("A summary that ran o", "length")
        thread = threads.create(None, "standard")["id"]
        run = threads.start_run(thread, "Why?", None)
        threads.set_summary(thread, "Before.", 0)
        before = threads.get(thread)
        monkeypatch.setattr(harness, "_save_turn", lambda *_given: True)
        monkeypatch.setattr(harness, "for_mode", lambda _m: Budget(1, 1, 1, 1, 1))
        turns = [
            harness.HumanMessage("word " * 50, id="m1"),
            harness.HumanMessage("word " * 50, id="m2"),
        ]
        state: Any = {
            "mode": "standard",
            "turns": turns,
            "answer": {},
            "local": True,
            "local_tag": TAG,
        }
        config: Any = {"configurable": {"thread_id": thread, "run_id": run}}
        kept = asyncio.run(harness.save(state, config))
        assert kept == {"turns": []}, "no turn leaves the checkpoint"
        after = threads.get(thread)
        assert after["summary"] == before["summary"] == "Before."
        assert after["summary_through"] == before["summary_through"]
        assert after["summary_version"] == before["summary_version"]
        asked = [r.body for r in ollama.requests if r.path == "/api/chat"]
        assert asked, "the summary was asked for"
        assert prompts.load("summary") in str(asked[0]["messages"][0]["content"])
