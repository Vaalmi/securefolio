"""What changes while a request is being built: the policy is asked again as it leaves,
and nothing out of date goes with it."""

from typing import Any

import pytest
import sqlalchemy as sa

from agent import harness, memory, models
from agent.tests.test_ask import (
    ask,
    default_reply,
    last_answer,
    message,
    new_thread,
    plan_reply,
    system_of,
    write_card,
)
from backend import database, keychain, memories, policy, sources, states, threads
from backend.build_mode import STUB_ANTHROPIC_ENV
from testing.queue import drain
from testing.stub_models import stub_anthropic


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


def as_the_client_is_built(monkeypatch, happen) -> None:
    """Runs `happen` once, after policy was first asked and before anything is sent. In
    the attempt's own thread: a write on the loop would wait on the checkpointer."""
    log = models.api.log_attempt
    done = []

    def logging(*given, **named):
        if not done:
            done.append(True)
            happen()
        return log(*given, **named)

    monkeypatch.setattr(models.api, "log_attempt", logging)


def add_memory(text: str) -> str:
    return memories.add(
        {
            "kind": "fact",
            "text": text,
            "embedding": None,
            "sensitivity": states.STANDARD,
            "source_thread_id": None,
            "source_label": "A chat",
        }
    )


def recall_it(monkeypatch, memory_id: str, text: str) -> None:
    monkeypatch.setattr(
        memory,
        "recall",
        lambda *_given: [
            {"id": memory_id, "text": text, "sensitivity": states.STANDARD}
        ],
    )


def test_a_memory_deleted_as_the_client_is_built_isnt_sent(
    folder, cloud, app_client, monkeypatch
):
    text = "My locker code is zq4471."
    memory_id = add_memory(text)
    recall_it(monkeypatch, memory_id, text)
    as_the_client_is_built(monkeypatch, lambda: memory.delete(memory_id))
    thread = new_thread(app_client)
    found = ask(app_client, thread, "When is the rent due?")
    assert last_answer(found)["kind"] == "answer"
    assert text not in cloud.all_text()


def test_a_memory_gone_private_as_the_client_is_built_isnt_sent(
    folder, cloud, app_client, monkeypatch
):
    text = "My locker code is zq4472."
    memory_id = add_memory(text)
    recall_it(monkeypatch, memory_id, text)

    def goes_private() -> None:
        with database.write() as connection:
            connection.execute(
                database.memories.update()
                .where(database.memories.c.id == memory_id)
                .values(sensitivity=states.PRIVATE)
            )

    as_the_client_is_built(monkeypatch, goes_private)
    thread = new_thread(app_client)
    ask(app_client, thread, "When is the rent due?")
    assert text not in cloud.all_text()


def test_a_near_miss_gone_private_as_it_leaves_isnt_sent(folder):
    with database.read() as connection:
        document_id = connection.execute(
            sa.select(database.documents.c.id)
        ).scalar_one()
    state: Any = {
        "question": "Anything on leases?",
        "mode": "standard",
        "near_miss": [{"id": document_id, "title": "Lease zq4473"}],
    }
    thread = {"summary": None, "summary_version": 0}
    parts = harness._plan_parts(state, thread, [])  # noqa: SLF001
    assert "Lease zq4473" in [p.text for p in parts]
    with database.write() as connection:
        connection.execute(
            database.documents.update()
            .where(database.documents.c.id == document_id)
            .values(sensitivity=states.PRIVATE)
        )
    kept = models.current_parts(parts, None)
    assert "Lease zq4473" not in [p.text for p in kept]


def test_a_total_is_worked_out_again_before_it_leaves(
    library, cloud, app_client, tmp_path, monkeypatch
):
    root = tmp_path / "ins"
    root.mkdir()
    for name in ("Auto", "Renters"):
        (root / f"{name} insurance.txt").write_text(f"{name} insurance policy.\n")
    sources.add_source(str(root), states.STANDARD)
    drain()
    write_card("Auto insurance", ["$1,000.00"])
    write_card("Renters insurance", ["$200.00"])

    def turn_one_private() -> None:
        with database.write() as connection:
            connection.execute(
                database.documents.update()
                .where(database.documents.c.title == "Renters insurance")
                .values(sensitivity=states.PRIVATE)
            )

    retrieve = harness._retrieve  # noqa: SLF001

    def then_private(state, thread):
        found = retrieve(state, thread)
        turn_one_private()
        return found

    monkeypatch.setattr(harness, "_retrieve", then_private)

    def reply(_path, body):
        if "stands alone" in system_of(body):
            return plan_reply(body, "insurance", "list", keywords=["insurance"])
        return message(body, [{"type": "text", "text": "One policy."}])

    cloud.reply = reply
    thread = new_thread(app_client)
    answer = last_answer(ask(app_client, thread, "What did I spend on insurance?"))
    assert answer["table"]["total"] == 1000.0
    assert "1,200.00" not in cloud.all_text()


def test_a_total_naming_a_private_document_is_refused(folder):
    with database.write() as connection:
        connection.execute(
            database.documents.update().values(sensitivity=states.PRIVATE)
        )
    with database.read() as connection:
        document_id = connection.execute(
            sa.select(database.documents.c.id)
        ).scalar_one()
    total = models.api.Part(
        models.api.PartKind.TOTAL, "A total", document_ids=[document_id]
    )
    assert models.api.refusal([total], None) == policy.PRIVATE_PART


def test_a_pending_card_gone_private_stops_a_total(library, cloud, monkeypatch):
    table = {
        "rows": [],
        "pending": [{"document_id": "d9"}],
        "total": 0,
        "wants_total": 0,
    }
    part = models.api.Part(
        models.api.PartKind.TOTAL,
        harness._result_line(table),  # noqa: SLF001
        document_ids=[r["document_id"] for r in [*table["rows"], *table["pending"]]],
    )
    monkeypatch.setattr(policy, "_any_private", lambda ids: ids == ["d9"])
    assert models.api.refusal([part], None) == policy.PRIVATE_PART


def test_a_source_gone_private_as_the_answer_is_written_taints_the_chat(
    folder, cloud, app_client, monkeypatch
):
    def goes_private(*_given):
        with database.write() as connection:
            connection.execute(
                database.documents.update().values(sensitivity=states.PRIVATE)
            )

    log = models.api.log_sent

    def sent_then_private(*given):
        made = log(*given)
        goes_private()
        return made

    monkeypatch.setattr(models.api, "log_sent", sent_then_private)
    thread = new_thread(app_client)
    ask(app_client, thread, "When is the rent due?")
    found = threads.get(thread)
    assert found["private_tainted"] is True
    assert all(
        turn["sensitivity"] == states.PRIVATE for turn in threads.transcript(thread)
    )


def test_a_private_chats_sentence_never_reaches_the_judge(library, cloud):
    """Even citing only a Standard passage: it came from a Private chat."""
    thread = threads.create(None, "standard")["id"]
    threads.taint(thread)
    cited = models.Cited("Note", "Words.", "Words.", "d1", states.STANDARD)
    with pytest.raises(models.CloudRefused):
        models.judge_citation("It came from a Private chat.", cited, [], thread)
    with pytest.raises(models.CloudRefused):
        models.judge_sentence("It came from a Private chat.", [cited], thread)
    assert cloud.requests == []
