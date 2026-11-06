"""Deleting a memory: it isn't extracted again, leaves every summary that quoted it, and
doesn't come back. Deleted at the worst moments, it still doesn't reach a request."""

import asyncio
import json
import uuid

import pytest

from agent import harness, memory, models
from backend import (
    database,
    keychain,
    memories,
    settings,
    sources,
    spend,
    states,
    threads,
    vault,
)
from backend.build_mode import STUB_ANTHROPIC_ENV
from testing.ask_helpers import (
    ask,
    default_reply,
    message,
    new_thread,
    plan_reply,
    system_of,
)
from testing.queue import drain
from testing.stub_models import stub_anthropic


def planted() -> str:
    return f"zq{uuid.uuid4().hex[:12]}"


@pytest.fixture
def cloud(monkeypatch):
    keychain.set_key("test-not-a-real-key")
    with stub_anthropic() as stub:
        monkeypatch.setenv(STUB_ANTHROPIC_ENV, stub.url)
        stub.reply = default_reply
        yield stub
    keychain.delete_key()


@pytest.fixture
def folder(library, tmp_path):
    root = tmp_path / "w"
    root.mkdir()
    (root / "Lease.txt").write_text("The rent is due monthly.\n")
    sources.add_source(str(root), states.STANDARD)
    drain()


def add_memory(text: str, thread_id: str | None = None) -> str:
    return memories.add(
        {
            "kind": "fact",
            "text": text,
            "embedding": None,
            "sensitivity": states.STANDARD,
            "source_thread_id": thread_id,
            "source_label": "A chat",
        }
    )


def actions_reply(actions: list[dict]):
    def reply(_path, body):
        return message(
            body, [{"type": "text", "text": json.dumps({"actions": actions})}]
        )

    return reply


def thread_with_turn(question: str) -> str:
    thread = threads.create(None, "standard")
    threads.start_run(thread["id"], question, None)
    return thread["id"]


def extract(thread_id: str) -> None:
    asyncio.run(memory.extract(thread_id))


class TestExtraction:
    def test_a_stated_fact_is_stored_with_its_source(self, library, cloud):
        thread = thread_with_turn("I prefer short answers, always.")
        cloud.reply = actions_reply(
            [
                {
                    "op": "ADD",
                    "kind": "preference",
                    "text": "I prefer short answers",
                    "memory_id": None,
                }
            ]
        )
        extract(thread)
        stored = memories.all_active()
        assert [m["text"] for m in stored] == ["I prefer short answers"]
        assert stored[0]["source_thread_id"] == thread
        assert stored[0]["sensitivity"] == states.STANDARD

    def test_noop_stores_nothing(self, library, cloud):
        thread = thread_with_turn("When is the rent due?")
        cloud.reply = actions_reply(
            [{"op": "NOOP", "kind": "fact", "text": "", "memory_id": None}]
        )
        extract(thread)
        assert memories.all_active() == []

    def test_document_text_isnt_stored(self, library, cloud):
        phrase = "the tenant shall pay a deposit of two thousand dollars"
        thread = thread_with_turn("What does the lease say?")
        cloud.reply = actions_reply(
            [{"op": "ADD", "kind": "fact", "text": phrase, "memory_id": None}]
        )
        extract(thread)
        assert memories.all_active() == []

    def test_the_switches_stop_it(self, library, cloud):
        thread = thread_with_turn("I prefer short answers.")
        threads.update(thread, memory_off=True)
        extract(thread)
        other = thread_with_turn("I prefer long answers.")
        settings.put(settings.MEMORY_ON, False)
        extract(other)
        assert cloud.requests == []


class TestDelete:
    def test_it_isnt_extracted_again(self, library, cloud):
        text = "I prefer short answers"
        add_id = add_memory(text)
        memory.delete(add_id)
        thread = thread_with_turn("I prefer short answers.")
        cloud.reply = actions_reply(
            [{"op": "ADD", "kind": "preference", "text": text, "memory_id": None}]
        )
        extract(thread)
        assert memories.all_active() == []

    def test_a_tombstone_holds_no_text(self, library):
        secret = planted()
        memory.delete(add_memory(f"My code is {secret}."))
        assert secret not in json.dumps(
            [{"hash": t["text_hash"]} for t in memories.tombstones()]
        )
        assert secret not in vault.tombstone_ledger().read_text()

    def test_it_leaves_a_summary_that_quoted_it(self, library):
        secret = f"My code is {planted()}."
        thread = threads.create(None, "standard")
        threads.set_summary(thread["id"], f"They said: {secret} Later more.", 2)
        memory.delete(add_memory(secret))
        again = threads.get(thread["id"])
        assert again["summary"] == ""
        assert again["summary_through"] == 2

    def test_a_rebuilt_summary_doesnt_quote_it(self, library):
        secret = f"My code is {planted()}."
        memory.delete(add_memory(secret))
        assert secret not in memory.scrub(f"Kept this. {secret} And this.")
        assert "Kept this." in memory.scrub(f"Kept this. {secret} And this.")

    def test_deleted_after_prepare_doesnt_reach_the_request(
        self, folder, cloud, app_client
    ):
        secret = f"My code is {planted()}."
        memory_id = ""

        def delete_when_planning(_path, body):
            if "stands alone" in system_of(body):
                memory.delete(memory_id)
                return plan_reply(body)
            return default_reply(_path, body)

        thread = new_thread(app_client)
        settled = default_reply
        cloud.reply = settled
        ask(app_client, thread, "When is the rent due?")
        memory_id = add_memory(secret)
        cloud.requests.clear()
        cloud.reply = delete_when_planning
        ask(app_client, thread, "What about the second rent?")
        assert secret not in cloud.all_text()

    def test_a_summary_cleared_after_prepare_doesnt_reach_the_request(
        self, folder, cloud, app_client
    ):
        secret = f"My code is {planted()}."
        thread = new_thread(app_client)
        ask(app_client, thread, "When is the rent due?")
        threads.set_summary(thread, secret, 1)
        memory_id = add_memory(secret, thread)

        def delete_when_planning(_path, body):
            if "stands alone" in system_of(body):
                memory.delete(memory_id)
                return plan_reply(body)
            return default_reply(_path, body)

        # The plan's own request was built first; only the answer is after the delete.
        cloud.reply = delete_when_planning
        ask(app_client, thread, "What about that?")
        answers = [r for r in cloud.requests if "search_result" in r.raw]
        assert secret not in answers[-1].raw


class TestRecaps:
    def test_they_expire_unless_pinned(self, library):
        memory_id = memories.add(
            {
                "kind": "recap",
                "text": "A chat about the lease.",
                "embedding": None,
                "sensitivity": states.STANDARD,
                "source_label": "A chat",
            }
        )
        later = memories.RECAP_SECONDS * 2 + 2_000_000_000
        memories.set_pinned(memory_id, True)
        assert memories.expire_recaps(later) == 0
        memories.set_pinned(memory_id, False)
        assert memories.expire_recaps(later) == 1


def test_the_routes_list_edit_and_delete(library, app_client):
    memory_id = add_memory("I prefer short answers")
    listed = app_client.get("/api/memories").json()
    assert listed[0]["text"] == "I prefer short answers"
    assert "embedding" not in listed[0]
    app_client.patch(f"/api/memories/{memory_id}", json={"text": "Keep it brief"})
    assert app_client.get("/api/memories").json()[0]["text"] == "Keep it brief"
    assert app_client.delete(f"/api/memories/{memory_id}").status_code == 200
    assert app_client.get("/api/memories").json() == []
    assert app_client.delete(f"/api/memories/{memory_id}").status_code == 409
    app_client.put("/api/memories/switch", json={"on": False})
    assert app_client.get("/api/memories/switch").json() == {"on": False}


class TestWhereAMemoryComesFrom:
    def test_a_private_chat_makes_an_updated_memory_private(self, library):
        memory_id = add_memory("I like tea.")
        thread = threads.create(None, "standard")["id"]
        threads.taint(thread)
        memories.edit(
            memory_id,
            "I like tea from my clinic.",
            None,
            {"source_thread_id": thread, "source_label": "Mine"},
        )
        row = memories.all_active()[0]
        assert row["sensitivity"] == states.PRIVATE
        assert row["source_thread_id"] == thread

    def test_a_chat_gone_private_during_extraction_makes_a_new_one_private(
        self, library
    ):
        thread = threads.create(None, "standard")["id"]
        threads.taint(thread)
        add_memory("I like tea.", thread)
        assert memories.all_active()[0]["sensitivity"] == states.PRIVATE

    def test_an_owner_only_edit_never_lifts_private(self, library):
        memory_id = add_memory("I like tea.")
        with database.write() as connection:
            connection.execute(
                database.memories.update()
                .where(database.memories.c.id == memory_id)
                .values(sensitivity=states.PRIVATE)
            )
        thread = threads.create(None, "standard")["id"]
        memories.edit(
            memory_id, "I like green tea.", None, {"source_thread_id": thread}
        )
        assert memories.all_active()[0]["sensitivity"] == states.PRIVATE

    def test_an_extracted_memory_names_its_message_and_a_rebuild_leaves_it_out(
        self, library, cloud
    ):
        text = f"My gate code is {planted()}"
        thread = thread_with_turn(text)
        cloud.reply = actions_reply(
            [{"op": "ADD", "kind": "fact", "text": text, "memory_id": None}]
        )
        extract(thread)
        row = memories.all_active()[0]
        source = threads.transcript(thread)[0]
        assert row["source_message_ids"] == [source["id"]]
        memory.delete(row["id"])
        assert memories.deleted_sources(thread) == {source["id"]}
        rows = harness._cleared_summary_turns(  # noqa: SLF001
            {**threads.get(thread), "summary_through": 9}, set()
        )
        assert not any(text in line for line in rows)

    def test_something_the_owner_quoted_isnt_theirs(self, library, cloud):
        phrase = "the tenant shall pay a deposit of two thousand dollars"
        thread = thread_with_turn(f'My lease says "{phrase}". Is that normal?')
        cloud.reply = actions_reply(
            [{"op": "ADD", "kind": "fact", "text": phrase, "memory_id": None}]
        )
        extract(thread)
        assert memories.all_active() == []

    @pytest.mark.parametrize("kind", ["fact", "recap"])
    def test_a_documents_words_arent_stored_however_they_got_there(
        self, folder, cloud, kind
    ):
        phrase = "The rent is due monthly"
        thread = thread_with_turn(f"{phrase}, right?")
        cloud.reply = actions_reply(
            [{"op": "ADD", "kind": kind, "text": phrase, "memory_id": None}]
        )
        extract(thread)
        assert memories.all_active() == []

    def test_a_recap_needs_the_owners_words_too(self, library, cloud):
        thread = thread_with_turn("When is the rent due?")
        cloud.reply = actions_reply(
            [
                {
                    "op": "ADD",
                    "kind": "recap",
                    "text": "Quarterly dividend tables were reviewed in detail",
                    "memory_id": None,
                }
            ]
        )
        extract(thread)
        assert memories.all_active() == []

    def test_a_model_can_only_name_a_memory_it_was_shown(self, library, cloud):
        private_id = add_memory("Hidden.")
        with database.write() as connection:
            connection.execute(
                database.memories.update()
                .where(database.memories.c.id == private_id)
                .values(sensitivity=states.PRIVATE)
            )
        thread = thread_with_turn("Please forget Hidden.")
        cloud.reply = actions_reply(
            [{"op": "DELETE", "kind": "fact", "text": "", "memory_id": private_id}]
        )
        extract(thread)
        assert memories.is_active(private_id)

    def test_extraction_waits_at_the_cap(self, library, cloud, monkeypatch):
        thread = thread_with_turn("My gate code is 4471.")
        monkeypatch.setattr(spend, "over_cap", lambda: True)
        with pytest.raises(models.CloudRefused):
            extract(thread)
        assert cloud.requests == []
        assert threads.get(thread)["memory_through"] == 0


class TestExtractionsOwnRequest:
    def test_a_memory_deleted_as_it_leaves_isnt_listed(
        self, library, cloud, monkeypatch
    ):
        secret = f"My code is {planted()}"
        memory_id = add_memory(secret)
        thread = thread_with_turn("I like tea.")
        log = models.api.log_background_attempt

        def deleted_first(*given, **named):
            if memories.is_active(memory_id):
                memory.delete(memory_id)
            return log(*given, **named)

        monkeypatch.setattr(models.api, "log_background_attempt", deleted_first)
        cloud.reply = actions_reply(
            [{"op": "NOOP", "kind": "fact", "text": "", "memory_id": None}]
        )
        extract(thread)
        assert secret not in cloud.all_text()

    def test_a_recap_names_every_message_it_drew_on(self, library, cloud):
        thread = thread_with_turn("Moving to Denver in spring.")
        for run in [threads.active_run(thread)]:
            if run:
                threads.complete_run(run["id"])
        threads.complete_run(
            threads.start_run(thread, "Buying a small house there.", None)
        )
        cloud.reply = actions_reply(
            [
                {
                    "op": "ADD",
                    "kind": "recap",
                    "text": "Moving to Denver, buying a small house.",
                    "memory_id": None,
                }
            ]
        )
        extract(thread)
        turns = [t["id"] for t in threads.transcript(thread) if t["role"] == "user"]
        assert sorted(memories.all_active()[0]["source_message_ids"]) == sorted(turns)

    def test_a_short_quote_in_backticks_isnt_the_owners(self, library, cloud):
        thread = thread_with_turn("The manual says `Reset weekly` here.")
        cloud.reply = actions_reply(
            [{"op": "ADD", "kind": "fact", "text": "Reset weekly", "memory_id": None}]
        )
        extract(thread)
        assert memories.all_active() == []

    def test_the_cap_is_decided_with_the_record(self, library, cloud, monkeypatch):
        settings.put(settings.SPEND_CAP, 1.0)
        models.api.log_attempt(
            "anthropic:claude-haiku-5-5",
            "memory",
            [],
            models.api.Quote((0.1, 0.5), 1.0),
        )
        monkeypatch.setattr(spend, "over_cap", lambda: False)
        thread = thread_with_turn("My gate code is 4471.")
        with pytest.raises(models.CloudRefused):
            extract(thread)
        assert cloud.requests == []

    def test_a_quote_reworded_isnt_the_owners_either(self, library, cloud):
        thread = thread_with_turn(
            'My lease says "The tenant shall pay rent of two thousand dollars." '
            "What is my monthly rent?"
        )
        fact = "My monthly rent is two thousand dollars"
        cloud.reply = actions_reply(
            [{"op": "ADD", "kind": "fact", "text": fact, "memory_id": None}]
        )
        extract(thread)
        assert memories.all_active() == []
